import asyncio
import logging
import random
from datetime import datetime, time, timedelta
from typing import List, Tuple

from playwright.async_api import BrowserContext
from sqlalchemy import select, and_

from app.config import settings
from app.db import ParsingProgressRepository, AsyncSessionLocal, ParsingLog, VacancyRepository, FilteredOutVacancy
from app.hh_client.detail_parser import HHVacancyDetailParser
from app.hh_client.list_parser import HHVacancyListParser
from app.hh_client.vacancy_filter import VacancyFilter
from app.utils.time_utils import msk_now, MSK_TZ

logger = logging.getLogger(__name__)

PARSING_WINDOWS = [
    (9, 0, 10, 0),
    (11, 0, 11, 30),
    (12, 0, 12, 30),
    (13, 0, 14, 0),
    (15, 0, 17, 0),
    (18, 0, 20, 0),
    (21, 0, 23, 0),
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
            now = msk_now()  # offset-aware
            for idx, (sh, sm, eh, em) in enumerate(PARSING_WINDOWS):
                window_start = time(sh, sm)
                window_end = time(eh, em)
                if now.time() > window_end:
                    continue

                async with AsyncSessionLocal() as session:
                    stmt = select(ParsingLog).where(
                        and_(
                            ParsingLog.window_index == idx,
                            ParsingLog.started_at >= datetime.combine(now.date(), time(0, 0))
                        )
                    ).order_by(ParsingLog.started_at.desc())
                    result = await session.execute(stmt)
                    existing = result.scalars().first()

                    if existing:
                        if existing.status == 'started' and (now - existing.started_at).total_seconds() > 1800:
                            logger.warning(f"Window {idx} stuck as 'started' for >30min, removing and re-running")
                            await session.delete(existing)
                            await session.commit()
                        else:
                            logger.info(f"Window {idx} already has a log entry today ({existing.status}), skipping")
                            continue

                # Создаём start_dt и end_dt с часовым поясом MSK
                start_dt = datetime.combine(now.date(), window_start).replace(tzinfo=MSK_TZ)
                end_dt = datetime.combine(now.date(), window_end).replace(tzinfo=MSK_TZ)

                if now < start_dt:
                    max_delay = (end_dt - start_dt).total_seconds()
                    delay = random.uniform(0, max_delay)
                    run_at = start_dt + timedelta(seconds=delay)
                else:
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

            # После проверки всех окон ждём до 00:05 следующего дня
            tomorrow = now.date() + timedelta(days=1)
            next_check = datetime.combine(tomorrow, time(0, 5)).replace(tzinfo=MSK_TZ)
            sleep_seconds = (next_check - now).total_seconds()
            if sleep_seconds > 0:
                logger.info(f"Parsing scheduler: next check at {next_check}")
                await asyncio.sleep(sleep_seconds)

    async def _run_parsing(self, window_idx: int):
        logger.info(f"Starting parsing for window {window_idx}")
        start_time = msk_now()
        start_time_naive = start_time.replace(tzinfo=None)

        # Создаём запись в parsing_log
        async with AsyncSessionLocal() as session:
            log_entry = ParsingLog(
                window_index=window_idx,
                started_at=start_time_naive,
                status='started'
            )
            session.add(log_entry)
            await session.commit()
            await session.refresh(log_entry)
            log_id = log_entry.id

        total_saved = 0
        pages_processed = 0
        try:
            # Получаем прогресс
            async with AsyncSessionLocal() as session:
                progress_repo = ParsingProgressRepository(session)
                progress = await progress_repo.get_or_create(settings.HH_SEARCH_URL)

            pages_to_do = settings.PAGES_PER_WINDOW  # например, 3

            for i in range(1, pages_to_do + 1):
                next_page = progress.last_page + i
                logger.info(f"Parsing page {next_page} for window {window_idx}")

                # Получаем список вакансий со страницы
                vacancies_list = await self.list_parser.fetch_ids(
                    settings.HH_SEARCH_URL, next_page, self.context
                )
                if not vacancies_list:
                    logger.info(f"No vacancies found on page {next_page}, stopping further pages.")
                    break

                # Фильтрация
                suitable = []
                filtered_out = []
                for vac in vacancies_list:
                    if self.filter_service.is_match(vac['title']):
                        suitable.append(vac)
                    else:
                        filtered_out.append(vac)
                        logger.debug(f"Filtered out: {vac['id']} - {vac['title']}")

                logger.info(f"After filter on page {next_page}: {len(suitable)} suitable")

                # Сохраняем отфильтрованные вакансии (опционально)
                if filtered_out:
                    async with AsyncSessionLocal() as session:
                        from sqlalchemy.dialects.postgresql import insert as pg_insert
                        from app.db.models import FilteredOutVacancy
                        for vac in filtered_out:
                            stmt = pg_insert(FilteredOutVacancy).values(
                                hh_id=vac['id'],
                                title=vac['title'],
                                url=vac['url'],
                                filter_reason='filtered out by criteria'
                            ).on_conflict_do_nothing(index_elements=['hh_id'])
                            await session.execute(stmt)
                        await session.commit()
                    logger.info(f"Saved {len(filtered_out)} filtered-out vacancies")

                # Детальный парсинг с ограничением конкурентности
                semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT)

                async def parse_one(vac):
                    async with semaphore:
                        logger.info(f"🔍 Парсинг вакансии {vac['id']} - {vac['title']}")
                        try:
                            details = await self.detail_parser.parse(vac['url'], self.context)
                            logger.info(
                                f"✅ Успешно распаршено {vac['id']}: зарплата={details.get('salary')}, опыт={details.get('experience')}, навыков={len(details.get('skills', []))}")
                            return {**vac, **details}
                        except Exception as e:
                            logger.error(f"❌ Ошибка парсинга {vac['id']}: {e}", exc_info=True)
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
                    logger.info(f"Page {next_page}: saved {saved_count} new vacancies")
                    total_saved += saved_count

                # Обновляем прогресс после успешной обработки страницы (даже если 0 новых)
                async with AsyncSessionLocal() as session:
                    progress_repo = ParsingProgressRepository(session)
                    await progress_repo.update_last_page(settings.HH_SEARCH_URL, next_page)
                    pages_processed += 1

            # Всё окно выполнено успешно
            async with AsyncSessionLocal() as session:
                # Получаем запись лога заново
                stmt = select(ParsingLog).where(ParsingLog.id == log_id)
                result = await session.execute(stmt)
                log_entry_db = result.scalar_one()
                log_entry_db.finished_at = msk_now().replace(tzinfo=None)
                log_entry_db.vacancies_found = total_saved
                log_entry_db.status = 'success'
                await session.commit()
            logger.info(f"Window {window_idx} completed. Total saved: {total_saved}")

        except Exception as e:
            logger.exception(f"Parsing failed for window {window_idx}")
            async with AsyncSessionLocal() as session:
                stmt = select(ParsingLog).where(ParsingLog.id == log_id)
                result = await session.execute(stmt)
                log_entry_db = result.scalar_one()
                log_entry_db.finished_at = msk_now().replace(tzinfo=None)
                log_entry_db.status = 'failed'
                log_entry_db.error = str(e)
                await session.commit()
            # Не обновляем прогресс при ошибке

    async def stop(self):
        self.stop_event.set()
