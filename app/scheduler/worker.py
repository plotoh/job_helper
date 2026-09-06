import asyncio
import logging
import signal
from datetime import datetime, timedelta, time
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.db import Account, AsyncSessionLocal, init_db
from app.hh_client.browser_manager import BrowserManager
from app.scheduler.account_worker import AccountWorker
from app.scheduler.parsing_scheduler import ParsingScheduler
from app.scheduler.daily_planner import generate_daily_limit, generate_pauses
from app.db.repositories.account_state_repo import AccountStateRepository
from app.db.repositories.vacancy_repo import VacancyRepository
from app.db.repositories.response_error_repo import ResponseErrorRepository
from app.utils.time_utils import msk_now, MSK_TZ, msk_today

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL))
logger = logging.getLogger(__name__)


class Worker:
    def __init__(self):
        self.browser_manager = BrowserManager(headless=settings.PLAYWRIGHT_HEADLESS)
        self.parsing_context = None
        self.account_workers = []
        self.worker_tasks = []
        self.parsing_scheduler = None
        self.parsing_task = None
        self.daily_planner_task = None
        self.error_report_task = None
        self.stop_event = asyncio.Event()
        self.template = self._load_template()

    def _load_template(self) -> str:
        template_path = Path(settings.MESSAGE_TEMPLATE_FILE)
        if not template_path.exists():
            raise FileNotFoundError(f"Template file {template_path} not found")
        return template_path.read_text(encoding='utf-8')

    async def start(self):
        logger.info("Starting worker")
        await init_db()
        await self.browser_manager.start()

        # Подготовка прокси для парсинга (если задан в .env)
        parsing_proxy = None
        if settings.PROXY_SERVER:
            parsing_proxy = {"server": settings.PROXY_SERVER}
            if settings.PROXY_USERNAME:
                parsing_proxy["username"] = settings.PROXY_USERNAME
            if settings.PROXY_PASSWORD:
                parsing_proxy["password"] = settings.PROXY_PASSWORD
            logger.info(f"Parsing will use proxy {settings.PROXY_SERVER}")

        parsing_cookies = getattr(settings, 'PARSING_COOKIES_FILE', None)
        self.parsing_context = await self.browser_manager.create_context(
            cookies_source=parsing_cookies,
            proxy=parsing_proxy
        )

        # Запускаем парсинг
        self.parsing_scheduler = ParsingScheduler(self.parsing_context)
        self.parsing_task = asyncio.create_task(self.parsing_scheduler.run())

        # Загружаем аккаунты
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Account))
            accounts = result.scalars().all()

        if settings.RESPONSES_ENABLED:
            logger.info("Responses are ENABLED. Starting account workers.")
            for acc in accounts:
                cookies_file = Path(settings.COOKIES_DIR) / f"{acc.tg_username}.txt"
                if not cookies_file.exists():
                    logger.warning(f"Cookies file for account {acc.id} not found at {cookies_file}, skipping")
                    continue

                # Прокси для аккаунта – используем глобальный из .env
                proxy = None
                if settings.PROXY_SERVER:
                    proxy = {"server": settings.PROXY_SERVER}
                    if settings.PROXY_USERNAME:
                        proxy["username"] = settings.PROXY_USERNAME
                    if settings.PROXY_PASSWORD:
                        proxy["password"] = settings.PROXY_PASSWORD
                    logger.info(f"Account {acc.id} will use proxy {settings.PROXY_SERVER}")

                context = await self.browser_manager.create_context(
                    cookies_source=str(cookies_file),
                    proxy=proxy
                )
                worker = AccountWorker(
                    account_id=acc.id,
                    resume_id=acc.resume_id,
                    browser_context=context,
                    template=self.template
                )
                self.account_workers.append(worker)
                task = asyncio.create_task(worker.run())
                self.worker_tasks.append(task)
        else:
            logger.info("Responses are DISABLED. No account workers will be started.")

        # Запускаем ежедневный планировщик (лимиты, очистка)
        self.daily_planner_task = asyncio.create_task(self._daily_planner_loop())

        # in /bot # Запускаем отправку ошибок в заданное время
        # self.error_report_task = asyncio.create_task(self._error_report_loop())

        await self.stop_event.wait()
        await self.stop()

    async def _daily_planner_loop(self):
        try:
            while not self.stop_event.is_set():
                now = msk_now()
                next_run = datetime.combine(msk_today() + timedelta(days=1), time(0, 5)).replace(tzinfo=MSK_TZ)
                sleep_seconds = (next_run - now).total_seconds()
                if sleep_seconds > 0:
                    await self._interruptible_sleep(sleep_seconds)

                logger.info("Generating daily limits and pauses for all accounts")
                async with AsyncSessionLocal() as session:
                    accounts = (await session.execute(select(Account))).scalars().all()
                    today = now.date()
                    state_repo = AccountStateRepository(session)
                    for acc in accounts:
                        existing = await state_repo.get_for_today(acc.id, today)
                        if existing:
                            continue
                        limit = generate_daily_limit()
                        pauses = generate_pauses()
                        data = {
                            'daily_limit': limit,
                            'pauses': pauses,
                            'done_count': 0,
                            'status': 'pending'
                        }
                        await state_repo.create_or_update(acc.id, today, data)
                        logger.info(f"Account {acc.id}: limit={limit}, pauses={pauses}")

                # Очистка устаревших вакансий (раз в сутки)
                await self._cleanup_old_vacancies()
        except asyncio.CancelledError:
            logger.info("Daily planner loop cancelled")
            raise

    async def _cleanup_old_vacancies(self):
        logger.info("Cleaning up old vacancies...")
        async with AsyncSessionLocal() as session:
            repo = VacancyRepository(session)
            await repo.deactivate_old_vacancies(settings.VACANCY_TTL_DAYS)
        logger.info("Cleanup completed.")

    # закоментированно т.к. функционал теперь внутри бота, а не воркера
    # async def _error_report_loop(self):
    #     """Цикл отправки ежедневных отчётов об ошибках в заданное время."""
    #     try:
    #         while not self.stop_event.is_set():
    #             now = msk_now()
    #             target = now.replace(
    #                 hour=settings.ERROR_REPORT_HOUR,
    #                 minute=settings.ERROR_REPORT_MINUTE,
    #                 second=0, microsecond=0
    #             )
    #             if now >= target:
    #                 target += timedelta(days=1)
    #             sleep_seconds = (target - now).total_seconds()
    #             logger.info(f"Next error report at {target}")
    #             await self._interruptible_sleep(sleep_seconds)
    #             await self._send_error_reports()
    #     except asyncio.CancelledError:
    #         logger.info("Error report loop cancelled")
    #
    # async def _send_error_reports(self):
    #     logger.info("Sending daily error reports...")
    #     async with AsyncSessionLocal() as session:
    #         accounts = (await session.execute(select(Account))).scalars().all()
    #         for acc in accounts:
    #             repo = ResponseErrorRepository(session)
    #             errors = await repo.get_unreported_errors(acc.id)
    #             if not errors:
    #                 continue
    #             # Отправляем через модуль bot.sender
    #             await send_error_report(acc.id, errors, acc.tg_username)
    #             # Помечаем как отправленные
    #             ids = [e["id"] for e in errors]
    #             await repo.mark_as_reported(ids)
    #             await asyncio.sleep(1)

    async def _interruptible_sleep(self, seconds: float):
        end_time = asyncio.get_event_loop().time() + seconds
        while not self.stop_event.is_set():
            remaining = end_time - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(remaining, 10))

    async def stop(self):
        logger.info("Stopping worker")
        self.stop_event.set()

        for w in self.account_workers:
            await w.stop()

        if self.parsing_scheduler:
            await self.parsing_scheduler.stop()

        tasks = []
        if self.parsing_task:
            tasks.append(self.parsing_task)
        if self.daily_planner_task:
            tasks.append(self.daily_planner_task)
        # if self.error_report_task:
        #     tasks.append(self.error_report_task)
        tasks.extend(self.worker_tasks)

        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)

        await self.browser_manager.stop()
        logger.info("Worker stopped")


async def main():
    worker = Worker()
    loop = asyncio.get_running_loop()

    def shutdown():
        asyncio.create_task(worker.stop())

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown)

    await worker.start()


if __name__ == "__main__":
    asyncio.run(main())
