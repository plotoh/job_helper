import asyncio
import logging
import random
from datetime import datetime, time, timedelta
from typing import Optional
import os
from pathlib import Path
from app.utils.time_utils import msk_now

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
        self.vacancy_attempts = {}
        self.consecutive_failures = 0

    async def _interruptible_sleep(self, seconds: float):
        """Спит с проверкой stop_event каждые 10 секунд."""
        end_time = asyncio.get_event_loop().time() + seconds
        while not self.stop_event.is_set():
            remaining = end_time - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(remaining, 10))

    async def run(self):
        logger.info(f"Starting worker for account {self.account_id}")

        try:
            while not self.stop_event.is_set():
                today = msk_now().date()

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

                    now = msk_now()
                    current_time = now.time()

                    # Если ещё не началось рабочее время
                    if current_time < WORK_START:
                        sleep_seconds = (datetime.combine(now.date(), WORK_START).replace(
                            tzinfo=MSK_TZ) - now).total_seconds()
                        logger.debug(f"Account {self.account_id}: before work hours, sleeping {sleep_seconds:.0f}s")
                        await self._interruptible_sleep(sleep_seconds)
                        continue

                    # Если рабочий день закончился
                    if current_time >= WORK_END or state.status == 'completed':
                        if state.status != 'completed':
                            await state_repo.mark_completed(self.account_id, today)
                        logger.info(f"Account {self.account_id}: work day ended or completed. Sleeping until tomorrow.")
                        tomorrow = now.date() + timedelta(days=1)
                        next_start = datetime.combine(tomorrow, WORK_START).replace(tzinfo=MSK_TZ)
                        sleep_seconds = (next_start - now).total_seconds()
                        await self._interruptible_sleep(sleep_seconds)
                        continue

                    # Если лимит уже достигнут
                    if state.done_count >= state.daily_limit:
                        logger.info(f"Account {self.account_id}: daily limit reached. Marking completed.")
                        await state_repo.update_status(self.account_id, today, 'completed')
                        continue

                    # Обработка статуса 'paused' из БД
                    if state.status == 'paused' and state.pause_until:
                        now_naive = now.replace(tzinfo=None)
                        if state.pause_until > now_naive:
                            sleep_seconds = (state.pause_until - now_naive).total_seconds()
                            logger.debug(
                                f"Account {self.account_id}: still in pause until {state.pause_until}, sleeping {sleep_seconds:.0f}s")
                            await self._interruptible_sleep(sleep_seconds)
                            await state_repo.update_status(self.account_id, today, 'active', pause_until=None)
                            logger.debug(f"Account {self.account_id}: pause ended, resuming")
                            continue
                        else:
                            await state_repo.update_status(self.account_id, today, 'active', pause_until=None)
                            logger.debug(f"Account {self.account_id}: pause already passed, resuming")

                    # Проверка запланированных пауз
                    pauses = state.pauses
                    in_pause, pause_end = self._check_pause(current_time, pauses)
                    if in_pause:
                        pause_end_dt = datetime.combine(now.date(), pause_end).replace(tzinfo=MSK_TZ)
                        sleep_seconds = (pause_end_dt - now).total_seconds()
                        if sleep_seconds <= 0:
                            logger.warning(f"Account {self.account_id}: pause end {pause_end} is in the past, skipping")
                            continue

                        logger.debug(
                            f"Account {self.account_id}: in pause until {pause_end}, sleeping {sleep_seconds:.0f}s")
                        pause_until_naive = (now + timedelta(seconds=sleep_seconds)).replace(tzinfo=None)
                        await state_repo.update_status(self.account_id, today, 'paused', pause_until=pause_until_naive)
                        await self._interruptible_sleep(sleep_seconds)
                        await state_repo.update_status(self.account_id, today, 'active', pause_until=None)
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
                    logger.debug(f"Account {self.account_id}: waiting {interval:.1f}s before next response")
                    await self._interruptible_sleep(interval)

                    # Выполняем отклик
                    await self._do_response(session, state, today)

        except asyncio.CancelledError:
            logger.info(f"Account {self.account_id}: worker cancelled")
            raise
        except Exception as e:
            logger.exception(f"Account {self.account_id}: unexpected error in main loop: {e}")
            await asyncio.sleep(10)

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

    async def _log_response(self, vacancy_id: str, title: str, status: str, error_text: str = ""):
        """Логирует результат отклика в файл logs/responses/responses_YYYY-MM-DD.txt"""
        log_dir = Path("logs/responses")
        log_dir.mkdir(parents=True, exist_ok=True)
        today_str = msk_now().strftime("%Y-%m-%d")
        log_file = log_dir / f"responses_{today_str}.txt"

        timestamp = msk_now().strftime("%Y-%m-%d %H:%M:%S")
        # Эмодзи для статуса
        emoji = {"success": "✅", "wo_soporovod": "⚠️", "failed": "❌"}.get(status, "❓")
        line = f"[{timestamp}] {emoji} Account {self.account_id} | Vacancy {vacancy_id} - {title} | Статус: {status}"
        if error_text:
            line += f" | Ошибка: {error_text}"
        line += "\n"

        # Асинхронная запись в файл (используем run_in_executor, чтобы не блокировать)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._write_log, log_file, line)

    def _write_log(self, log_file: Path, line: str):
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line)

    async def _do_response(self, session: AsyncSession, state, today):
        vacancy_repo = VacancyRepository(session)
        error_repo = ResponseErrorRepository(session)
        state_repo = AccountStateRepository(session)

        vacancy = await vacancy_repo.get_random_unresponded(self.account_id)
        if not vacancy:
            self.consecutive_failures += 1
            if self.consecutive_failures >= 5:
                logger.warning(
                    f"Account {self.account_id}: no actual vacancies after {self.consecutive_failures} attempts. Waiting longer.")
                await self._interruptible_sleep(3600)
                self.consecutive_failures = 0
            else:
                logger.debug(
                    f"Account {self.account_id}: no actual vacancies, attempt {self.consecutive_failures}. Waiting 10 min.")
                await self._interruptible_sleep(600)
            return
        else:
            self.consecutive_failures = 0

        context = {
            "vacancy_name": vacancy.title,
            "tg_username": settings.TG_USERNAME,
        }
        message = generate_message(self.template, context)

        try:
            response_status = await self.response_service.respond_to_vacancy(
                vacancy.url, self.resume_id, message, self.context
            )
        except Exception as e:
            logger.exception(f"Account {self.account_id}: exception during response")
            await error_repo.add(self.account_id, vacancy.hh_id, str(e), error_type='exception')
            # Помечаем как failed, чтобы не повторять
            await vacancy_repo.mark_responded(self.account_id, vacancy.hh_id, status="failed")
            await self._log_response(vacancy.hh_id, vacancy.title, 'failed', str(e))
            return

        # Обработка статусов
        if response_status == 'success':
            new_done = await state_repo.increment_done(self.account_id, today)
            logger.info(
                f"Account {self.account_id}: ✅ отклик с письмом на {vacancy.hh_id}, done {new_done}/{state.daily_limit}")
            await vacancy_repo.mark_responded(self.account_id, vacancy.hh_id, status="success")
            await vacancy_repo.increment_used_count(vacancy.hh_id)
            self.vacancy_attempts.pop(vacancy.hh_id, None)
            await self._log_response(vacancy.hh_id, vacancy.title, 'success')

        elif response_status == 'wo_soporovod':
            new_done = await state_repo.increment_done(self.account_id, today)
            logger.info(
                f"Account {self.account_id}: ⚠️ отклик без письма на {vacancy.hh_id}, done {new_done}/{state.daily_limit}")
            await vacancy_repo.mark_responded(self.account_id, vacancy.hh_id, status="wo_soporovod")
            await vacancy_repo.increment_used_count(vacancy.hh_id)
            self.vacancy_attempts.pop(vacancy.hh_id, None)
            await self._log_response(vacancy.hh_id, vacancy.title, 'wo_soporovod')

        else:  # 'failed'
            await error_repo.add(self.account_id, vacancy.hh_id, "Response returned failed",
                                 error_type='response_failed')
            attempts = self.vacancy_attempts.get(vacancy.hh_id, 0) + 1
            self.vacancy_attempts[vacancy.hh_id] = attempts
            if attempts >= 3:
                logger.warning(f"Account {self.account_id}: vacancy {vacancy.hh_id} failed 3 times, marking as failed.")
                await vacancy_repo.mark_responded(self.account_id, vacancy.hh_id, status="failed")
            await self._log_response(vacancy.hh_id, vacancy.title, 'failed', 'response failed')

    # async def _do_response(self, session: AsyncSession, state, today):
    #     vacancy_repo = VacancyRepository(session)
    #     error_repo = ResponseErrorRepository(session)
    #     state_repo = AccountStateRepository(session)
    #
    #     vacancy = await vacancy_repo.get_random_unresponded(self.account_id)
    #     if not vacancy:
    #         self.consecutive_failures += 1
    #         if self.consecutive_failures >= 5:
    #             logger.warning(
    #                 f"Account {self.account_id}: no actual vacancies after {self.consecutive_failures} attempts. "
    #                 "Waiting longer before retry."
    #             )
    #             await self._interruptible_sleep(3600)  # час
    #             self.consecutive_failures = 0
    #         else:
    #             logger.debug(
    #                 f"Account {self.account_id}: no actual vacancies, attempt {self.consecutive_failures}. Waiting 10 minutes.")
    #             await self._interruptible_sleep(600)
    #         return
    #     else:
    #         self.consecutive_failures = 0
    #
    #     context = {
    #         "vacancy_name": vacancy.title,
    #         "tg_username": settings.TG_USERNAME,
    #     }
    #     message = generate_message(self.template, context)
    #
    #     try:
    #         success = await self.response_service.respond_to_vacancy(
    #             vacancy.url, self.resume_id, message, self.context
    #         )
    #     except Exception as e:
    #         logger.exception(f"Account {self.account_id}: exception during response")
    #         await error_repo.add(self.account_id, vacancy.hh_id, str(e), error_type='exception')
    #         attempts = self.vacancy_attempts.get(vacancy.hh_id, 0) + 1
    #         self.vacancy_attempts[vacancy.hh_id] = attempts
    #         if attempts >= 3:
    #             logger.warning(f"Account {self.account_id}: vacancy {vacancy.hh_id} failed 3 times, marking as failed.")
    #             await vacancy_repo.mark_responded(self.account_id, vacancy.hh_id, status="failed")
    #         return
    #
    #     if success:
    #         new_done = await state_repo.increment_done(self.account_id, today)
    #         logger.info(f"Account {self.account_id}: responded to {vacancy.hh_id}, done {new_done}/{state.daily_limit}")
    #         await vacancy_repo.mark_responded(self.account_id, vacancy.hh_id, status="success")
    #         await vacancy_repo.increment_used_count(vacancy.hh_id)
    #         self.vacancy_attempts.pop(vacancy.hh_id, None)
    #     else:
    #         await error_repo.add(self.account_id, vacancy.hh_id, "Response returned False",
    #                              error_type='response_failed')
    #         attempts = self.vacancy_attempts.get(vacancy.hh_id, 0) + 1
    #         self.vacancy_attempts[vacancy.hh_id] = attempts
    #         await vacancy_repo.mark_responded(self.account_id, vacancy.hh_id, status="wo_soporovod")
    #
    #         if attempts >= 3:
    #             logger.warning(f"Account {self.account_id}: vacancy {vacancy.hh_id} failed 3 times, marking as failed.")
    #             await vacancy_repo.mark_responded(self.account_id, vacancy.hh_id, status="failed")

    async def stop(self):
        self.stop_event.set()
