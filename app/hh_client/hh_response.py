import asyncio
import logging
from playwright.async_api import BrowserContext, TimeoutError as PlaywrightTimeoutError
from app.hh_client.interfaces import IResponseService

logger = logging.getLogger(__name__)


class HHResponseService(IResponseService):
    async def respond_to_vacancy(
            self, vacancy_url: str, resume_id: str, message: str, context: BrowserContext
    ) -> bool:
        page = await context.new_page()
        try:
            logger.info(f"➡️ Переходим на страницу вакансии: {vacancy_url}")
            await page.goto(vacancy_url, wait_until="domcontentloaded", timeout=60000)

            # Ищем кнопку отклика (верхнюю или нижнюю)
            logger.info("🔍 Ищем кнопку отклика...")
            respond_button = page.locator('a[data-qa="vacancy-response-link-top"]').first
            if await respond_button.count() == 0:
                respond_button = page.locator('a[data-qa="vacancy-response-link-bottom"]').first
            if await respond_button.count() == 0:
                logger.warning(f"❌ Кнопка отклика не найдена на {vacancy_url}")
                return False

            logger.info("✅ Кнопка найдена, кликаем...")
            await respond_button.click(no_wait_after=True)
            logger.info("✅ Кнопка нажата, ожидаем появления поля ввода...")

            # --- Правильный селектор поля сопроводительного письма ---
            message_field_selector = 'textarea[name="text"]'

            try:
                # Ждём, пока поле станет видимым
                await page.wait_for_selector(message_field_selector, state="visible", timeout=15000)
                logger.info("✅ Поле ввода появилось.")
            except PlaywrightTimeoutError:
                # Если поле не появилось, возможно, отклик уже был отправлен без письма
                success_selector = 'div[data-qa="vacancy-response-success"]'
                if await page.locator(success_selector).count() > 0:
                    logger.info("ℹ️ Отклик уже был отправлен или отправлен без письма.")
                    return True

                # Возможно, требуется выбрать резюме
                resume_selector = 'div[data-qa="resume-selector"]'
                if await page.locator(resume_selector).count() > 0:
                    logger.info("🔍 Появился выбор резюме, ищем нужное...")
                    resume_radio = page.locator(f'input[type="radio"][value="{resume_id}"]')
                    if await resume_radio.count() > 0:
                        logger.info(f"✅ Выбираем резюме с id {resume_id}")
                        await resume_radio.click()
                    else:
                        logger.warning(f"⚠️ Резюме {resume_id} не найдено, выбираем первое доступное")
                        first_radio = page.locator('input[type="radio"][name="resume"]').first
                        if await first_radio.count() > 0:
                            await first_radio.click()
                        else:
                            logger.error("❌ Нет доступных резюме для выбора")
                            return False

                    # После выбора резюме снова ждём поле
                    try:
                        await page.wait_for_selector(message_field_selector, state="visible", timeout=10000)
                        logger.info("✅ Поле ввода появилось после выбора резюме.")
                    except PlaywrightTimeoutError:
                        if await page.locator(success_selector).count() > 0:
                            logger.info("ℹ️ Отклик отправлен без сопроводительного письма.")
                            return True
                        else:
                            logger.error("❌ После выбора резюме поле не появилось.")
                            return False
                else:
                    # Ничего не помогло
                    logger.error("❌ Поле ввода не появилось, и нет других признаков успеха.")
                    return False

            # Поле найдено – получаем его и активируем кликом
            message_field = page.locator(message_field_selector).first
            await message_field.click()
            await asyncio.sleep(0.5)  # небольшая пауза для активации
            await message_field.fill(message)
            logger.info("📨 Сопроводительное письмо вставлено.")

            # Ищем кнопку отправки
            submit_button = page.locator('button[data-qa="vacancy-response-letter-submit"]')
            if await submit_button.count() == 0:
                logger.error("❌ Кнопка отправки письма не найдена")
                return False

            logger.info("🖱️ Нажимаем кнопку отправки")
            await submit_button.first.click()

            # Даём время на отправку запроса
            await asyncio.sleep(3)

            # Проверяем, исчезло ли поле ввода (признак успешной отправки)
            if await page.locator('textarea[name="text"]').count() == 0:
                logger.info("🎉 Поле ввода исчезло – отклик успешно отправлен")
                return True

            # Если поле ещё на месте, пробуем дождаться подтверждения (но недолго)
            try:
                await page.wait_for_selector('div[data-qa="vacancy-response-success"]',
                                             state="visible", timeout=5000)
                logger.info("🎉 Отклик подтверждён")
                return True
            except Exception:
                # Проверяем, нет ли сообщения об ошибке
                error_elem = page.locator('div[data-qa="vacancy-response-error"]')
                if await error_elem.count() > 0:
                    error_text = await error_elem.text_content()
                    logger.error(f"❌ Ошибка при отклике: {error_text}")
                    return False
                else:
                    # Явной ошибки нет – скорее всего, отклик ушёл
                    logger.warning("⚠️ Не дождались подтверждения, но ошибка не обнаружена – считаем успехом")
                    return True

        except Exception as e:
            logger.error(f"💥 Ошибка при отклике на {vacancy_url}: {e}")
            return False
        finally:
            await page.close()