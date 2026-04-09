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
from app.utils.time_utils import msk_now, MSK_TZ, msk_today

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL))
logger = logging.getLogger(__name__)


class Worker:
    def __init__(self):
        self.browser_manager = BrowserManager(headless=settings.PLAYWRIGHT_HEADLESS)
        self.parsing_context = None
        self.account_workers = []
        self.parsing_scheduler = None
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

        # Создаём контекст для парсинга (всегда нужен)
        parsing_cookies = getattr(settings, 'PARSING_COOKIES_FILE', None)
        self.parsing_context = await self.browser_manager.create_context(parsing_cookies)

        # Запускаем планировщик парсинга (всегда)
        self.parsing_scheduler = ParsingScheduler(self.parsing_context)
        asyncio.create_task(self.parsing_scheduler.run())

        # Загружаем список аккаунтов из БД
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Account))
            accounts = result.scalars().all()

        # Создаём воркеры для откликов, только если они включены
        if settings.RESPONSES_ENABLED:
            logger.info("Responses are ENABLED. Starting account workers.")
            for acc in accounts:
                cookies_file = Path(settings.COOKIES_DIR) / f"{acc.tg_username}.txt"
                if not cookies_file.exists():
                    logger.warning(f"Cookies file for account {acc.id} not found at {cookies_file}, skipping")
                    continue
                context = await self.browser_manager.create_context(str(cookies_file))
                worker = AccountWorker(
                    account_id=acc.id,
                    resume_id=acc.resume_id,
                    browser_context=context,
                    template=self.template
                )
                self.account_workers.append(worker)
                asyncio.create_task(worker.run())
        else:
            logger.info("Responses are DISABLED. No account workers will be started.")

        # Запускаем задачу ежедневного планирования лимитов (нужна для статистики)
        asyncio.create_task(self._daily_planner_loop())

        # Ожидаем сигнала остановки
        await self.stop_event.wait()

    async def _daily_planner_loop(self):
        while not self.stop_event.is_set():
            now = msk_now()
            next_run = datetime.combine(msk_today() + timedelta(days=1), time(0, 5)).replace(tzinfo=MSK_TZ)
            sleep_seconds = (next_run - now).total_seconds()
            if sleep_seconds > 0:
                await asyncio.sleep(sleep_seconds)

            logger.info("Generating daily limits and pauses for all accounts")
            async with AsyncSessionLocal() as session:
                accounts = (await session.execute(select(Account))).scalars().all()
                today = now.date()
                state_repo = AccountStateRepository(session)
                for acc in accounts:
                    # Проверяем, есть ли уже запись на сегодня
                    existing = await state_repo.get_for_today(acc.id, today)
                    if existing:
                        logger.debug(f"Account {acc.id} already has state for today, skipping")
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

    async def stop(self):
        logger.info("Stopping worker")
        self.stop_event.set()
        for w in self.account_workers:
            await w.stop()
        if self.parsing_scheduler:
            await self.parsing_scheduler.stop()
        await self.browser_manager.stop()


async def main():
    worker = Worker()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(worker.stop()))
    await worker.start()


if __name__ == "__main__":
    asyncio.run(main())
