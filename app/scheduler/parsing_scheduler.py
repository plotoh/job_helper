import asyncio
import logging
import random
from datetime import datetime, time, timedelta
from typing import List, Tuple

from playwright.async_api import BrowserContext
from sqlalchemy import select, and_

from app.config import settings
from app.db.database import AsyncSessionLocal
from app.db.models import ParsingLog
from app.db.repositories.vacancy_repo import VacancyRepository

from app.hh_client.detail_parser import HHVacancyDetailParser
from app.hh_client.list_parser import HHVacancyListParser
from app.hh_client.vacancy_filter import VacancyFilter

logger = logging.getLogger(__name__)

# Окна парсинга: (start_hour, start_min, end_hour, end_min)
PARSING_WINDOWS = [
    (10, 0, 12, 0),
    (14, 0, 15, 0),
    (18, 0, 20, 0),
]


class ParsingScheduler:
    def __init__(self, browser_context: BrowserContext):
        self.context = browser_context
        self.list_parser = HHVacancyListParser()
        self.detail_parser = HHVacancyDetailParser()
        self.filter_service = VacancyFilter()
        self.stop_event = asyncio.Event()

    async def run(self):
        while not self.stop_event.is_set():
            now = datetime.now()
            for idx, (sh, sm, eh, em) in enumerate(PARSING_WINDOWS):
                window_start = time(sh, sm)
                window_end = time(eh, em)
                if now.time() > window_end:
                    continue

                async with AsyncSessionLocal() as session:
                    # Ищем любую запись за сегодня
                    stmt = select(ParsingLog).where(
                        and_(
                            ParsingLog.window_index == idx,
                            ParsingLog.started_at >= datetime.combine(now.date(), time(0, 0))
                        )
                    ).order_by(ParsingLog.started_at.desc())
                    result = await session.execute(stmt)
                    existing = result.scalars().first()

                    if existing:
                        # Если висит в статусе started более 30 минут – удаляем и разрешаем новый запуск
                        if existing.status == 'started' and (now - existing.started_at).total_seconds() > 1800:
                            logger.warning(f"Window {idx} stuck as 'started' for >30min, removing and re-running")
                            await session.delete(existing)
                            await session.commit()
                        else:
                            logger.info(f"Window {idx} already has a log entry today ({existing.status}), skipping")
                            continue

                start_dt = datetime.combine(now.date(), window_start)
                if now < start_dt:
                    max_delay = (datetime.combine(now.date(), window_end) - start_dt).total_seconds()
                    delay = random.uniform(0, max_delay)
                    run_at = start_dt + timedelta(seconds=delay)
                else:
                    # Внутри окна: оставляем минимум 5 минут на выполнение
                    end_dt = datetime.combine(now.date(), window_end)
                    if (end_dt - now).total_seconds() < 300:
                        logger.info(f"Window {idx} ends soon (<5 min), skipping")
                        continue
                    max_delay = (end_dt - now).total_seconds() - 300
                    delay = random.uniform(0, max_delay)
                    run_at = now + timedelta(seconds=delay)

                wait_seconds = (run_at - now).total_seconds()
                if wait_seconds > 0:
                    logger.info(f"Parsing window {idx} scheduled at {run_at}")
                    await asyncio.sleep(wait_seconds)

                await self._run_parsing(idx)

    async def _run_parsing(self, window_idx: int):
        """Выполнить парсинг вакансий."""
        logger.info(f"Starting parsing for window {window_idx}")
        start_time = datetime.now()
        log_entry = ParsingLog(
            window_index=window_idx,
            started_at=start_time,
            status='started'
        )
        async with AsyncSessionLocal() as session:
            session.add(log_entry)
            await session.commit()
            await session.refresh(log_entry)

        try:
            # Сбор ID с поисковой страницы
            vacancies_list = await self.list_parser.fetch_ids(
                settings.HH_SEARCH_URL, settings.MAX_PAGES, self.context
            )
            logger.info(f"Collected {len(vacancies_list)} raw vacancies")

            # Фильтрация
            suitable = [v for v in vacancies_list if self.filter_service.is_match(v['title'])]
            logger.info(f"After filter: {len(suitable)} suitable")

            # Детальный парсинг с ограничением конкурентности
            semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT)

            async def parse_one(vac):
                async with semaphore:
                    try:
                        details = await self.detail_parser.parse(vac['url'], self.context)
                        return {**vac, **details}
                    except Exception as e:
                        logger.error(f"Failed to parse {vac['id']}: {e}")
                        return None

            tasks = [parse_one(v) for v in suitable]
            results = await asyncio.gather(*tasks)
            successful = [r for r in results if r is not None]

            # Сохраняем в БД
            async with AsyncSessionLocal() as session:
                repo = VacancyRepository(session)
                saved_count = 0
                for vac in successful:
                    inserted = await repo.save_if_not_exists(vac)
                    if inserted:
                        saved_count += 1
                logger.info(f"Saved {saved_count} new vacancies")

            # Обновляем лог
            async with AsyncSessionLocal() as session:
                log_entry.finished_at = datetime.now()
                log_entry.vacancies_found = saved_count
                log_entry.status = 'success'
                await session.commit()

        except Exception as e:
            logger.exception(f"Parsing failed for window {window_idx}")
            async with AsyncSessionLocal() as session:
                log_entry.finished_at = datetime.now()
                log_entry.status = 'failed'
                log_entry.error = str(e)
                await session.commit()

    async def stop(self):
        self.stop_event.set()