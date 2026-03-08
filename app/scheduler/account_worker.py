import asyncio
import logging
import random
from datetime import datetime, time, timedelta
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from playwright.async_api import BrowserContext

from app.config import settings
from app.db import AsyncSessionLocal
from app.db import AccountStateRepository, VacancyRepository, ResponseErrorRepository
from app.hh_client import HHResponseService, generate_message

logger = logging.getLogger(__name__)


WORK_START = time(settings.WORK_START_HOUR, settings.WORK_START_MINUTE)
WORK_END = time(settings.WORK_END_HOUR, settings.WORK_END_MINUTE)


class AccountWorker:
    def __init__(self, account_id: int, resume_id: str, browser_context: BrowserContext, template: str):
        self.account_id = account_id
        self.resume_id = resume_id
        self.context = browser_context
        self.template = template
        self.response_service = HHResponseService()
        self.stop_event = asyncio.Event()

    async def run(self):
        """Основной цикл воркера."""
        logger.info(f"Starting worker for account {self.account_id}")

        while not self.stop_event.is_set():
            # Получаем сегодняшнюю дату
            today = datetime.now().date()

            # Создаём сессию БД (каждую итерацию, чтобы избежать проблем с устаревшими объектами)
            async with AsyncSessionLocal() as session:
                state_repo = AccountStateRepository(session)
                state = await state_repo.get_for_today(self.account_id, today)

                # Если нет состояния на сегодня — ждём, пока daily_planner создаст (или создадим сами)
                if not state:
                    logger.info(f"No daily state for account {self.account_id} today, waiting...")
                    # Ждём минуту и проверяем снова
                    await asyncio.sleep(60)
                    continue

                # Проверяем, рабочий ли сейчас день
                now = datetime.now()
                current_time = now.time()

                # Если ещё не началось рабочее время
                if current_time < WORK_START:
                    sleep_seconds = (datetime.combine(now.date(), WORK_START) - now).total_seconds()
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
                    next_start = datetime.combine(tomorrow, WORK_START)
                    sleep_seconds = (next_start - now).total_seconds()
                    await asyncio.sleep(sleep_seconds)
                    continue

                # Если лимит уже достигнут
                if state.done_count >= state.daily_limit:
                    logger.info(f"Account {self.account_id}: daily limit reached. Marking completed.")
                    await state_repo.update_status(self.account_id, today, 'completed')
                    continue

                # Обработка пауз
                pauses = state.pauses  # список [{"start": "HH:MM", "end": "HH:MM"}, ...]
                in_pause, pause_end = self._check_pause(current_time, pauses)
                if in_pause:
                    # Спим до конца паузы
                    sleep_seconds = (datetime.combine(now.date(), pause_end) - now).total_seconds()
                    logger.info(f"Account {self.account_id}: in pause until {pause_end}, sleeping {sleep_seconds:.0f}s")
                    await state_repo.update_status(self.account_id, today, 'paused',
                                                   pause_until=now + timedelta(seconds=sleep_seconds))
                    await asyncio.sleep(sleep_seconds)
                    await state_repo.update_status(self.account_id, today, 'active', pause_until=None)  # сброс
                    continue

                # Определяем, когда следующая пауза, чтобы не начать отклик перед ней
                next_pause_start = self._next_pause_start(current_time, pauses)
                max_sleep = None
                if next_pause_start:
                    # Время до следующей паузы
                    next_start_dt = datetime.combine(now.date(), next_pause_start)
                    seconds_to_pause = (next_start_dt - now).total_seconds()
                    # Нельзя спать дольше, чем до начала паузы минус небольшой запас (1 мин)
                    max_sleep = max(0, seconds_to_pause - 60)  # запас 1 минута, чтобы успеть проснуться до паузы

                # Выбираем интервал ожидания
                min_interval = 60  # 1 минута в секундах
                max_interval = 300  # 5 минут
                if max_sleep is not None and max_sleep < min_interval:
                    # Слишком мало времени до паузы, лучше сразу уйти в паузу
                    logger.info(f"Account {self.account_id}: too close to next pause, sleeping until pause")
                    await asyncio.sleep(max_sleep + 60)  # досыпаем до паузы и потом проверим снова
                    continue

                interval = random.randint(min_interval, min(max_interval, max_sleep if max_sleep else max_interval))
                logger.info(f"Account {self.account_id}: waiting {interval}s before next response")
                await asyncio.sleep(interval)

                # Выполняем отклик
                await self._do_response(session, state, today)

    def _check_pause(self, current_time: time, pauses: list) -> (bool, Optional[time]):
        """Проверяет, находится ли текущее время внутри какой-либо паузы. Возвращает (True, время окончания паузы) если внутри, иначе (False, None)."""
        for p in pauses:
            start = datetime.strptime(p['start'], "%H:%M").time()
            end = datetime.strptime(p['end'], "%H:%M").time()
            # Обрабатываем случай, если пауза переходит через полночь? Нет, у нас в пределах дня.
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
            logger.warning(f"Account {self.account_id}: no unresponded vacancies found")
            # Если нет вакансий, можно подождать и повторить позже (но парсинг должен их добавлять)
            await asyncio.sleep(300)  # 5 мин
            return

        # Генерируем сообщение
        context = {
            "vacancy_name": vacancy.title,
            "tg_username": settings.TG_USERNAME,  # возможно, нужно брать из аккаунта
        }
        message = generate_message(self.template, context)

        # Выполняем отклик
        try:
            success = await self.response_service.respond_to_vacancy(
                vacancy.url,
                self.resume_id,  # используем свой resume_id
                message,
                self.context
            )
        except Exception as e:
            logger.exception(f"Account {self.account_id}: exception during response")
            await error_repo.add(self.account_id, vacancy.hh_id, str(e), error_type='exception')
            return

        if success:
            # Увеличиваем счётчик
            new_done = await state_repo.increment_done(self.account_id, today)
            logger.info(f"Account {self.account_id}: responded to {vacancy.hh_id}, done {new_done}/{state.daily_limit}")
            await vacancy_repo.mark_responded(self.account_id, vacancy.hh_id, status="success")
        else:
            # Неудача
            await error_repo.add(self.account_id, vacancy.hh_id, "Response returned False",
                                 error_type='response_failed')
            # Не увеличиваем счётчик

    async def stop(self):
        """Останавливает воркер."""
        self.stop_event.set()
