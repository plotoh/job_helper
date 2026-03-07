#!/usr/bin/env python3
"""
скрипт для тестирования отклика

python hh_respond_test.py [--url URL] [--resume RESUME_ID] [--message "Текст"] [--cookies ФАЙЛ] [--headless]

python scripts/test_respond.py --url 130807542 --headless
"""

import asyncio
import logging
import argparse
from pathlib import Path

from playwright.async_api import async_playwright, BrowserContext, TimeoutError as PlaywrightTimeoutError


def load_cookies_from_netscape(file_path: str) -> dict:
    """
    загружает cookies из файла в формате Netscape
    возвращает словарь
    """
    cookies = {}
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split('\t')
                if len(parts) >= 7:
                    # Формат: domain, flag, path, secure, expiration, name, value
                    name = parts[5]
                    value = parts[6]
                    cookies[name] = value
        logging.info(f"Есть {len(cookies)} cookies из {file_path}")
    except Exception as e:
        logging.error(f"Ошибка cookies - {file_path}: {e}")
        raise
    return cookies


# ----------------------------------------------------------------------
# 2. Сервис отклика (адаптирован из вашего HHResponseService)
# ----------------------------------------------------------------------
logger = logging.getLogger(__name__)


class HHResponseService:
    async def respond_to_vacancy(
            self, vacancy_url: str, resume_id: str, message: str, context: BrowserContext
    ) -> bool:
        page = await context.new_page()
        try:
            logger.info(f"➡️ Переходим на страницу вакансии: {vacancy_url}")
            await page.goto(vacancy_url, wait_until="domcontentloaded", timeout=60000)

            # Ищем кнопку отклика (верхнюю или нижнюю)
            logger.info("🔍 Ищем кнопку отклика")
            respond_button = page.locator('a[data-qa="vacancy-response-link-top"]').first
            if await respond_button.count() == 0:
                respond_button = page.locator('a[data-qa="vacancy-response-link-bottom"]').first
            if await respond_button.count() == 0:
                logger.warning(f"❌ Кнопка отклика не найдена на {vacancy_url}")
                return False

            logger.info("✅ Кнопка найдена")
            # Кликаем и сразу начинаем ждать появления поля ввода
            await respond_button.click(no_wait_after=True)
            logger.info("✅ Кнопка нажата, жду поле ввода")

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
                    logger.info("🔍 Появился выбор резюме, ищем нужное")
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
                            logger.info("ℹ️ Отклик отправлен без письма.")
                            return True
                        else:
                            logger.error("❌ После выбора резюме поле сопровода не появилось.")
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

            # Ждём подтверждения отправки
            try:
                await page.wait_for_selector('div[data-qa="vacancy-response-success"]',
                                             state="visible", timeout=10000)
                logger.info(f"🎉 Отклик на {vacancy_url} отправлен успешно")
                return True
            except PlaywrightTimeoutError:
                logger.error("❌ Не дождались подтверждения отправки")
                return False

        except Exception as e:
            logger.error(f"💥 Ошибка при отклике на {vacancy_url}: {e}")
            return False
        finally:
            await page.close()


async def test_respond(vacancy_url: str, resume_id: str, message: str, cookies_file: str, headless: bool = False):
    """
    Загружает cookies, создаёт контекст браузера и выполняет отклик.
    """
    cookies_dict = load_cookies_from_netscape(cookies_file)

    def load_playwright_cookies(file_path: str):
        cookies_list = []
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    parts = line.split('\t')
                    if len(parts) >= 7:
                        domain = parts[0]
                        path = parts[2]
                        secure = parts[3].upper() == 'TRUE'  # TRUE/FALSE
                        name = parts[5]
                        value = parts[6]
                        cookie = {
                            "name": name,
                            "value": value,
                            "domain": domain,
                            "path": path,
                            "secure": secure,
                            "httpOnly": False,
                            "sameSite": "Lax",
                        }
                        cookies_list.append(cookie)
            logger.info(f"Есть {len(cookies_list)} cookies из {file_path}")
        except Exception as e:
            logger.error(f"Ошибка  cookies - путь {file_path}: {e}")
            raise
        return cookies_list

    cookies = load_playwright_cookies(cookies_file)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context()

        if cookies:
            await context.add_cookies(cookies)

        responder = HHResponseService()
        success = await responder.respond_to_vacancy(
            vacancy_url=f"https://hh.ru/vacancy/{vacancy_url}",
            resume_id=resume_id,
            message=message,
            context=context
        )

        await browser.close()
        return success


def main():
    parser = argparse.ArgumentParser(description="тестирование отклика")
    parser.add_argument("--url", required=True, help="вакансия", default="https://hh.ru/vacancy/131029953")
    parser.add_argument("--resume", help="резюме", default="41bf6a4cff103235840039ed1f44433836306e")
    parser.add_argument("--message", help="сопроводительное",
                        default="Здравствуйте! Рассмотрите, пожалуйста, мою кандидатуру")
    parser.add_argument("--cookies", default="app/cookies.txt")
    parser.add_argument("--headless", action="store_true", help="фоновый режим(без GUI)")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    result = asyncio.run(test_respond(
        vacancy_url=args.url,
        resume_id=args.resume,
        message=args.message,
        cookies_file=args.cookies,
        headless=args.headless
    ))

    if result:
        logging.info("✅ cкрипт завершился успешно")
    else:
        logging.error("❌ cкрипт завершился с ошибкой")


if __name__ == "__main__":
    main()
