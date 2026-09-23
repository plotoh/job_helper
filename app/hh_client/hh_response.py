# app/hh_client/hh_response.py
import asyncio
import logging
from datetime import datetime
from pathlib import Path

from playwright.async_api import BrowserContext

from app.config import settings
from app.hh_client.interfaces import IResponseService

logger = logging.getLogger(__name__)

# === Селекторы ===
RESPOND_BUTTON_SELECTORS = [
    'a[data-qa="vacancy-response-link-top"]',
    'button[data-qa="vacancy-response-link-top"]',
    'a[data-qa="vacancy-response-link-bottom"]',
    'button[data-qa="vacancy-response-link-bottom"]',
]

ATTACH_LETTER_SELECTOR = '[data-qa="responded-success-attach-cover-letter"]'

TEST_INDICATORS = [
    '[data-qa="employer-asking-for-test"]',
    '[data-qa="task-question"]',
    '[data-qa="task-body"]',
    '[data-qa="test-description"]',
]

LETTER_FIELD_SELECTORS = [
    'textarea[data-qa="vacancy-response-popup-form-letter-input"]',
    'textarea[data-qa="vacancy-response-letter"]',
]

SUBMIT_POPUP_SELECTORS = [
    'button[data-qa="vacancy-response-submit-popup"]',
    'button[data-qa="vacancy-response-letter-submit"]',
]

SUCCESS_SELECTORS = [
    'div[data-qa="vacancy-response-success"]',
]

SUCCESS_TEXT_PATTERNS = [
    r'резюме доставлено',
    r'отклик отправлен',
    r'вы откликнулись',
]

# Модалка «вакансия в другой стране»
OTHER_COUNTRY_MODAL_SELECTORS = [
    '[data-qa="relocation-warning"]',
    '[data-qa="vacancy-response-relocation-warning"]',
    '[data-qa="other-country-warning"]',
]

OTHER_COUNTRY_CONFIRM_SELECTORS = [
    '[data-qa="relocation-warning-submit"]',
    '[data-qa="apply-anyway"]',
    'button:has-text("Всё равно откликнуться")',
    'button:has-text("Все равно откликнуться")',
]

OTHER_COUNTRY_CANCEL_SELECTORS = [
    'button:has-text("Отменить")',
    'button:has-text("Отмена")',
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

    async def _close_overlays(self, page):
        """Скрывает плавающие оверлеи (виджет чата и т.п.), мешающие кликам."""
        for sel in [
            '[data-qa="chatik-activator-navi-item"]',
            '[data-qa="chatik-activator"]',
            '.chatik-activator-navi-item',
        ]:
            try:
                loc = page.locator(sel)
                if await loc.count() > 0 and await loc.first.is_visible():
                    await page.evaluate(
                        "sel => document.querySelectorAll(sel).forEach(e => e.style.display='none')",
                        sel,
                    )
                    logger.debug(f"Скрыли оверлей: {sel}")
            except Exception:
                pass

    async def _is_logged_in(self, page) -> bool:
        """
        Проверяет, залогинен ли пользователь.
        True  — явные признаки авторизации.
        False — явные признаки гостя (форма логина/регистрации).
        """
        # Явные маркеры гостя
        for sel in [
            'form[data-qa="auth-form"]',
            'input[data-qa="login"]',
            'form[data-qa="account-signup-email"]',
        ]:
            try:
                loc = page.locator(sel)
                if await loc.count() > 0 and await loc.first.is_visible():
                    logger.debug(f"🔍 Признак гостя: {sel}")
                    return False
            except Exception:
                continue

        # Явные маркеры авторизованного
        for sel in [
            '[data-qa="profileAndResumes-button"]',
            '[data-qa="applicantProfileDesktopDrop-button"]',
            '[data-qa="vacancyResponses-button"]',
        ]:
            try:
                if await page.locator(sel).count() > 0:
                    return True
            except Exception:
                continue

        # Не удалось определить — считаем залогиненным,
        # чтобы не блокировать работу при изменении вёрстки hh.ru
        logger.debug("🤔 Не удалось определить статус авторизации")
        return True

    async def _safe_click(self, locator, label: str = "element") -> bool:
        """Пробует 4 стратегии клика. True, если хоть одна сработала."""
        try:
            await locator.click(timeout=5000)
            logger.info(f"✅ Обычный клик по {label}")
            return True
        except Exception as e:
            logger.debug(f"Обычный клик по {label} не сработал: {e}")

        try:
            await locator.click(force=True, timeout=5000)
            logger.warning(f"⚠️ force=True клик по {label}")
            return True
        except Exception as e:
            logger.debug(f"force-клик по {label} не сработал: {e}")

        try:
            await locator.dispatch_event('click')
            logger.warning(f"⚠️ dispatch_event('click') по {label}")
            return True
        except Exception as e:
            logger.debug(f"dispatch_event по {label} не сработал: {e}")

        try:
            await locator.evaluate("el => el.click()")
            logger.warning(f"⚠️ JS .click() по {label}")
            return True
        except Exception as e:
            logger.debug(f"JS .click() по {label} не сработал: {e}")

        return False

    async def _dump_page_debug(self, page, tag: str):
        debug_dir = Path("logs/debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        try:
            # НЕ full_page — быстрее и не висит на анимациях
            await page.screenshot(
                path=str(debug_dir / f"{ts}_{tag}.png"),
                full_page=False,
                timeout=5000,
            )
        except Exception as e:
            logger.warning(f"Screenshot не сохранился: {e}")

        try:
            html = await page.content()
            (debug_dir / f"{ts}_{tag}.html").write_text(html, encoding="utf-8")
            logger.info(f"🖼️ Debug сохранён: {ts}_{tag}")
        except Exception as e:
            logger.warning(f"Не удалось сохранить debug: {e}")

    # async def _dump_page_debug(self, page, tag: str):
    #     debug_dir = Path("logs/debug")
    #     debug_dir.mkdir(parents=True, exist_ok=True)
    #     ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    #     try:
    #         await page.screenshot(path=str(debug_dir / f"{ts}_{tag}.png"), full_page=True)
    #         html = await page.content()
    #         (debug_dir / f"{ts}_{tag}.html").write_text(html, encoding="utf-8")
    #         logger.info(f"🖼️ Debug сохранён: {ts}_{tag}")
    #     except Exception as e:
    #         logger.warning(f"Не удалось сохранить debug: {e}")
    #         return
    #
    #     # Выведем все data-qa на странице — помогает понять, что не так
    #     try:
    #         qa_values = await page.eval_on_selector_all(
    #             "[data-qa]", "els => els.map(e => e.getAttribute('data-qa'))"
    #         )
    #         uniq = sorted(set(v for v in qa_values if v))
    #         logger.warning(f"🔍 data-qa на странице ({tag}): {uniq}")
    #     except Exception as e:
    #         logger.warning(f"Не удалось собрать data-qa: {e}")
    #
    #     # Ключевые фразы из текста — помогает понять сценарий
    #     try:
    #         body_text = await page.locator("body").inner_text(timeout=3000)
    #         # только первые 400 символов + ищем ключевые маркеры
    #         markers = []
    #         for kw in ("отклик", "резюме", "тест", "вопрос", "ошибка", "капча",
    #                    "войти", "авториз", "уже отклик", "заполните", "обязательн"):
    #             if kw.lower() in body_text.lower():
    #                 markers.append(kw)
    #         logger.warning(f"🔍 Ключевые слова на странице: {markers}")
    #         logger.debug(f"🔍 Первые 400 символов текста: {body_text[:400]!r}")
    #     except Exception:
    #         pass

    # ---------- Детекторы ----------

    async def _is_already_responded(self, page) -> bool:
        if await page.locator(ATTACH_LETTER_SELECTOR).count() > 0:
            return True
        if await page.locator('[data-qa="vacancy-response-link-view-topic"]').count() > 0:
            return True
        for pattern in (r'вы откликнулись', r'резюме доставлено'):
            try:
                if await page.locator(f'text=/{pattern}/i').count() > 0:
                    return True
            except Exception:
                continue
        return False

    async def _is_test_modal(self, page) -> bool:
        for sel in TEST_INDICATORS:
            try:
                loc = page.locator(sel)
                if await loc.count() > 0 and await loc.first.is_visible():
                    logger.debug(f"🧪 Тест-индикатор: {sel}")
                    return True
            except Exception:
                continue
        return False

    async def _is_other_country_modal(self, page) -> bool:
        """Модалка 'вакансия в другой стране'. Только по data-qa — без медленного regex."""
        for sel in OTHER_COUNTRY_MODAL_SELECTORS:
            try:
                loc = page.locator(sel)
                if await loc.count() > 0 and await loc.first.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _is_vacancy_archived(self, page) -> bool:
        """Вакансия в архиве — отклик невозможен."""
        for sel in [
            '[data-qa="vacancy-title-archived-text"]',
            '[data-qa="vacancy-archive-description"]',
            '[data-qa="vacancy-view-vacancies-from-search"]',
        ]:
            try:
                if await page.locator(sel).count() > 0:
                    return True
            except Exception:
                continue
        # Fallback по тексту
        try:
            if await page.locator('text=/Вакансия в архиве/i').count() > 0:
                return True
        except Exception:
            pass
        return False

    async def _is_login_required(self, page) -> bool:
        """HH показывает форму логина — сессия для этого запроса недействительна."""
        for sel in [
            '[data-qa="account-login-form"]',
            '[data-qa="applicant-login-card"]',
            '[data-qa="applicant-login-input-email"]',
            '[data-qa="expand-login-by-password"]',
        ]:
            try:
                loc = page.locator(sel)
                if await loc.count() > 0 and await loc.first.is_visible():
                    logger.debug(f"🔐 Признак логина: {sel}")
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
        for sel in LETTER_FIELD_SELECTORS:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    logger.info(f"✅ Поле письма найдено: {sel}")
                    return loc
            except Exception:
                continue
        return None

    async def _check_iframe(self, page):
        iframe = page.locator(
            'iframe[src*="vacancy-response"], iframe[src*="response"]'
        ).first
        try:
            if await iframe.count() > 0:
                return await iframe.content_frame()
        except Exception:
            pass
        return None

    # ---------- Обработка модалки «другая страна» ----------

    async def _handle_other_country_modal(self, page) -> str:
        """'not_found' | 'proceed' | 'skipped'."""
        if not await self._is_other_country_modal(page):
            return 'not_found'

        logger.warning("🌍 Модалка 'другая страна'")
        await self._close_overlays(page)

        if not settings.RESPOND_TO_OTHER_COUNTRY:
            cancel_btn = None
            for sel in OTHER_COUNTRY_CANCEL_SELECTORS:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    cancel_btn = loc
                    break
            if cancel_btn:
                await self._safe_click(cancel_btn, "«Отменить» (другая страна)")
            return 'skipped'

        confirm_btn = None
        for sel in OTHER_COUNTRY_CONFIRM_SELECTORS:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                confirm_btn = loc
                logger.info(f"✅ Кнопка 'Всё равно откликнуться': {sel}")
                break

        if not confirm_btn:
            logger.error("❌ Кнопка подтверждения не найдена")
            await self._dump_page_debug(page, "other_country_no_confirm")
            return 'skipped'

        ok = await self._safe_click(confirm_btn, "«Всё равно откликнуться»")
        if not ok:
            return 'skipped'

        await asyncio.sleep(2)
        return 'proceed'

    # ---------- Прикладывание письма ----------

    async def _attach_cover_letter(self, page, message: str) -> str:
        attach_btn = page.locator(ATTACH_LETTER_SELECTOR).first
        if await attach_btn.count() == 0:
            logger.error("❌ Кнопка 'Приложить сопроводительное' не найдена")
            return 'failed'

        await self._close_overlays(page)
        try:
            await attach_btn.scroll_into_view_if_needed(timeout=5000)
        except Exception:
            pass

        logger.info("🖱️ Кликаем 'Приложить сопроводительное письмо'")
        clicked = await self._safe_click(attach_btn, "«Приложить сопроводительное»")
        if not clicked:
            await self._dump_page_debug(page, "attach_click_failed")
            return 'wo_soprovod'

        try:
            await page.wait_for_selector(
                '[data-qa="vacancy-response-popup-form-letter-input"], '
                'textarea[data-qa="vacancy-response-letter"]',
                state="visible",
                timeout=10000,
            )
        except Exception:
            pass

        if await self._is_test_modal(page):
            return 'test_required'

        frame = await self._check_iframe(page)
        page_for_field = frame if frame else page

        letter_field = await self._find_letter_field(page_for_field)
        if not letter_field:
            await self._dump_page_debug(page, "attach_no_field")
            return 'wo_soprovod'

        try:
            await letter_field.click()
            await asyncio.sleep(0.3)
            await letter_field.fill(message)
            logger.info("📨 Письмо вставлено")
        except Exception as e:
            logger.error(f"❌ Не удалось заполнить поле: {e}")
            return 'wo_soprovod'

        submit_button = None
        for sel in SUBMIT_POPUP_SELECTORS:
            btn = page.locator(sel).first
            if await btn.count() > 0 and await btn.is_visible():
                submit_button = btn
                break

        if not submit_button:
            return 'wo_soprovod'

        await self._safe_click(submit_button, "«Отправить письмо»")
        await asyncio.sleep(3)

        if await self._is_test_modal(page):
            return 'test_required'
        if await self._has_success_marker(page):
            return 'success'
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

            # --- Проверка авторизации ---
            if not await self._is_logged_in(page):
                logger.error("❌ Пользователь НЕ авторизован (cookies устарели?)")
                await self._dump_page_debug(page, "not_logged_in")
                return 'auth_required'
            logger.info("🔬 [diag] авторизация OK")

            # --- Уже откликались ---
            if await page.locator(ATTACH_LETTER_SELECTOR).count() > 0:
                logger.info("🔬 [diag] есть кнопка 'Приложить' → _attach_cover_letter")
                return await self._attach_cover_letter(page, message)
            logger.info("🔬 [diag] 'Приложить' нет")

            # --- Архив ---
            if await self._is_vacancy_archived(page):
                logger.info("📦 Вакансия в архиве — пропускаем")
                return 'vacancy_archived'
            logger.info("🔬 [diag] архив: нет")

            # --- Требует логин (форма входа на странице) ---
            if await self._is_login_required(page):
                logger.warning("🔐 HH запросил авторизацию — пропускаем вакансию")
                await self._dump_page_debug(page, "login_required")
                return 'auth_required'
            logger.info("🔬 [diag] login-form: нет")

            # --- Ищем кнопку отклика ---
            respond_button = None
            for sel in RESPOND_BUTTON_SELECTORS:
                btn = page.locator(sel).first
                try:
                    if await btn.count() > 0:
                        respond_button = btn
                        logger.info(f"✅ Кнопка отклика: {sel}")
                        break
                except Exception as e:
                    logger.debug(f"ошибка count для {sel}: {e}")
                    continue

            if not respond_button:
                if await self._is_already_responded(page):
                    if await page.locator(ATTACH_LETTER_SELECTOR).count() > 0:
                        return await self._attach_cover_letter(page, message)
                    return 'already_responded'
                logger.warning("❌ Кнопка отклика не найдена")
                await self._dump_page_debug(page, "no_respond_button")
                return 'failed'

            # --- КЛИК с явным таймаутом ---
            logger.info("🔬 [diag] кликаем по кнопке отклика (timeout 15s)...")
            try:
                await respond_button.click(timeout=15000)
                logger.info("🔬 [diag] клик завершён")
            except Exception as e:
                logger.error(f"❌ Клик не удался: {e}")
                await self._dump_page_debug(page, "respond_click_failed")
                return 'failed'

            await asyncio.sleep(3)
            logger.info("🔬 [diag] sleep(3) прошёл, начинаем разбор модалок")

            # --- Тест ---
            logger.info("🔬 [diag] → _is_test_modal")
            try:
                is_test = await asyncio.wait_for(
                    self._is_test_modal(page), timeout=10
                )
            except asyncio.TimeoutError:
                logger.error("⏱️ Таймаут 'тест-модалка' (>10s)")
                await self._dump_page_debug(page, "timeout_test_modal")
                return 'failed'
            logger.info(f"🔬 [diag] тест: {is_test}")

            if is_test:
                await self._dump_page_debug(page, "test_modal")
                return 'test_required'

            # # --- Модалка «другая страна» ---
            # logger.info("🔬 [diag] → _handle_other_country_modal")
            # try:
            #     country_result = await asyncio.wait_for(
            #         self._handle_other_country_modal(page), timeout=15
            #     )
            # except asyncio.TimeoutError:
            #     logger.error("⏱️ Таймаут 'другая страна' (>15s)")
            #     await self._dump_page_debug(page, "timeout_country_modal")
            #     return 'failed'
            # logger.info(f"🔬 [diag] 'другая страна': {country_result}")
            #
            # if country_result == 'skipped':
            #     return 'other_country_skipped'
            # if country_result == 'proceed':
            #     await asyncio.sleep(2)

            # --- Отклик без модалки → "Приложить письмо" ---
            logger.info("🔬 [diag] проверяем 'Приложить' после клика")
            has_attach = await page.locator(ATTACH_LETTER_SELECTOR).count() > 0
            logger.info(f"🔬 [diag] attach: {has_attach}")
            if has_attach:
                return await self._attach_cover_letter(page, message)

            # --- Модалка success ---
            logger.info("🔬 [diag] проверяем success-модалку")
            has_success = await page.locator(SUCCESS_SELECTORS[0]).count() > 0
            logger.info(f"🔬 [diag] success: {has_success}")
            if has_success:
                if await page.locator(ATTACH_LETTER_SELECTOR).count() > 0:
                    return await self._attach_cover_letter(page, message)
                return 'success'

            # --- Обычная форма с полем письма ---
            logger.info("🔬 [diag] → _check_iframe")
            frame = await self._check_iframe(page)
            page_for_field = frame if frame else page

            logger.info("🔬 [diag] → _find_letter_field")
            letter_field = await self._find_letter_field(page_for_field)
            logger.info(f"🔬 [diag] letter_field: {letter_field is not None}")

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
                if await self._is_test_modal(page):
                    return 'test_required'
                if await self._has_success_marker(page):
                    return 'wo_soprovod'
                logger.warning("❌ Ни поля письма, ни признаков успеха")
                await self._dump_page_debug(page, "no_field_no_success")
                return 'failed'

            # --- Заполняем письмо ---
            logger.info("🔬 [diag] заполняем поле письма")
            try:
                await letter_field.click(timeout=5000)
                await asyncio.sleep(0.5)
                await letter_field.fill(message, timeout=10000)
                logger.info("📨 Письмо вставлено")
            except Exception as e:
                logger.error(f"❌ Не удалось заполнить поле: {e}")
                return 'failed'

            # --- Кнопка submit ---
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
                logger.warning("❌ Кнопка отправки не найдена")
                return 'failed'

            logger.info("🔬 [diag] клик отправки")
            await submit_button.click(timeout=10000)
            await asyncio.sleep(3)

            if await self._is_test_modal(page):
                return 'test_required'
            if await self._has_success_marker(page):
                return 'success'
            if await page.locator('div[data-qa="vacancy-response-error"]').count() > 0:
                return 'failed'
            await self._dump_page_debug(page, "unclear_result")
            return 'failed'

        except Exception as e:
            logger.error(f"💥 Ошибка при отклике на {vacancy_url}: {e}")
            return 'failed'
        finally:
            await page.close()
