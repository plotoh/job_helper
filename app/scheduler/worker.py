import asyncio
import logging
import signal
from datetime import datetime, timedelta, time
from pathlib import Path
from logging.handlers import RotatingFileHandler

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
from app.utils.settings_utils import get_setting, set_setting
from app.utils.time_utils import msk_now, MSK_TZ, msk_today


def setup_logging():
    """Настраивает логирование: stdout + файл logs/worker.log с ротацией."""
    log_level = getattr(logging, settings.LOG_LEVEL, logging.INFO)
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

    root_logger = logging.getLogger()
    # Удаляем старые хендлеры, чтобы не дублировать
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Консольный хендлер
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(logging.Formatter(log_format))
    root_logger.addHandler(console_handler)

    # Файловый хендлер с ротацией
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    file_handler = RotatingFileHandler(
        "logs/worker.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(logging.Formatter(log_format))
    root_logger.addHandler(file_handler)

    root_logger.setLevel(log_level)
    logger = logging.getLogger(__name__)
    logger.info("Логирование настроено: stdout + logs/worker.log (ротация 10MB, 5 файлов)")


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

    async def _check_force_parsing(self):
        logger = logging.getLogger(__name__)
        while not self.stop_event.is_set():
            await asyncio.sleep(10)
            force = await get_setting('force_parsing', 'false')
            if force == 'true':
                logger.info("🚀 Принудительный запуск парсинга по запросу из веб-интерфейса")
                await self.parsing_scheduler.force_parse(pages=3)  # или сколько угодно
                await set_setting('force_parsing', 'false')
                logger.info("✅ Принудительный парсинг завершён, флаг сброшен")

    async def start(self):
        logger = logging.getLogger(__name__)
        logger.info("Starting worker")
        await init_db()
        await self.browser_manager.start()

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

        self.parsing_scheduler = ParsingScheduler(self.parsing_context)
        self.parsing_task = asyncio.create_task(self.parsing_scheduler.run())

        self.force_parsing_task = asyncio.create_task(self._check_force_parsing())

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

        self.daily_planner_task = asyncio.create_task(self._daily_planner_loop())

        await self.stop_event.wait()
        await self.stop()

    async def _daily_planner_loop(self):
        logger = logging.getLogger(__name__)
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

                await self._cleanup_old_vacancies()
        except asyncio.CancelledError:
            logger.info("Daily planner loop cancelled")
            raise

    async def _cleanup_old_vacancies(self):
        logger = logging.getLogger(__name__)
        logger.info("Cleaning up old vacancies...")
        async with AsyncSessionLocal() as session:
            repo = VacancyRepository(session)
            await repo.deactivate_old_vacancies(settings.VACANCY_TTL_DAYS)
        logger.info("Cleanup completed.")

    async def _interruptible_sleep(self, seconds: float):
        end_time = asyncio.get_event_loop().time() + seconds
        while not self.stop_event.is_set():
            remaining = end_time - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(remaining, 10))

    async def stop(self):
        logger = logging.getLogger(__name__)
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
        tasks.extend(self.worker_tasks)

        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)

        await self.browser_manager.stop()
        logger.info("Worker stopped")


async def main():
    setup_logging()  # <-- Инициализируем логирование при запуске
    worker = Worker()
    loop = asyncio.get_running_loop()

    def shutdown():
        asyncio.create_task(worker.stop())

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown)

    await worker.start()


if __name__ == "__main__":
    asyncio.run(main())
