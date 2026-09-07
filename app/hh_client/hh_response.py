import asyncio
import logging
from playwright.async_api import BrowserContext, TimeoutError as PlaywrightTimeoutError
from app.hh_client.interfaces import IResponseService

logger = logging.getLogger(__name__)


class HHResponseService(IResponseService):
    async def _handle_dialogs(self, page):
        """Закрывает всплывающие окна (регион, куки), которые могут помешать отклику."""
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

    async def _find_message_field(self, page):
        """Универсальный поиск поля ввода сопроводительного письма."""
        possible_selectors = [
            'textarea[name="text"]',
            'textarea[data-qa="vacancy-response-letter"]',
            'div[data-qa="vacancy-response-letter"] textarea',
            'form textarea',
            'div[class*="letter"] textarea',
            'textarea'  # последний шанс – любой textarea на странице
        ]
        for selector in possible_selectors:
            field = page.locator(selector).first
            if await field.count() > 0 and await field.is_visible():
                logger.info(f"✅ Поле ввода найдено по селектору: {selector}")
                return field
        return None

    async def _check_iframe(self, page):
        """Проверяет, не открылась ли форма отклика в iframe."""
        iframe = page.locator('iframe[src*="vacancy-response"], iframe[src*="response"]').first
        if await iframe.count() > 0:
            logger.info("📦 Форма отклика в iframe, переключаемся...")
            frame = await iframe.content_frame()
            return frame
        return None

    async def respond_to_vacancy(
            self, vacancy_url: str, resume_id: str, message: str, context: BrowserContext
    ) -> str:
        """
        Возвращает:
        - 'success' – отклик отправлен с письмом
        - 'wo_soporovod' – отклик отправлен, но письмо не вставлено
        - 'failed' – ошибка (кнопка не найдена, форма не открылась и т.п.)
        """
        page = await context.new_page()
        try:
            logger.info(f"➡️ Переходим на страницу вакансии: {vacancy_url}")
            await page.goto(vacancy_url, wait_until="domcontentloaded", timeout=60000)
            await self._handle_dialogs(page)

            logger.info("🔍 Ищем кнопку отклика...")
            respond_button = page.locator('a[data-qa="vacancy-response-link-top"]').first
            if await respond_button.count() == 0:
                respond_button = page.locator('a[data-qa="vacancy-response-link-bottom"]').first
            if await respond_button.count() == 0:
                logger.warning(f"❌ Кнопка отклика не найдена на {vacancy_url}")
                return 'failed'

            logger.info("✅ Кнопка найдена, кликаем...")
            await respond_button.click(no_wait_after=True)
            logger.info("✅ Кнопка нажата, ожидаем появления поля ввода...")

            # Проверяем iframe
            frame = await self._check_iframe(page)
            page_for_field = frame if frame else page

            await asyncio.sleep(2)
            message_field = await self._find_message_field(page_for_field)

            # Если поле не появилось, проверяем, не отправился ли отклик без письма
            if not message_field:
                success_selector = 'div[data-qa="vacancy-response-success"]'
                if await page.locator(success_selector).count() > 0:
                    logger.info("ℹ️ Отклик отправлен без сопроводительного письма.")
                    return 'wo_soporovod'

                # Возможно, нужно выбрать резюме
                resume_selector = 'div[data-qa="resume-selector"]'
                if await page.locator(resume_selector).count() > 0:
                    logger.info("🔍 Появился выбор резюме, ищем нужное...")
                    resume_radio = page.locator(f'input[type="radio"][value="{resume_id}"]')
                    if await resume_radio.count() > 0:
                        await resume_radio.click()
                    else:
                        first_radio = page.locator('input[type="radio"][name="resume"]').first
                        if await first_radio.count() > 0:
                            await first_radio.click()
                        else:
                            logger.error("❌ Нет доступных резюме для выбора")
                            return 'failed'

                    await asyncio.sleep(1)
                    message_field = await self._find_message_field(page_for_field)
                    if not message_field:
                        if await page.locator(success_selector).count() > 0:
                            logger.info("ℹ️ Отклик отправлен без сопроводительного письма.")
                            return 'wo_soporovod'
                        else:
                            logger.error("❌ После выбора резюме поле не появилось.")
                            return 'failed'
                else:
                    # Проверяем, есть ли кнопка отправки без поля
                    submit_btn = page.locator('button[data-qa="vacancy-response-letter-submit"]').first
                    if await submit_btn.count() > 0 and await submit_btn.is_visible():
                        logger.warning(
                            "⚠️ Поле ввода не найдено, но кнопка отправки есть. Пробуем отправить без письма.")
                        await submit_btn.click()
                        await asyncio.sleep(3)
                        if await page.locator(success_selector).count() > 0:
                            logger.info("🎉 Отклик отправлен без письма (успех)")
                            return 'wo_soporovod'
                        else:
                            logger.error("❌ Не удалось отправить отклик без письма.")
                            return 'failed'
                    else:
                        logger.error("❌ Поле ввода не появилось, и нет других признаков успеха.")
                        return 'failed'

            # Поле найдено – заполняем
            try:
                await message_field.click()
                await asyncio.sleep(0.5)
                await message_field.fill(message)
                logger.info("📨 Сопроводительное письмо вставлено.")
            except Exception as e:
                logger.error(f"❌ Не удалось заполнить поле: {e}")
                return 'failed'

            # Отправляем
            submit_button = page.locator('button[data-qa="vacancy-response-letter-submit"]')
            if await submit_button.count() == 0:
                logger.error("❌ Кнопка отправки письма не найдена")
                return 'failed'

            logger.info("🖱️ Нажимаем кнопку отправки")
            await submit_button.first.click()
            await asyncio.sleep(3)

            # Проверяем успех
            if await page.locator('textarea[name="text"]').count() == 0:
                logger.info("🎉 Поле ввода исчезло – отклик успешно отправлен")
                return 'success'

            try:
                await page.wait_for_selector('div[data-qa="vacancy-response-success"]', state="visible", timeout=5000)
                logger.info("🎉 Отклик подтверждён")
                return 'success'
            except Exception:
                error_elem = page.locator('div[data-qa="vacancy-response-error"]')
                if await error_elem.count() > 0:
                    error_text = await error_elem.text_content()
                    logger.error(f"❌ Ошибка при отклике: {error_text}")
                    return 'failed'
                else:
                    logger.warning("⚠️ Не дождались подтверждения, но ошибка не обнаружена – считаем успехом")
                    return 'success'

        except Exception as e:
            logger.error(f"💥 Ошибка при отклике на {vacancy_url}: {e}")
            return 'failed'
        finally:
            await page.close()

    # async def respond_to_vacancy(
    #         self, vacancy_url: str, resume_id: str, message: str, context: BrowserContext
    # ) -> bool:
    #     page = await context.new_page()
    #     try:
    #         logger.info(f"➡️ Переходим на страницу вакансии: {vacancy_url}")
    #         await page.goto(vacancy_url, wait_until="domcontentloaded", timeout=60000)
    #
    #         # Закрываем возможные диалоги
    #         await self._handle_dialogs(page)
    #
    #         # Ищем кнопку отклика (верхнюю или нижнюю)
    #         logger.info("🔍 Ищем кнопку отклика...")
    #         respond_button = page.locator('a[data-qa="vacancy-response-link-top"]').first
    #         if await respond_button.count() == 0:
    #             respond_button = page.locator('a[data-qa="vacancy-response-link-bottom"]').first
    #         if await respond_button.count() == 0:
    #             logger.warning(f"❌ Кнопка отклика не найдена на {vacancy_url}")
    #             return False
    #
    #         logger.info("✅ Кнопка найдена, кликаем...")
    #         await respond_button.click(no_wait_after=True)
    #         logger.info("✅ Кнопка нажата, ожидаем появления поля ввода...")
    #
    #         # Проверяем, не в iframe ли открылась форма
    #         frame = await self._check_iframe(page)
    #         if frame:
    #             # Дальше работаем с фреймом
    #             page_for_field = frame
    #         else:
    #             page_for_field = page
    #
    #         # Пытаемся найти поле ввода
    #         await asyncio.sleep(2)  # небольшая пауза для загрузки формы
    #         message_field = await self._find_message_field(page_for_field)
    #
    #         if not message_field:
    #             # Если поле не найдено, возможно, отклик уже был отправлен без письма
    #             success_selector = 'div[data-qa="vacancy-response-success"]'
    #             if await page.locator(success_selector).count() > 0:
    #                 logger.info("ℹ️ Отклик уже был отправлен или отправлен без письма.")
    #                 return True
    #
    #             # Возможно, требуется выбрать резюме
    #             resume_selector = 'div[data-qa="resume-selector"]'
    #             if await page.locator(resume_selector).count() > 0:
    #                 logger.info("🔍 Появился выбор резюме, ищем нужное...")
    #                 resume_radio = page.locator(f'input[type="radio"][value="{resume_id}"]')
    #                 if await resume_radio.count() > 0:
    #                     logger.info(f"✅ Выбираем резюме с id {resume_id}")
    #                     await resume_radio.click()
    #                 else:
    #                     logger.warning(f"⚠️ Резюме {resume_id} не найдено, выбираем первое доступное")
    #                     first_radio = page.locator('input[type="radio"][name="resume"]').first
    #                     if await first_radio.count() > 0:
    #                         await first_radio.click()
    #                     else:
    #                         logger.error("❌ Нет доступных резюме для выбора")
    #                         return False
    #
    #                 # После выбора резюме снова ищем поле
    #                 await asyncio.sleep(1)
    #                 message_field = await self._find_message_field(page_for_field)
    #                 if not message_field:
    #                     if await page.locator(success_selector).count() > 0:
    #                         logger.info("ℹ️ Отклик отправлен без сопроводительного письма.")
    #                         return True
    #                     else:
    #                         logger.error("❌ После выбора резюме поле не появилось.")
    #                         return False
    #             else:
    #                 # Проверим, есть ли кнопка отправки без поля (редко)
    #                 submit_btn = page.locator('button[data-qa="vacancy-response-letter-submit"]').first
    #                 if await submit_btn.count() > 0 and await submit_btn.is_visible():
    #                     logger.warning(
    #                         "⚠️ Поле ввода не найдено, но кнопка отправки есть. Пробуем отправить без письма.")
    #                     await submit_btn.click()
    #                     await asyncio.sleep(3)
    #                     if await page.locator(success_selector).count() > 0:
    #                         logger.info("🎉 Отклик отправлен без письма (успех)")
    #                         return True
    #                     else:
    #                         logger.error("❌ Не удалось отправить отклик без письма.")
    #                         return False
    #                 else:
    #                     logger.error("❌ Поле ввода не появилось, и нет других признаков успеха.")
    #                     return False
    #
    #         # Поле найдено – активируем и заполняем
    #         try:
    #             await message_field.click()
    #             await asyncio.sleep(0.5)
    #             await message_field.fill(message)
    #             logger.info("📨 Сопроводительное письмо вставлено.")
    #         except Exception as e:
    #             logger.error(f"❌ Не удалось заполнить поле: {e}")
    #             return False
    #
    #         # Ищем кнопку отправки
    #         submit_button = page.locator('button[data-qa="vacancy-response-letter-submit"]')
    #         if await submit_button.count() == 0:
    #             logger.error("❌ Кнопка отправки письма не найдена")
    #             return False
    #
    #         logger.info("🖱️ Нажимаем кнопку отправки")
    #         await submit_button.first.click()
    #
    #         # Даём время на отправку запроса
    #         await asyncio.sleep(3)
    #
    #         # Проверяем, исчезло ли поле ввода (признак успешной отправки)
    #         if await page.locator('textarea[name="text"]').count() == 0:
    #             logger.info("🎉 Поле ввода исчезло – отклик успешно отправлен")
    #             return True
    #
    #         # Если поле ещё на месте, пробуем дождаться подтверждения
    #         try:
    #             await page.wait_for_selector('div[data-qa="vacancy-response-success"]',
    #                                          state="visible", timeout=5000)
    #             logger.info("🎉 Отклик подтверждён")
    #             return True
    #         except Exception:
    #             # Проверяем, нет ли сообщения об ошибке
    #             error_elem = page.locator('div[data-qa="vacancy-response-error"]')
    #             if await error_elem.count() > 0:
    #                 error_text = await error_elem.text_content()
    #                 logger.error(f"❌ Ошибка при отклике: {error_text}")
    #                 return False
    #             else:
    #                 # Явной ошибки нет – скорее всего, отклик ушёл
    #                 logger.warning("⚠️ Не дождались подтверждения, но ошибка не обнаружена – считаем успехом")
    #                 return True
    #
    #     except Exception as e:
    #         logger.error(f"💥 Ошибка при отклике на {vacancy_url}: {e}")
    #         return False
    #     finally:
    #         await page.close()
