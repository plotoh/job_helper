import asyncio
import logging
import random
from datetime import datetime, time, timedelta
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import AsyncSessionLocal
from app.db.repositories.account_state_repo import AccountStateRepository
from app.db.repositories.vacancy_repo import VacancyRepository
from app.db.repositories.response_error_repo import ResponseErrorRepository
from app.hh_client.hh_response import HHResponseService
from app.hh_client.message_generator import generate_message
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
        self.vacancy_attempts = {}  # {vacancy_id: attempts}
        self.consecutive_failures = 0

    async def run(self):
        """Основной цикл воркера."""
        logger.info(f"Starting worker for account {self.account_id}")

        while not self.stop_event.is_set():
            try:
                # Получаем сегодняшнюю дату
                today = msk_now().date()

                # Создаём сессию БД (каждую итерацию)
                async with AsyncSessionLocal() as session:
                    state_repo = AccountStateRepository(session)
                    state = await state_repo.get_for_today(self.account_id, today)

                    if not state:
                        logger.info(f"No daily state for account {self.account_id} today, generating now.")
                        from app.scheduler.daily_planner import generate_daily_limit, generate_pauses
                        limit = generate_daily_limit()
                        pauses = generate_pauses()
                        data = {
                            'daily_limit': limit,
                            'pauses': pauses,
                            'done_count': 0,
                            'status': 'active'
                        }
                        state = await state_repo.create_or_update(self.account_id, today, data)
                        continue

                    # Проверяем, рабочий ли сейчас день
                    now = msk_now()
                    current_time = now.time()

                    # Если ещё не началось рабочее время
                    if current_time < WORK_START:
                        sleep_seconds = (
                                    datetime.combine(now.date(), WORK_START).replace(tzinfo=MSK_TZ) - now).total_seconds()
                        logger.info(f"Account {self.account_id}: before work hours, sleeping {sleep_seconds:.0f}s")
                        await asyncio.sleep(sleep_seconds)
                        continue

                    # Если рабочий день закончился
                    if current_time >= WORK_END or state.status == 'completed':
                        if state.status != 'completed':
                            await state_repo.mark_completed(self.account_id, today)
                        logger.info(f"Account {self.account_id}: work day ended or completed. Sleeping until tomorrow.")
                        # Спим до следующего рабочего дня (10:00 следующего дня)
                        tomorrow = now.date() + timedelta(days=1)
                        next_start = datetime.combine(tomorrow, WORK_START).replace(tzinfo=MSK_TZ)
                        sleep_seconds = (next_start - now).total_seconds()
                        await asyncio.sleep(sleep_seconds)
                        continue

                    # Если лимит уже достигнут
                    if state.done_count >= state.daily_limit:
                        logger.info(f"Account {self.account_id}: daily limit reached. Marking completed.")
                        await state_repo.update_status(self.account_id, today, 'completed')
                        continue

                    # --- Обработка статуса 'paused' из БД (если воркер перезапустился во время паузы) ---
                    if state.status == 'paused' and state.pause_until:
                        now_naive = now.replace(tzinfo=None)
                        if state.pause_until > now_naive:
                            sleep_seconds = (state.pause_until - now_naive).total_seconds()
                            logger.info(
                                f"Account {self.account_id}: still in pause until {state.pause_until}, sleeping {sleep_seconds:.0f}s")
                            await asyncio.sleep(sleep_seconds)
                            await state_repo.update_status(self.account_id, today, 'active', pause_until=None)
                            logger.info(f"Account {self.account_id}: pause ended, resuming")
                            continue
                        else:
                            await state_repo.update_status(self.account_id, today, 'active', pause_until=None)
                            logger.info(f"Account {self.account_id}: pause already passed, resuming")
                            # Продолжаем нормальную работу

                    # --- Проверка запланированных пауз ---
                    pauses = state.pauses  # список [{"start": "HH:MM", "end": "HH:MM"}, ...]
                    in_pause, pause_end = self._check_pause(current_time, pauses)
                    if in_pause:
                        pause_end_dt = datetime.combine(now.date(), pause_end).replace(tzinfo=MSK_TZ)
                        sleep_seconds = (pause_end_dt - now).total_seconds()
                        if sleep_seconds <= 0:
                            logger.warning(f"Account {self.account_id}: pause end {pause_end} is in the past, skipping")
                            continue

                        logger.info(f"Account {self.account_id}: in pause until {pause_end}, sleeping {sleep_seconds:.0f}s")
                        pause_until_naive = (now + timedelta(seconds=sleep_seconds)).replace(tzinfo=None)
                        await state_repo.update_status(self.account_id, today, 'paused', pause_until=pause_until_naive)
                        try:
                            await asyncio.sleep(sleep_seconds)
                        except asyncio.CancelledError:
                            logger.warning(f"Account {self.account_id}: sleep cancelled during pause")
                            raise
                        await state_repo.update_status(self.account_id, today, 'active', pause_until=None)
                        logger.info(f"Account {self.account_id}: pause ended, resuming")
                        continue

                    # --- Определяем, когда следующая пауза, чтобы не начать отклик перед ней ---
                    next_pause_start = self._next_pause_start(current_time, pauses)
                    max_sleep = None
                    if next_pause_start:
                        next_start_dt = datetime.combine(now.date(), next_pause_start).replace(tzinfo=MSK_TZ)
                        seconds_to_pause = (next_start_dt - now).total_seconds()
                        # Нельзя спать дольше, чем до начала паузы минус небольшой запас (1 мин)
                        max_sleep = max(0.0, seconds_to_pause - 60)

                    # --- Выбираем интервал ожидания (случайный в пределах от min до max, но с учётом паузы) ---
                    min_interval = 60  # 1 минута в секундах
                    max_interval = 300  # 5 минут

                    if max_sleep is not None:
                        upper_bound = min(max_interval, max_sleep)
                    else:
                        upper_bound = max_interval

                    # Убедимся, что верхняя граница не меньше нижней
                    if upper_bound < min_interval:
                        upper_bound = min_interval

                    # Используем uniform для генерации float (asyncio.sleep поддерживает float)
                    interval = random.uniform(min_interval, upper_bound)
                    logger.info(f"Account {self.account_id}: waiting {interval:.1f}s before next response")
                    await asyncio.sleep(interval)

                    # --- Выполняем отклик ---
                    await self._do_response(session, state, today)

            except asyncio.CancelledError:
                logger.info(f"Account {self.account_id}: worker cancelled")
                break
            except Exception as e:
                logger.exception(f"Account {self.account_id}: unexpected error in main loop: {e}")
                # Небольшая пауза перед повтором, чтобы избежать бесконечного цикла ошибок
                await asyncio.sleep(10)

    def _check_pause(self, current_time: time, pauses: list) -> (bool, Optional[time]):
        """Проверяет, находится ли текущее время внутри какой-либо паузы."""
        for p in pauses:
            start = datetime.strptime(p['start'], "%H:%M").time()
            end = datetime.strptime(p['end'], "%H:%M").time()
            if start <= current_time < end:
                return True, end
        return False, None

    def _next_pause_start(self, current_time: time, pauses: list) -> Optional[time]:
        """Возвращает время начала следующей паузы (ближайшей после current_time) или None."""
        starts = [datetime.strptime(p['start'], "%H:%M").time() for p in pauses]
        future_starts = [t for t in starts if t > current_time]
        if future_starts:
            return min(future_starts)
        return None

    async def _do_response(self, session: AsyncSession, state, today):
        """Один цикл отклика."""
        vacancy_repo = VacancyRepository(session)
        error_repo = ResponseErrorRepository(session)
        state_repo = AccountStateRepository(session)

        # Получаем случайную вакансию
        vacancy = await vacancy_repo.get_random_unresponded(self.account_id)
        if not vacancy:
            # Если вакансий нет, увеличиваем счётчик неудач
            self.consecutive_failures += 1
            if self.consecutive_failures >= 5:  # допустим, после 5 неудач подряд считаем, что день исчерпан
                logger.warning(
                    f"Account {self.account_id}: no actual vacancies after {self.consecutive_failures} attempts, marking day as completed.")
                await state_repo.mark_completed(self.account_id, today)
                return
            else:
                logger.info(
                    f"Account {self.account_id}: no actual vacancies, attempt {self.consecutive_failures}, waiting longer.")
                await asyncio.sleep(3600)  # ждём 10 минут
                return
        else:
            self.consecutive_failures = 0  # сбрасываем при успехе

        # Генерируем сообщение
        context = {
            "vacancy_name": vacancy.title,
            "tg_username": settings.TG_USERNAME,
        }
        message = generate_message(self.template, context)

        # Выполняем отклик
        try:
            success = await self.response_service.respond_to_vacancy(
                vacancy.url, self.resume_id, message, self.context
            )
        except Exception as e:
            logger.exception(f"Account {self.account_id}: exception during response")
            await error_repo.add(self.account_id, vacancy.hh_id, str(e), error_type='exception')
            attempts = self.vacancy_attempts.get(vacancy.hh_id, 0) + 1
            self.vacancy_attempts[vacancy.hh_id] = attempts
            if attempts >= 3:
                logger.warning(f"Account {self.account_id}: vacancy {vacancy.hh_id} failed 3 times, marking as failed.")
                await vacancy_repo.mark_responded(self.account_id, vacancy.hh_id, status="failed")
            return

        if success:
            new_done = await state_repo.increment_done(self.account_id, today)
            logger.info(f"Account {self.account_id}: responded to {vacancy.hh_id}, done {new_done}/{state.daily_limit}")
            await vacancy_repo.mark_responded(self.account_id, vacancy.hh_id, status="success")
            # Сбрасываем счётчик попыток при успехе
            self.vacancy_attempts.pop(vacancy.hh_id, None)
        else:
            await error_repo.add(self.account_id, vacancy.hh_id, "Response returned False", error_type='response_failed')
            attempts = self.vacancy_attempts.get(vacancy.hh_id, 0) + 1
            self.vacancy_attempts[vacancy.hh_id] = attempts
            if attempts >= 3:
                logger.warning(f"Account {self.account_id}: vacancy {vacancy.hh_id} failed 3 times, marking as failed.")
                await vacancy_repo.mark_responded(self.account_id, vacancy.hh_id, status="failed")

    async def stop(self):
        self.stop_event.set()