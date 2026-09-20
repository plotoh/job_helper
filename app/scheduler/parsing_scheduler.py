import asyncio
import logging
import random
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import List

from playwright.async_api import BrowserContext
from sqlalchemy import select, and_, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.db import (
    ParsingProgressRepository,
    AsyncSessionLocal,
    ParsingLog,
    VacancyRepository,
    FilteredOutVacancy,
)
from app.hh_client.detail_parser import HHVacancyDetailParser
from app.hh_client.list_parser import HHVacancyListParser
from app.hh_client.vacancy_filter import VacancyFilter
from app.scoring.scorer import VacancyScorer
from app.utils.time_utils import msk_now, MSK_TZ

logger = logging.getLogger(__name__)

PARSING_WINDOWS = [
    (9, 0, 10, 0),
    (12, 30, 13, 00),
    (13, 0, 14, 0),
    (15, 0, 16, 0),
    (21, 0, 23, 0),
]


class ParsingScheduler:
    def __init__(self, browser_context: BrowserContext):
        self.context = browser_context
        self.list_parser = HHVacancyListParser()
        self.detail_parser = HHVacancyDetailParser()
        self.filter_service = VacancyFilter()
        self.stop_event = asyncio.Event()
        self.scorer = VacancyScorer()

    async def _interruptible_sleep(self, seconds: float):
        """Спит с проверкой stop_event каждые 10 секунд."""
        end_time = asyncio.get_event_loop().time() + seconds
        while not self.stop_event.is_set():
            remaining = end_time - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(remaining, 10))

    async def force_parse(self, pages: int = 3):
        """
        Принудительный парсинг вне расписания (без создания лога окна).
        """
        logger.info(f"🚀 Принудительный парсинг {pages} страниц")
        total_saved = 0
        try:
            async with AsyncSessionLocal() as session:
                progress_repo = ParsingProgressRepository(session)
                progress = await progress_repo.get_or_create(settings.HH_SEARCH_URL)
                start_page = progress.last_page + 1  # начинаем со следующей страницы за последней сохранённой

            for i in range(pages):
                page = start_page + i
                logger.info(f"Принудительный парсинг страницы {page}")
                vacancies_list = await self.list_parser.fetch_ids(settings.HH_SEARCH_URL, page, self.context)
                if not vacancies_list:
                    logger.info(f"Нет вакансий на странице {page}, остановка")
                    break

                # Категоризация, фильтрация, парсинг – копируем логику из _run_parsing
                all_vacancies = []
                for vac in vacancies_list:
                    category = self.filter_service.categorize(vac['title'])
                    vac['category'] = category
                    all_vacancies.append(vac)

                # Отбрасываем rejected
                to_parse = [v for v in all_vacancies if v['category'] != 'reject']
                if not to_parse:
                    continue

                # Парсим детали
                semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT)

                async def parse_one(vac):
                    async with semaphore:
                        try:
                            details = await self.detail_parser.parse(vac['url'], self.context)
                            vacancy_data = {**vac, **details}
                            score = self.scorer.compute(vacancy_data)
                            vacancy_data['score'] = score
                            vacancy_data['level'] = vac['category']
                            return vacancy_data
                        except Exception as e:
                            logger.error(f"Ошибка парсинга {vac['id']}: {e}")
                            return None

                tasks = [parse_one(v) for v in to_parse]
                results = await asyncio.gather(*tasks)
                successful = [r for r in results if r is not None]

                # Сохраняем
                saved = 0
                async with AsyncSessionLocal() as session:
                    repo = VacancyRepository(session)
                    for vac in successful:
                        inserted = await repo.save_if_not_exists(vac)
                        if inserted:
                            saved += 1
                    # Обновляем прогресс (чтобы в следующий раз начать со следующей страницы)
                    progress_repo = ParsingProgressRepository(session)
                    await progress_repo.update_last_page(settings.HH_SEARCH_URL, page)
                    total_saved += saved
                logger.info(f"Принудительно: страница {page} сохранено {saved} вакансий")

            logger.info(f"✅ Принудительный парсинг завершён, сохранено {total_saved} вакансий")
        except Exception as e:
            logger.exception(f"Ошибка при принудительном парсинге: {e}")

    async def run(self):
        try:
            while not self.stop_event.is_set():
                now = msk_now()
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
                        # TODO: при масштабировании использовать with_for_update(skip_locked=True)
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
                        await self._interruptible_sleep(wait_seconds)

                    await self._run_parsing(idx)

                tomorrow = now.date() + timedelta(days=1)
                next_check = datetime.combine(tomorrow, time(0, 5)).replace(tzinfo=MSK_TZ)
                sleep_seconds = (next_check - now).total_seconds()
                if sleep_seconds > 0:
                    logger.info(f"Parsing scheduler: next check at {next_check}")
                    await self._interruptible_sleep(sleep_seconds)
        except asyncio.CancelledError:
            logger.info("Parsing scheduler cancelled")
            raise

    async def _run_parsing(self, window_idx: int):
        logger.info(f"Starting parsing for window {window_idx}")
        start_time = msk_now()
        start_time_naive = start_time.replace(tzinfo=None)

        # Проверка дневного лимита
        today_start = start_time.replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)
        async with AsyncSessionLocal() as session:
            total_today = await session.execute(
                select(func.sum(ParsingLog.vacancies_found))
                .where(ParsingLog.started_at >= today_start)
            )
            total = total_today.scalar() or 0
            if total >= settings.DAILY_PARSING_LIMIT:
                logger.info(f"Daily parsing limit reached ({total} >= {settings.DAILY_PARSING_LIMIT}), skipping window")
                return

        # Создаём лог-запись
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
            async with AsyncSessionLocal() as session:
                progress_repo = ParsingProgressRepository(session)
                progress = await progress_repo.get_or_create(settings.HH_SEARCH_URL)

            pages_to_do = settings.PAGES_PER_WINDOW

            for i in range(1, pages_to_do + 1):
                if total_saved >= settings.DAILY_PARSING_LIMIT:
                    logger.info("Daily limit reached, stopping further pages.")
                    break

                next_page = progress.last_page + i
                logger.info(f"Parsing page {next_page} for window {window_idx}")

                vacancies_list = await self.list_parser.fetch_ids(
                    settings.HH_SEARCH_URL, next_page, self.context
                )
                if not vacancies_list:
                    logger.info(f"No vacancies found on page {next_page}, stopping further pages.")
                    break

                # Категоризируем все вакансии и добавляем поле category
                all_vacancies = []
                for vac in vacancies_list:
                    category = self.filter_service.categorize(vac['title'])
                    vac['category'] = category
                    all_vacancies.append(vac)

                # Логируем статистику по категориям
                regular_count = sum(1 for v in all_vacancies if v['category'] == 'regular')
                junior_count = sum(1 for v in all_vacancies if v['category'] == 'junior')
                senior_count = sum(1 for v in all_vacancies if v['category'] == 'senior')
                rejected_count = sum(1 for v in all_vacancies if v['category'] == 'reject')
                logger.info(
                    f"Page {next_page}: regular={regular_count}, junior={junior_count}, "
                    f"senior={senior_count}, rejected={rejected_count}"
                )

                # Сохраняем rejected отдельно (в таблицу и файл)
                rejected = [v for v in all_vacancies if v['category'] == 'reject']
                if rejected:
                    async with AsyncSessionLocal() as session:
                        for vac in rejected:
                            stmt = pg_insert(FilteredOutVacancy).values(
                                hh_id=vac['id'],
                                title=vac['title'],
                                url=vac['url'],
                                filter_reason='filtered out by criteria'
                            ).on_conflict_do_nothing(index_elements=['hh_id'])
                            await session.execute(stmt)
                        await session.commit()
                    log_dir = Path(settings.REJECTED_LOGS_DIR)
                    log_dir.mkdir(exist_ok=True)
                    today_str = msk_now().strftime("%Y-%m-%d")
                    log_file = log_dir / f"rejected_{today_str}.txt"
                    with open(log_file, "a", encoding="utf-8") as f:
                        for vac in rejected:
                            f.write(f"{vac['title']} | {vac['url']}\n")
                    logger.info(f"Appended {len(rejected)} rejected vacancies to {log_file}")

                # Оставляем только те, что не rejected (regular, junior, senior)
                to_parse = [v for v in all_vacancies if v['category'] != 'reject']
                if not to_parse:
                    logger.info("No non-rejected vacancies to parse on this page.")
                    continue

                # Парсим все оставшиеся вакансии
                semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT)

                async def parse_one(vac):
                    async with semaphore:
                        logger.debug(f"🔍 Парсинг вакансии {vac['id']} - {vac['title']}")
                        try:
                            details = await self.detail_parser.parse(vac['url'], self.context)
                            vacancy_data = {**vac, **details}
                            # Вычисляем скор
                            score = self.scorer.compute(vacancy_data)
                            vacancy_data['score'] = score
                            # Устанавливаем уровень (из категории)
                            level = vac['category']  # 'regular', 'junior', 'senior'
                            vacancy_data['level'] = level
                            logger.info(f"Vacancy {vac['id']} (\"{vac['title']}\") score: {score}, level: {level}")
                            return vacancy_data
                        except Exception as e:
                            logger.error(f"❌ Ошибка парсинга {vac['id']}: {e}", exc_info=True)
                            return None

                tasks = [parse_one(v) for v in to_parse]
                results = await asyncio.gather(*tasks)
                successful = [r for r in results if r is not None]

                saved_this_page = 0
                async with AsyncSessionLocal() as session:
                    repo = VacancyRepository(session)
                    for vac in successful:
                        inserted = await repo.save_if_not_exists(vac)
                        if inserted:
                            saved_this_page += 1
                    logger.info(f"Page {next_page}: saved {saved_this_page} new vacancies")
                    total_saved += saved_this_page

                async with AsyncSessionLocal() as session:
                    progress_repo = ParsingProgressRepository(session)
                    await progress_repo.update_last_page(settings.HH_SEARCH_URL, next_page)
                    pages_processed += 1

            # Обновляем лог
            async with AsyncSessionLocal() as session:
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

    async def stop(self):
        self.stop_event.set()
