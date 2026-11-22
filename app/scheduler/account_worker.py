# app/scheduler/account_worker.py
import asyncio
import logging
import random
from datetime import datetime, time, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from app.config import settings
from app.db import AsyncSessionLocal
from app.db.models import Account
from app.db.repositories.account_repo import AccountRepository
from app.db.repositories.account_state_repo import AccountStateRepository
from app.db.repositories.response_error_repo import ResponseErrorRepository
from app.db.repositories.vacancy_repo import VacancyRepository
from app.hh_client.hh_response import HHResponseService
from app.hh_client.message_generator import generate_message
from app.scheduler.daily_planner import generate_daily_limit, generate_pauses
from app.utils.proxy_utils import build_proxy_dict
from app.utils.settings_utils import (
    get_setting,
    get_include_junior,
    get_include_senior,
    effective_settings,
)
from app.utils.time_utils import msk_now, MSK_TZ

logger = logging.getLogger(__name__)


def setup_account_logger(account_id: int) -> logging.Logger:
    """Отдельный logger с файлом logs/accounts/{id}.log + propagate в root."""
    acc_logger = logging.getLogger(f"account.{account_id}")
    if acc_logger.handlers:
        return acc_logger

    acc_logger.setLevel(logging.DEBUG)
    acc_logger.propagate = True

    log_dir = Path("logs/accounts")
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(
        log_dir / f"{account_id}.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setFormatter(logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s"
    ))
    acc_logger.addHandler(fh)
    return acc_logger


class AccountWorker:
    def __init__(self, account: Account, browser_context, template: str,
                 browser_manager=None):
        self.account = account
        self.account_id = account.id
        self.resume_id = account.resume_id
        self.context = browser_context
        self.browser_manager = browser_manager
        self.template = template
        self.response_service = HHResponseService()
        self.stop_event = asyncio.Event()
        self.consecutive_failures = 0
        self.log = setup_account_logger(account.id)
        self._iterations = 0
        self._apply_settings(account)
        self.auth_failures = 0

    def _apply_settings(self, account: Account):
        cfg = effective_settings(account, settings)
        self.cfg = cfg
        self.work_start = time(cfg["work_start_hour"], cfg["work_start_minute"])
        self.work_end = time(cfg["work_end_hour"], cfg["work_end_minute"])

    async def _reload_account(self) -> bool:
        async with AsyncSessionLocal() as session:
            repo = AccountRepository(session)
            fresh = await repo.get(self.account_id)
            if not fresh:
                return False
            self.account = fresh
            self.resume_id = fresh.resume_id
            self._apply_settings(fresh)
            # обновляем include-настройки
            self.cfg["include_junior"] = await get_include_junior()
            self.cfg["include_senior"] = await get_include_senior()
            return bool(fresh.is_enabled)

    # ---------- Сон с проверками ----------

    async def _interruptible_sleep(self, seconds: float, watch_pause: bool = False):
        if seconds >= 300:
            self.log.info(f"💤 Сон {seconds / 60:.1f} мин (watch_pause={watch_pause})")
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
                        self.log.debug("Глобальные отклики отключены, прерываем сон")
                        break

                    if watch_pause:
                        async with AsyncSessionLocal() as session:
                            state_repo = AccountStateRepository(session)
                            today = msk_now().date()
                            state = await state_repo.get_for_today(self.account_id, today)
                            if state and state.manual_override:
                                self.log.debug("Ручной override включён, прерываем сон")
                                break
                except Exception as e:
                    self.log.warning(f"Ошибка проверки статуса в БД во время сна: {e}")

            await asyncio.sleep(min(remaining, 10))

    # ---------- Проверка и пересоздание браузерного контекста ----------

    async def _check_browser_alive(self) -> bool:
        try:
            page = await asyncio.wait_for(self.context.new_page(), timeout=10)
            await page.close()
            return True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.log.error(f"Browser context dead: {type(e).__name__}: {e}")
            return False

    async def _recreate_browser_context(self) -> bool:
        self.log.warning("♻️ Пересоздаём browser context...")
        try:
            await asyncio.wait_for(self.context.close(), timeout=10)
        except Exception as e:
            self.log.warning(f"Не удалось закрыть старый контекст: {e}")

        if self.browser_manager is None:
            self.log.error("browser_manager не передан — рестарт не возможен")
            return False

        try:
            cookies_file = Path(settings.COOKIES_DIR) / f"{self.account.tg_username}.txt"
            proxy = build_proxy_dict(self.account)
            self.context = await self.browser_manager.create_context(
                cookies_source=str(cookies_file),
                proxy=proxy,
            )
            self.log.info("♻️ Browser context пересоздан")
            return True
        except Exception as e:
            self.log.error(f"Не удалось пересоздать контекст: {e}")
            return False

    # ---------- Основной цикл ----------

    async def run(self):
        self.log.info(f"Starting worker for account {self.account_id}")

        while not self.stop_event.is_set():
            try:
                await self._run_iteration()
            except asyncio.CancelledError:
                self.log.info("worker cancelled")
                raise
            except Exception as e:
                self.log.exception(f"unexpected error in iteration: {e}")
                try:
                    await self._interruptible_sleep(30, watch_pause=False)
                except asyncio.CancelledError:
                    self.log.info("worker cancelled during error recovery")
                    raise

        self.log.info("worker stopped")

    async def _run_iteration(self):
        self._iterations += 1

        # Hot-reload настроек из БД
        if self._iterations % 5 == 0:
            if not await self._reload_account():
                self.log.info("Аккаунт отключён в UI (is_enabled=false), пауза 60 сек")
                await self._interruptible_sleep(60, watch_pause=False)
                return

        today = msk_now().date()

        # ============================================================
        # КОРОТКАЯ СЕССИЯ: читаем state и вытаскиваем примитивы
        # ============================================================
        daily_limit = 0
        done_count = 0
        state_status = None
        state_pauses = []
        state_manual_override = False
        state_pause_until = None

        async with AsyncSessionLocal() as session:
            state_repo = AccountStateRepository(session)
            state = await state_repo.get_for_today(self.account_id, today)

            if not state:
                limit = generate_daily_limit(
                    self.cfg["daily_limit_min"], self.cfg["daily_limit_max"]
                )
                pauses = generate_pauses(
                    self.cfg["work_start_hour"], self.cfg["work_start_minute"],
                    self.cfg["work_end_hour"], self.cfg["work_end_minute"],
                    self.cfg["pause_count"],
                    self.cfg["pause_min_minutes"], self.cfg["pause_max_minutes"],
                    self.cfg["min_gap_minutes"],
                )
                data = {
                    'daily_limit': limit,
                    'pauses': pauses,
                    'done_count': 0,
                    'status': 'active',
                    'manual_override': False,
                }
                await state_repo.create_or_update(self.account_id, today, data)
                self.log.info(f"📅 Создан state на {today}: limit={limit}, pauses={len(pauses)}")
                return

            daily_limit = state.daily_limit
            done_count = state.done_count
            state_status = state.status
            state_pauses = list(state.pauses) if state.pauses else []
            state_manual_override = bool(state.manual_override)
            state_pause_until = state.pause_until

        now = msk_now()
        current_time = now.time()

        # ============================================================
        # Проверка дневного лимита (вне сессии)
        # ============================================================
        if done_count >= daily_limit:
            self.log.info(f"✅ Daily limit reached ({done_count}/{daily_limit})")
            async with AsyncSessionLocal() as s:
                await AccountStateRepository(s).update_status(
                    self.account_id, today, 'completed'
                )
            return

        # ============================================================
        # Общая проверка браузера (для обоих режимов)
        # ============================================================
        if self._iterations % 3 == 0:
            if not await self._check_browser_alive():
                ok = await self._recreate_browser_context()
                if not ok:
                    await self._interruptible_sleep(60, watch_pause=False)
                    return

        # ============================================================
        # MANUAL OVERRIDE: игнорируем расписание и паузы
        # ============================================================
        if state_manual_override:
            interval = random.uniform(34, 117)
            self.log.debug(f"manual_override активен, ждём {interval:.1f}s")
            await self._interruptible_sleep(interval, watch_pause=False)

            if await get_setting('responses_enabled', 'true') != 'true':
                await self._interruptible_sleep(30, watch_pause=False)
                return

            await self._do_response(daily_limit, today)
            return

        # ============================================================
        # ОБЫЧНЫЙ РЕЖИМ
        # ============================================================

        # --- До начала рабочего дня ---
        if current_time < self.work_start:
            sleep_seconds = (
                    datetime.combine(now.date(), self.work_start).replace(tzinfo=MSK_TZ) - now
            ).total_seconds()
            self.log.info(
                f"⏰ До начала рабочего дня {sleep_seconds / 60:.1f} мин "
                f"(старт в {self.work_start.strftime('%H:%M')})"
            )
            await self._interruptible_sleep(sleep_seconds, watch_pause=True)
            return

        # --- После конца рабочего дня / завершено ---
        if current_time >= self.work_end or state_status == 'completed':
            if state_status != 'completed':
                async with AsyncSessionLocal() as s:
                    await AccountStateRepository(s).mark_completed(self.account_id, today)
            tomorrow = now.date() + timedelta(days=1)
            next_start = datetime.combine(tomorrow, self.work_start).replace(tzinfo=MSK_TZ)
            sleep_seconds = (next_start - now).total_seconds()
            self.log.info(
                f"🌙 Конец рабочего дня. Следующий старт "
                f"{next_start.strftime('%d.%m %H:%M')} ({sleep_seconds / 3600:.1f} ч)"
            )
            await self._interruptible_sleep(sleep_seconds, watch_pause=True)
            return

        # --- Пауза по pause_until ---
        if state_status == 'paused' and state_pause_until:
            now_naive = now.replace(tzinfo=None)
            if state_pause_until > now_naive:
                sleep_seconds = (state_pause_until - now_naive).total_seconds()
                self.log.info(f"⏸️ Пауза до {state_pause_until} ({sleep_seconds / 60:.1f} мин)")
                await self._interruptible_sleep(sleep_seconds, watch_pause=True)
                async with AsyncSessionLocal() as s:
                    await AccountStateRepository(s).update_status(
                        self.account_id, today, 'active', pause_until=None
                    )
                return
            else:
                async with AsyncSessionLocal() as s:
                    await AccountStateRepository(s).update_status(
                        self.account_id, today, 'active', pause_until=None
                    )

        # --- Запланированные паузы из расписания ---
        in_pause, pause_end = self._check_pause(current_time, state_pauses)
        if in_pause:
            pause_end_dt = datetime.combine(now.date(), pause_end).replace(tzinfo=MSK_TZ)
            sleep_seconds = (pause_end_dt - now).total_seconds()
            if sleep_seconds <= 0:
                return
            self.log.info(
                f"☕ Запланированная пауза до {pause_end.strftime('%H:%M')} "
                f"({sleep_seconds / 60:.1f} мин)"
            )
            pause_until_naive = (now + timedelta(seconds=sleep_seconds)).replace(tzinfo=None)
            async with AsyncSessionLocal() as s:
                await AccountStateRepository(s).update_status(
                    self.account_id, today, 'paused', pause_until=pause_until_naive
                )
            await self._interruptible_sleep(sleep_seconds, watch_pause=True)
            async with AsyncSessionLocal() as s:
                await AccountStateRepository(s).update_status(
                    self.account_id, today, 'active', pause_until=None
                )
            return

        # --- Интервал до следующего отклика ---
        next_pause_start = self._next_pause_start(current_time, state_pauses)
        max_sleep = None
        if next_pause_start:
            next_start_dt = datetime.combine(now.date(), next_pause_start).replace(tzinfo=MSK_TZ)
            seconds_to_pause = (next_start_dt - now).total_seconds()
            max_sleep = max(0.0, seconds_to_pause - 60)

        min_interval = 34
        max_interval = 117
        upper_bound = min(max_interval, max_sleep) if max_sleep is not None else max_interval
        if upper_bound < min_interval:
            upper_bound = min_interval

        interval = random.uniform(min_interval, upper_bound)
        self.log.info(f"Waiting {interval:.1f}s before next response")
        await self._interruptible_sleep(interval, watch_pause=False)

        if await get_setting('responses_enabled', 'true') != 'true':
            await self._interruptible_sleep(30, watch_pause=False)
            return

        await self._do_response(daily_limit, today)

    def _check_pause(self, current_time: time, pauses: list) -> tuple[bool, Optional[time]]:
        for p in pauses:
            start = datetime.strptime(p['start'], "%H:%M").time()
            end = datetime.strptime(p['end'], "%H:%M").time()
            if start <= current_time < end:
                return True, end
        return False, None

    def _next_pause_start(self, current_time: time, pauses: list) -> Optional[time]:
        starts = [datetime.strptime(p['start'], "%H:%M").time() for p in pauses]
        future_starts = [t for t in starts if t > current_time]
        return min(future_starts) if future_starts else None

    # ---------- Логирование откликов ----------

    async def _log_response(self, vacancy_id: str, title: str, url: str,
                            status: str, error_text: str = ""):
        timestamp = msk_now().strftime("%Y-%m-%d %H:%M:%S")
        emoji = {
            "success": "✅",
            "wo_soprovod": "⚠️",
            "failed": "❌",
            "test_required": "🧪",
            "already_responded": "♻️",
            "other_country_skipped": "🌍",
            "auth_required": "🔐",
            "vacancy_archived": "📦",
        }.get(status, "❓")

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

    # ---------- Отклик ----------

    async def _do_response(self, daily_limit: int, today):
        """
        Отклик на одну вакансию.
        Использует короткие сессии: долгие операции (Playwright) — вне транзакций.
        """
        v_id = v_url = v_title = None

        # ============================================================
        # ТРАНЗАКЦИЯ 1: выбрать вакансию и пометить pending
        # ============================================================
        async with AsyncSessionLocal() as s1:
            vacancy_repo = VacancyRepository(s1)

            # include_junior/senior — из self.cfg, не из БД каждый раз
            vacancy = await vacancy_repo.get_random_unresponded(
                self.account_id,
                self.cfg.get("include_junior", True),
                self.cfg.get("include_senior", True),
                skip_test_required_by_others=settings.SKIP_TEST_REQUIRED_BY_OTHERS,
            )

            if vacancy is None:
                # выходим из сессии до sleep
                pass
            else:
                self.consecutive_failures = 0
                v_id = vacancy.hh_id
                v_url = vacancy.url
                v_title = vacancy.title
                # mark_responded — ВНУТРИ сессии
                await vacancy_repo.mark_responded(
                    self.account_id, v_id, status="pending", commit=True
                )

        if v_id is None:
            self.consecutive_failures += 1
            wait_min = 30 if self.consecutive_failures >= 5 else 10
            self.log.warning(
                f"🔍 Нет подходящих вакансий "
                f"(attempt {self.consecutive_failures}/5), следующая попытка через {wait_min} мин"
            )
            if self.consecutive_failures >= 5:
                await self._interruptible_sleep(1800, watch_pause=False)
                self.consecutive_failures = 0
            else:
                await self._interruptible_sleep(600, watch_pause=False)
            return

        # ============================================================
        # БЕЗ ТРАНЗАКЦИИ: письмо + Playwright
        # ============================================================
        tg = self.account.tg_username or settings.TG_USERNAME or ""
        if tg and not tg.startswith("@"):
            tg = f"@{tg}"
        context = {"vacancy_name": v_title, "tg_username": tg}
        message = generate_message(self.template, context)

        try:
            response_status = await self.response_service.respond_to_vacancy(
                v_url, self.resume_id, message, self.context
            )
        except Exception as e:
            self.log.exception("exception during response")
            async with AsyncSessionLocal() as s2:
                await ResponseErrorRepository(s2).add(
                    self.account_id, v_id, str(e), error_type='exception'
                )
                await VacancyRepository(s2).mark_responded(
                    self.account_id, v_id, status="failed", commit=True
                )
            await self._log_response(v_id, v_title, v_url, 'failed', str(e))
            return

        if response_status != 'auth_required':
            self.auth_failures = 0

        # ============================================================
        # ТРАНЗАКЦИЯ 2: записать результат
        # ============================================================
        async with AsyncSessionLocal() as s3:
            vacancy_repo = VacancyRepository(s3)
            error_repo = ResponseErrorRepository(s3)
            state_repo = AccountStateRepository(s3)

            # --- success / wo_soprovod ---
            if response_status in ('success', 'wo_soprovod'):
                await vacancy_repo.increment_used_count(v_id)
                new_done = await state_repo.increment_done(self.account_id, today)
                await vacancy_repo.mark_responded(
                    self.account_id, v_id, status=response_status, commit=True
                )
                label = ('✅ отклик с письмом' if response_status == 'success'
                         else '⚠️ отклик без письма')
                self.log.info(f"{label} на {v_id}, done {new_done}/{daily_limit}")
                await self._log_response(v_id, v_title, v_url, response_status)
                return

            # --- test_required ---
            if response_status == 'test_required':
                await vacancy_repo.mark_responded(
                    self.account_id, v_id, status="test_required", commit=True
                )
                self.log.info(f"🧪 вакансия {v_id} требует теста")
                await self._log_response(v_id, v_title, v_url, 'test_required',
                                         'тест от работодателя')
                return

            # --- already_responded ---
            if response_status == 'already_responded':
                await vacancy_repo.mark_responded(
                    self.account_id, v_id, status="already_responded", commit=True
                )
                self.log.info(f"♻️ вакансия {v_id} уже откликнута ранее")
                await self._log_response(v_id, v_title, v_url, 'already_responded',
                                         'отклик уже был сделан ранее')
                return

            # --- other_country_skipped ---
            if response_status == 'other_country_skipped':
                await vacancy_repo.mark_responded(
                    self.account_id, v_id, status="other_country_skipped", commit=True
                )
                self.log.info(f"🌍 вакансия {v_id} в другой стране — пропускаем")
                await self._log_response(v_id, v_title, v_url, 'other_country_skipped',
                                         'вакансия в другой стране')
                return

            # --- vacancy_archived ---
            if response_status == 'vacancy_archived':
                await vacancy_repo.mark_responded(
                    self.account_id, v_id, status="vacancy_archived", commit=True
                )
                from sqlalchemy import update as sa_update
                from app.db.models import Vacancy as V
                await s3.execute(
                    sa_update(V).where(V.hh_id == v_id).values(is_actual=False)
                )
                await s3.commit()
                self.log.info(f"📦 вакансия {v_id} в архиве — деактивируем")
                await self._log_response(v_id, v_title, v_url, 'vacancy_archived',
                                         'вакансия в архиве на hh.ru')
                return

            # --- failed ---
            if response_status == 'failed':
                await error_repo.add(
                    self.account_id, v_id,
                    "response_failed", error_type='response_failed',
                )
                await vacancy_repo.mark_responded(
                    self.account_id, v_id, status="failed", commit=True
                )
                self.log.warning(f"❌ вакансия {v_id}: неудача при отклике")
                await self._log_response(v_id, v_title, v_url, 'failed', '')
                return

            # --- auth_required ---
            if response_status == 'auth_required':
                await vacancy_repo.mark_responded(
                    self.account_id, v_id, status="auth_required", commit=True
                )
                await error_repo.add(
                    self.account_id, v_id,
                    "auth_required: cookies expired", error_type='auth',
                )
                self.log.warning(f"🔐 вакансия {v_id}: требуется авторизация на hh.ru")
                await self._log_response(v_id, v_title, v_url, 'auth_required',
                                         'требуется авторизация hh.ru')
                self.auth_failures += 1
                if self.auth_failures >= 3:
                    self.log.error(
                        "🔐 Cookies устарели — 3 подряд auth_required. Пауза 30 минут."
                    )
                    self.auth_failures = 0
                    await self._interruptible_sleep(1800, watch_pause=False)
                return

            # --- unknown ---
            await error_repo.add(
                self.account_id, v_id,
                f"Unknown response status: {response_status}",
                error_type='unknown',
            )
            await vacancy_repo.mark_responded(
                self.account_id, v_id, status="failed", commit=True
            )
            await self._log_response(v_id, v_title, v_url, 'failed',
                                     f'Unknown status: {response_status}')

    async def stop(self):
        self.stop_event.set()
