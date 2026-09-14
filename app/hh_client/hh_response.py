import asyncio
import logging
from datetime import datetime
from pathlib import Path
from playwright.async_api import BrowserContext

from app.hh_client.interfaces import IResponseService

logger = logging.getLogger(__name__)

# === Селекторы (проверены по реальному HTML hh.ru) ===
RESPOND_BUTTON_SELECTORS = [
    'a[data-qa="vacancy-response-link-top"]',
    'button[data-qa="vacancy-response-link-top"]',
    'a[data-qa="vacancy-response-link-bottom"]',
    'button[data-qa="vacancy-response-link-bottom"]',
]

# Кнопка "Приложить сопроводительное письмо" (после простого отклика)
ATTACH_LETTER_SELECTOR = '[data-qa="responded-success-attach-cover-letter"]'

# Индикаторы теста (любой из них => это тест, а не обычный отклик)
TEST_INDICATORS = [
    '[data-qa="employer-asking-for-test"]',
    '[data-qa="task-question"]',
    '[data-qa="task-body"]',
    '[data-qa="test-description"]',
]

# Точное поле сопроводительного письма
LETTER_FIELD_SELECTORS = [
    'textarea[data-qa="vacancy-response-popup-form-letter-input"]',
    'textarea[data-qa="vacancy-response-letter"]',
]

# Точная кнопка submit в попапе отклика
SUBMIT_POPUP_SELECTORS = [
    'button[data-qa="vacancy-response-submit-popup"]',
    'button[data-qa="vacancy-response-letter-submit"]',
]

# Общие селекторы успеха
SUCCESS_SELECTORS = [
    'div[data-qa="vacancy-response-success"]',
]

# Текстовые маркеры
SUCCESS_TEXT_PATTERNS = [
    r'резюме доставлено',
    r'отклик отправлен',
    r'вы откликнулись',
]


class HHResponseService(IResponseService):

    # ---------- Служебные ----------

    async def _handle_dialogs(self, page):
        try:
            region_confirm = page.locator('button[data-qa="confirm-phone-region"]')
            if await region_confirm.count() > 0:
                await region_confirm.click()
                await asyncio.sleep(1)
            cookie_agree = page.locator('button[data-qa="cookies-policy-agree"]')
            if await cookie_agree.count() > 0:
                await cookie_agree.click()
                await asyncio.sleep(1)
        except Exception:
            pass

    async def _dump_page_debug(self, page, tag: str):
        debug_dir = Path("logs/debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        screenshot_path = debug_dir / f"{ts}_{tag}.png"
        html_path = debug_dir / f"{ts}_{tag}.html"
        try:
            await page.screenshot(path=str(screenshot_path), full_page=True)
            html = await page.content()
            html_path.write_text(html, encoding="utf-8")
            logger.info(f"🖼️ Debug сохранён: {screenshot_path.name}")
        except Exception as e:
            logger.warning(f"Не удалось сохранить debug: {e}")

    # ---------- Детекторы ----------
    async def _is_already_responded(self, page) -> bool:
        """
        Признаки того, что на эту вакансию уже был отклик (текущим аккаунтом).
        """
        # Самый надёжный — кнопка "Приложить сопроводительное письмо"
        if await page.locator(ATTACH_LETTER_SELECTOR).count() > 0:
            return True
        # Кнопка "Чат" в блоке отклика — она появляется только после отклика
        if await page.locator('[data-qa="vacancy-response-link-view-topic"]').count() > 0:
            return True
        # Текстовые маркеры
        for pattern in (r'вы откликнулись', r'резюме доставлено'):
            try:
                if await page.locator(f'text=/{pattern}/i').count() > 0:
                    return True
            except Exception:
                continue
        return False

    async def _is_test_modal(self, page) -> bool:
        """
        Определяет, что открылась модалка с тестом.
        Проверяем ТОЛЬКО по специфичным data-qa (не по общим textarea!).
        """
        for sel in TEST_INDICATORS:
            try:
                loc = page.locator(sel)
                if await loc.count() > 0:
                    if await loc.first.is_visible():
                        logger.debug(f"🧪 Тест-индикатор: {sel}")
                        return True
            except Exception:
                continue
        return False

    async def _has_success_marker(self, page) -> bool:
        for sel in SUCCESS_SELECTORS:
            try:
                if await page.locator(sel).count() > 0:
                    return True
            except Exception:
                continue
        try:
            if await page.locator(ATTACH_LETTER_SELECTOR).count() > 0:
                return True
        except Exception:
            pass
        for pattern in SUCCESS_TEXT_PATTERNS:
            try:
                if await page.locator(f'text=/{pattern}/i').count() > 0:
                    return True
            except Exception:
                continue
        return False

    async def _find_letter_field(self, page):
        """
        Ищем ИМЕННО поле сопроводительного письма.
        Не трогаем поля вопросов теста.
        """
        for sel in LETTER_FIELD_SELECTORS:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    logger.info(f"✅ Поле письма найдено: {sel}")
                    return loc
            except Exception:
                continue
        logger.warning("⚠️ Поле письма не найдено по точным селекторам")
        return None

    async def _check_iframe(self, page):
        iframe = page.locator('iframe[src*="vacancy-response"], iframe[src*="response"]').first
        try:
            if await iframe.count() > 0:
                logger.info("📦 Форма в iframe, переключаемся...")
                return await iframe.content_frame()
        except Exception:
            pass
        return None

    # ---------- Прикладывание письма (после простого отклика) ----------

    async def _attach_cover_letter(self, page, message: str) -> str:
        attach_btn = page.locator(ATTACH_LETTER_SELECTOR).first
        if await attach_btn.count() == 0:
            logger.error("❌ Кнопка 'Приложить сопроводительное' не найдена")
            return 'failed'

        logger.info("🖱️ Кликаем 'Приложить сопроводительное письмо'")
        await attach_btn.click(no_wait_after=True)
        await asyncio.sleep(2)

        if await self._is_test_modal(page):
            logger.warning("🧪 После 'Приложить письмо' всплыл тест")
            return 'test_required'

        frame = await self._check_iframe(page)
        page_for_field = frame if frame else page

        letter_field = await self._find_letter_field(page_for_field)
        if not letter_field:
            await self._dump_page_debug(page, "attach_no_field")
            return 'failed'

        try:
            await letter_field.click()
            await asyncio.sleep(0.3)
            await letter_field.fill(message)
            logger.info("📨 Письмо вставлено")
        except Exception as e:
            logger.error(f"❌ Не удалось заполнить поле: {e}")
            return 'failed'

        submit_button = None
        for sel in SUBMIT_POPUP_SELECTORS:
            btn = page.locator(sel).first
            if await btn.count() > 0 and await btn.is_visible():
                submit_button = btn
                logger.info(f"✅ Кнопка отправки: {sel}")
                break

        if not submit_button:
            logger.error("❌ Кнопка отправки не найдена")
            return 'failed'

        await submit_button.click()
        await asyncio.sleep(3)

        if await self._is_test_modal(page):
            return 'test_required'
        if await self._has_success_marker(page):
            logger.info("🎉 Письмо приложено, отклик подтверждён")
            return 'success'
        logger.warning("⚠️ Явного маркера успеха нет, но submit прошёл")
        return 'success'

    # ---------- Основной метод ----------

    async def respond_to_vacancy(
            self, vacancy_url: str, resume_id: str, message: str, context: BrowserContext
    ) -> str:
        page = await context.new_page()
        try:
            logger.info(f"➡️ Переход: {vacancy_url}")
            await page.goto(vacancy_url, wait_until="domcontentloaded", timeout=60000)
            await self._handle_dialogs(page)
            await asyncio.sleep(1)

            # --- СЦЕНАРИЙ A: уже откликались ранее ---
            if await page.locator(ATTACH_LETTER_SELECTOR).count() > 0:
                logger.info("ℹ️ Уже откликались — прикладываем письмо")
                return await self._attach_cover_letter(page, message)

            # --- СЦЕНАРИЙ B: кликаем по кнопке отклика ---
            respond_button = None
            for sel in RESPOND_BUTTON_SELECTORS:
                btn = page.locator(sel).first
                try:
                    if await btn.count() > 0:
                        respond_button = btn
                        logger.info(f"✅ Кнопка отклика: {sel}")
                        break
                except Exception:
                    continue

            if not respond_button:
                # Может, уже откликались? Кнопки "Откликнуться" в этом случае нет.
                if await self._is_already_responded(page):
                    logger.info("ℹ️ Кнопки отклика нет, но есть признаки уже сделанного отклика")
                    # Если есть куда приложить письмо — прикладываем
                    if await page.locator(ATTACH_LETTER_SELECTOR).count() > 0:
                        return await self._attach_cover_letter(page, message)
                    return 'already_responded'

                logger.warning("❌ Кнопка отклика не найдена и признаков отклика нет")
                await self._dump_page_debug(page, "no_respond_button")
                return 'failed'

            await respond_button.click(no_wait_after=True)
            await asyncio.sleep(3)

            # --- ГЛАВНАЯ ПРОВЕРКА: тест? ---
            if await self._is_test_modal(page):
                logger.warning("🧪 Открылся тест — отклик НЕ отправлен, возвращаем test_required")
                await self._dump_page_debug(page, "test_modal")
                return 'test_required'

            # --- Отклик ушёл без модалки → "Приложить письмо" ---
            if await page.locator(ATTACH_LETTER_SELECTOR).count() > 0:
                logger.info("ℹ️ Отклик ушёл без модалки — прикладываем письмо")
                return await self._attach_cover_letter(page, message)

            # --- Модалка success (уже откликались) ---
            if await page.locator(SUCCESS_SELECTORS[0]).count() > 0:
                logger.info("ℹ️ Модалка success")
                if await page.locator(ATTACH_LETTER_SELECTOR).count() > 0:
                    return await self._attach_cover_letter(page, message)
                return 'success'

            # --- СЦЕНАРИЙ C: обычная форма с полем письма ---
            frame = await self._check_iframe(page)
            page_for_field = frame if frame else page

            letter_field = await self._find_letter_field(page_for_field)

            # Если поля нет — может, выбор резюме
            if not letter_field:
                if await page.locator('div[data-qa="resume-selector"]').count() > 0:
                    logger.info("🔍 Выбор резюме...")
                    resume_radio = page.locator(f'input[type="radio"][value="{resume_id}"]')
                    if await resume_radio.count() > 0:
                        await resume_radio.click()
                    else:
                        first_radio = page.locator('input[type="radio"][name="resume"]').first
                        if await first_radio.count() > 0:
                            await first_radio.click()
                    await asyncio.sleep(1)
                    letter_field = await self._find_letter_field(page_for_field)

            if not letter_field:
                # Финальные проверки
                if await self._is_test_modal(page):
                    logger.warning("🧪 Обнаружен тест")
                    return 'test_required'
                if await self._has_success_marker(page):
                    logger.info("ℹ️ Отклик отправлен без письма")
                    return 'wo_soprovod'
                logger.error("❌ Ни поля письма, ни признаков успеха")
                await self._dump_page_debug(page, "no_field_no_success")
                return 'failed'

            # Заполняем письмо
            try:
                await letter_field.click()
                await asyncio.sleep(0.5)
                await letter_field.fill(message)
                logger.info("📨 Письмо вставлено")
            except Exception as e:
                logger.error(f"❌ Не удалось заполнить поле: {e}")
                return 'failed'

            # Кнопка отправки
            submit_button = None
            for sel in SUBMIT_POPUP_SELECTORS:
                btn = page.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    submit_button = btn
                    logger.info(f"✅ Кнопка отправки: {sel}")
                    break

            if not submit_button:
                if await self._has_success_marker(page):
                    return 'success'
                logger.error("❌ Кнопка отправки не найдена")
                return 'failed'

            await submit_button.click()
            await asyncio.sleep(3)

            # --- ПРОВЕРКИ ПОСЛЕ SUBMIT ---
            if await self._is_test_modal(page):
                logger.warning("🧪 После submit всплыл тест — отклик НЕ отправлен")
                await self._dump_page_debug(page, "test_after_submit")
                return 'test_required'

            if await self._has_success_marker(page):
                logger.info("🎉 Отклик подтверждён")
                return 'success'

            if await page.locator('div[data-qa="vacancy-response-error"]').count() > 0:
                logger.error("❌ HH показал ошибку отклика")
                return 'failed'

            logger.error("❌ Ни успеха, ни теста, ни ошибки — неясный результат")
            await self._dump_page_debug(page, "unclear_result")
            return 'failed'

        except Exception as e:
            logger.error(f"💥 Ошибка при отклике на {vacancy_url}: {e}")
            return 'failed'
        finally:
            await page.close()
