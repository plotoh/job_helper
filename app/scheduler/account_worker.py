import asyncio
import logging
import random
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import AsyncSessionLocal
from app.db.repositories.account_state_repo import AccountStateRepository
from app.db.repositories.vacancy_repo import VacancyRepository
from app.db.repositories.response_error_repo import ResponseErrorRepository
from app.hh_client.hh_response import HHResponseService
from app.hh_client.message_generator import generate_message
from app.scheduler.daily_planner import generate_daily_limit, generate_pauses
from app.utils.settings_utils import get_setting, get_include_junior, get_include_senior
from app.utils.time_utils import msk_now, MSK_TZ

logger = logging.getLogger(__name__)

WORK_START = time(settings.WORK_START_HOUR, settings.WORK_START_MINUTE)
WORK_END = time(settings.WORK_END_HOUR, settings.WORK_END_MINUTE)


class AccountWorker:
    def __init__(self, account_id: int, resume_id: str, browser_context, template: str):
        self.account_id = account_id
        self.resume_id = resume_id
        self.context = browser_context
        self.template = template
        self.response_service = HHResponseService()
        self.stop_event = asyncio.Event()
        self.consecutive_failures = 0

    # ---------- Сон с проверками ----------

    async def _interruptible_sleep(self, seconds: float, watch_pause: bool = False):
        """
        Спит с проверкой:
          - stop_event (каждые 10 сек)
          - глобального флага responses_enabled (каждые 30 сек)
          - ручного override (каждые 30 сек, если watch_pause=True)
        """
        end_time = asyncio.get_event_loop().time() + seconds
        last_db_check = asyncio.get_event_loop().time()
        while not self.stop_event.is_set():
            remaining = end_time - asyncio.get_event_loop().time()
            if remaining <= 0:
                break

            now_loop = asyncio.get_event_loop().time()
            if now_loop - last_db_check >= 30:
                last_db_check = now_loop
                try:
                    responses_enabled = await get_setting('responses_enabled', 'true')
                    if responses_enabled != 'true':
                        logger.debug(
                            f"Account {self.account_id}: глобальные отклики отключены, прерываем сон"
                        )
                        break

                    if watch_pause:
                        async with AsyncSessionLocal() as session:
                            state_repo = AccountStateRepository(session)
                            today = msk_now().date()
                            state = await state_repo.get_for_today(self.account_id, today)
                            if state and state.manual_override:
                                logger.debug(
                                    f"Account {self.account_id}: ручной override включён, прерываем сон"
                                )
                                break
                except Exception as e:
                    logger.warning(
                        f"Account {self.account_id}: ошибка проверки статуса в БД во время сна: {e}"
                    )

            await asyncio.sleep(min(remaining, 10))

    # ---------- Основной цикл ----------

    async def run(self):
        logger.info(f"Starting worker for account {self.account_id}")

        try:
            while not self.stop_event.is_set():
                today = msk_now().date()

                async with AsyncSessionLocal() as session:
                    state_repo = AccountStateRepository(session)
                    state = await state_repo.get_for_today(self.account_id, today)

                    if not state:
                        logger.info(
                            f"No daily state for account {self.account_id} today, generating now."
                        )
                        limit = generate_daily_limit()
                        pauses = generate_pauses()
                        data = {
                            'daily_limit': limit,
                            'pauses': pauses,
                            'done_count': 0,
                            'status': 'active',
                            'manual_override': False,
                        }
                        state = await state_repo.create_or_update(self.account_id, today, data)
                        continue

                    now = msk_now()
                    current_time = now.time()

                    # --- Проверка лимита (всегда) ---
                    if state.done_count >= state.daily_limit:
                        logger.info(
                            f"Account {self.account_id}: daily limit reached. Marking completed."
                        )
                        await state_repo.update_status(self.account_id, today, 'completed')
                        continue

                    # --- РЕЖИМ РУЧНОГО OVERRIDE: игнорируем расписание и паузы ---
                    if state.manual_override:
                        logger.debug(
                            f"Account {self.account_id}: manual_override активен, "
                            f"игнорируем расписание"
                        )

                        min_interval = 34
                        max_interval = 117
                        interval = random.uniform(min_interval, max_interval)
                        logger.debug(
                            f"Account {self.account_id}: waiting {interval:.1f}s before next response"
                        )
                        await self._interruptible_sleep(interval, watch_pause=False)

                        responses_enabled = await get_setting('responses_enabled', 'true')
                        if responses_enabled != 'true':
                            logger.debug(
                                f"Account {self.account_id}: глобальные отклики отключены, спим 30 сек"
                            )
                            await self._interruptible_sleep(30, watch_pause=False)
                            continue

                        await self._do_response(session, state, today)
                        continue

                    # --- ОБЫЧНЫЙ РЕЖИМ ---

                    # Если ещё не началось рабочее время
                    if current_time < WORK_START:
                        sleep_seconds = (
                                datetime.combine(now.date(), WORK_START).replace(tzinfo=MSK_TZ) - now
                        ).total_seconds()
                        logger.debug(
                            f"Account {self.account_id}: before work hours, "
                            f"sleeping {sleep_seconds:.0f}s"
                        )
                        await self._interruptible_sleep(sleep_seconds, watch_pause=True)
                        continue

                    # Если рабочий день закончился
                    if current_time >= WORK_END or state.status == 'completed':
                        if state.status != 'completed':
                            await state_repo.mark_completed(self.account_id, today)
                        logger.info(
                            f"Account {self.account_id}: work day ended or completed. "
                            f"Sleeping until tomorrow."
                        )
                        tomorrow = now.date() + timedelta(days=1)
                        next_start = datetime.combine(tomorrow, WORK_START).replace(tzinfo=MSK_TZ)
                        sleep_seconds = (next_start - now).total_seconds()
                        await self._interruptible_sleep(sleep_seconds, watch_pause=True)
                        continue

                    # Обработка статуса 'paused' из БД
                    if state.status == 'paused' and state.pause_until:
                        now_naive = now.replace(tzinfo=None)
                        if state.pause_until > now_naive:
                            sleep_seconds = (state.pause_until - now_naive).total_seconds()
                            logger.debug(
                                f"Account {self.account_id}: still in pause until "
                                f"{state.pause_until}, sleeping {sleep_seconds:.0f}s"
                            )
                            await self._interruptible_sleep(sleep_seconds, watch_pause=True)
                            await state_repo.update_status(
                                self.account_id, today, 'active', pause_until=None
                            )
                            logger.debug(f"Account {self.account_id}: pause ended, resuming")
                            continue
                        else:
                            await state_repo.update_status(
                                self.account_id, today, 'active', pause_until=None
                            )
                            logger.debug(
                                f"Account {self.account_id}: pause already passed, resuming"
                            )

                    # Проверка запланированных пауз
                    pauses = state.pauses
                    in_pause, pause_end = self._check_pause(current_time, pauses)
                    if in_pause:
                        pause_end_dt = datetime.combine(now.date(), pause_end).replace(tzinfo=MSK_TZ)
                        sleep_seconds = (pause_end_dt - now).total_seconds()
                        if sleep_seconds <= 0:
                            logger.warning(
                                f"Account {self.account_id}: pause end {pause_end} is in the past, skipping"
                            )
                            continue

                        logger.debug(
                            f"Account {self.account_id}: in pause until {pause_end}, "
                            f"sleeping {sleep_seconds:.0f}s"
                        )
                        pause_until_naive = (now + timedelta(seconds=sleep_seconds)).replace(tzinfo=None)
                        await state_repo.update_status(
                            self.account_id, today, 'paused', pause_until=pause_until_naive
                        )
                        await self._interruptible_sleep(sleep_seconds, watch_pause=True)
                        await state_repo.update_status(
                            self.account_id, today, 'active', pause_until=None
                        )
                        logger.debug(f"Account {self.account_id}: pause ended, resuming")
                        continue

                    # Определяем, когда следующая пауза
                    next_pause_start = self._next_pause_start(current_time, pauses)
                    max_sleep = None
                    if next_pause_start:
                        next_start_dt = datetime.combine(now.date(), next_pause_start).replace(tzinfo=MSK_TZ)
                        seconds_to_pause = (next_start_dt - now).total_seconds()
                        max_sleep = max(0.0, seconds_to_pause - 60)

                    # Интервал ожидания перед откликом
                    min_interval = 34
                    max_interval = 117
                    if max_sleep is not None:
                        upper_bound = min(max_interval, max_sleep)
                    else:
                        upper_bound = max_interval
                    if upper_bound < min_interval:
                        upper_bound = min_interval

                    interval = random.uniform(min_interval, upper_bound)
                    logger.debug(
                        f"Account {self.account_id}: waiting {interval:.1f}s before next response"
                    )
                    await self._interruptible_sleep(interval, watch_pause=False)

                    responses_enabled = await get_setting('responses_enabled', 'true')
                    if responses_enabled != 'true':
                        logger.debug(
                            f"Account {self.account_id}: глобальные отклики отключены, спим 30 сек"
                        )
                        await self._interruptible_sleep(30, watch_pause=False)
                        continue

                    # Выполняем отклик
                    await self._do_response(session, state, today)

        except asyncio.CancelledError:
            logger.info(f"Account {self.account_id}: worker cancelled")
            raise
        except Exception as e:
            logger.exception(f"Account {self.account_id}: unexpected error in main loop: {e}")
            await asyncio.sleep(10)

    # ---------- Паузы ----------

    def _check_pause(self, current_time: time, pauses: list) -> (bool, Optional[time]):
        for p in pauses:
            start = datetime.strptime(p['start'], "%H:%M").time()
            end = datetime.strptime(p['end'], "%H:%M").time()
            if start <= current_time < end:
                return True, end
        return False, None

    def _next_pause_start(self, current_time: time, pauses: list) -> Optional[time]:
        starts = [datetime.strptime(p['start'], "%H:%M").time() for p in pauses]
        future_starts = [t for t in starts if t > current_time]
        if future_starts:
            return min(future_starts)
        return None

    # ---------- Логирование откликов ----------

    async def _log_response(
            self, vacancy_id: str, title: str, url: str, status: str, error_text: str = ""
    ):
        """
        Логирует результат отклика в файлы:
          - logs/responses/responses_YYYY-MM-DD.txt — все статусы
          - logs/tests/tests_YYYY-MM-DD.txt         — только test_required
        """
        logger.debug(f"🪵 Логирование отклика: {vacancy_id} -> {status}")
        timestamp = msk_now().strftime("%Y-%m-%d %H:%M:%S")
        emoji = {
            "success": "✅",
            "wo_soprovod": "⚠️",
            "failed": "❌",
            "test_required": "🧪",
            "already_responded": "♻️",
        }.get(status, "❓")

        # --- Общий лог ---
        log_dir = Path("logs/responses")
        log_dir.mkdir(parents=True, exist_ok=True)
        today_str = msk_now().strftime("%Y-%m-%d")
        log_file = log_dir / f"responses_{today_str}.txt"
        line = (
            f"[{timestamp}] {emoji} Account {self.account_id} | "
            f"Vacancy {vacancy_id} - {title} | Ссылка: {url} | Статус: {status}"
        )
        if error_text:
            line += f" | Ошибка: {error_text}"
        line += "\n"

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._write_log, log_file, line)

        # --- Отдельный лог для тестов ---
        if status == 'test_required':
            test_dir = Path("logs/tests")
            test_dir.mkdir(parents=True, exist_ok=True)
            test_file = test_dir / f"tests_{today_str}.txt"
            test_line = (
                f"[{timestamp}] 🧪 Account {self.account_id} | "
                f"Vacancy {vacancy_id} - {title} | Ссылка: {url} | Требуется тест\n"
            )
            await loop.run_in_executor(None, self._write_log, test_file, test_line)

    def _write_log(self, log_file: Path, line: str):
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line)

    # ---------- Отклик на одну вакансию ----------

    async def _do_response(self, session: AsyncSession, state, today):
        vacancy_repo = VacancyRepository(session)
        error_repo = ResponseErrorRepository(session)
        state_repo = AccountStateRepository(session)

        include_junior = await get_include_junior()
        include_senior = await get_include_senior()
        vacancy = await vacancy_repo.get_random_unresponded(
            self.account_id,
            include_junior,
            include_senior,
            skip_test_required_by_others=settings.SKIP_TEST_REQUIRED_BY_OTHERS,
        )

        if not vacancy:
            self.consecutive_failures += 1
            if self.consecutive_failures >= 5:
                logger.warning(
                    f"Account {self.account_id}: no actual vacancies after "
                    f"{self.consecutive_failures} attempts. Waiting longer."
                )
                await self._interruptible_sleep(3600, watch_pause=False)
                self.consecutive_failures = 0
            else:
                logger.debug(
                    f"Account {self.account_id}: no actual vacancies, "
                    f"attempt {self.consecutive_failures}. Waiting 10 min."
                )
                await self._interruptible_sleep(600, watch_pause=False)
            return

        self.consecutive_failures = 0

        # Резервируем вакансию за аккаунтом, чтобы не выбрали повторно.
        # Без commit — коммит произойдёт в одной из финальных ветвей ниже.
        await vacancy_repo.mark_responded(
            self.account_id, vacancy.hh_id, status="pending", commit=False
        )

        context = {
            "vacancy_name": vacancy.title,
            "tg_username": settings.TG_USERNAME,
        }
        message = generate_message(self.template, context)

        # --- Одна попытка. Ретраи не нужны: статусы явные. ---
        try:
            response_status = await self.response_service.respond_to_vacancy(
                vacancy.url, self.resume_id, message, self.context
            )
        except Exception as e:
            logger.exception(f"Account {self.account_id}: exception during response")
            await error_repo.add(self.account_id, vacancy.hh_id, str(e), error_type='exception')
            await vacancy_repo.mark_responded(self.account_id, vacancy.hh_id, status="failed")
            await self._log_response(
                vacancy.hh_id, vacancy.title, vacancy.url, 'failed', str(e)
            )
            return

        # --- SUCCESS / WO_SOPROVOD ---
        if response_status in ('success', 'wo_soprovod'):
            await vacancy_repo.increment_used_count(vacancy.hh_id)
            new_done = await state_repo.increment_done(self.account_id, today)
            label = (
                '✅ отклик с письмом' if response_status == 'success'
                else '⚠️ отклик без письма'
            )
            logger.info(
                f"Account {self.account_id}: {label} на {vacancy.hh_id}, "
                f"done {new_done}/{state.daily_limit}"
            )
            await vacancy_repo.mark_responded(
                self.account_id, vacancy.hh_id, status=response_status
            )
            await self._log_response(
                vacancy.hh_id, vacancy.title, vacancy.url, response_status
            )
            return

        # --- TEST_REQUIRED ---
        if response_status == 'test_required':
            logger.info(
                f"Account {self.account_id}: вакансия {vacancy.hh_id} требует теста"
            )
            # used_count НЕ увеличиваем — отклик не совершён
            await vacancy_repo.mark_responded(
                self.account_id, vacancy.hh_id, status="test_required"
            )
            await self._log_response(
                vacancy.hh_id, vacancy.title, vacancy.url,
                'test_required', 'тест от работодателя',
            )
            return

        # --- ALREADY_RESPONDED ---
        if response_status == 'already_responded':
            logger.info(
                f"Account {self.account_id}: вакансия {vacancy.hh_id} уже откликнута ранее"
            )
            # used_count НЕ увеличиваем — отклик был не от нас
            await vacancy_repo.mark_responded(
                self.account_id, vacancy.hh_id, status="already_responded"
            )
            await self._log_response(
                vacancy.hh_id, vacancy.title, vacancy.url,
                'already_responded', 'отклик уже был сделан ранее',
            )
            return

        # --- UNKNOWN STATUS ---
        await error_repo.add(
            self.account_id, vacancy.hh_id,
            f"Unknown response status: {response_status}",
            error_type='unknown',
        )
        await vacancy_repo.mark_responded(
            self.account_id, vacancy.hh_id, status="failed"
        )
        await self._log_response(
            vacancy.hh_id, vacancy.title, vacancy.url,
            'failed', f'Unknown status: {response_status}',
        )

    # ---------- Остановка ----------

    async def stop(self):
        self.stop_event.set()
