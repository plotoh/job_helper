#!/usr/bin/env python3
"""
Автономный тест отклика, использующий конфигурацию и файл cookies.
Не требует базы данных.
"""

import asyncio
import logging
from pathlib import Path

from app.config import settings
from app.services import BrowserManager, HHResponseService, generate_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def test_respond(vacancy_url: str):
    """
    Выполняет тестовый отклик на указанную вакансию.
    """
    # 1. Проверяем наличие необходимых настроек
    if not settings.RESUME_ID:
        logger.error("RESUME_ID не задан в .env")
        return

    # 2. Загружаем шаблон письма
    template_path = Path(settings.MESSAGE_TEMPLATE_FILE)
    if not template_path.exists():
        logger.error(f"Файл шаблона {template_path} не найден")
        return
    template = template_path.read_text(encoding='utf-8')

    # 3. Формируем контекст для генерации письма
    # В реальности можно парсить название вакансии, но для теста используем заглушку
    vacancy_name = "Тестовая вакансия"  # В идеале извлечь из страницы
    context = {
        "vacancy_name": vacancy_name,
        "tg_username": "test_user"  # можно задать или брать из .env
    }
    message = generate_message(template, context)
    logger.info(f"Сгенерированное письмо:\n{message}")

    # 5. Запускаем браузер и выполняем отклик
    async with BrowserManager(headless=settings.PLAYWRIGHT_HEADLESS, cookies=settings.HH_COOKIES_FILE) as browser:
        responder = HHResponseService()
        success = await responder.respond_to_vacancy(
            vacancy_url=f"https://hh.ru/vacancy/{vacancy_url}",
            resume_id=settings.RESUME_ID,
            message=message,
            context=browser.context
        )

    if success:
        logger.info("✅ Отклик успешно отправлен!")
    else:
        logger.error("❌ Отклик не удался")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Использование: python test_respond_one.py <URL_вакансии>")
        sys.exit(1)
    vacancy_url = sys.argv[1]
    asyncio.run(test_respond(vacancy_url))
