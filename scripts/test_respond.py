#!/usr/bin/env python3
"""
Скрипт для тестирования отклика с использованием тех же компонентов,
что и основной воркер (BrowserManager + HHResponseService + message_generator).

Примеры:
    python -m scripts.test_respond --url 137328128 --no-proxy
    python -m scripts.test_respond --url 137328128 --gui --no-proxy
    python -m scripts.test_respond --url 137328128 --template app/message_template.txt
    python -m scripts.test_respond --url 137328128 --message "Здравствуйте! ..."
    python -m scripts.test_respond --url 137328128 --vacancy-name "Backend-разработчик"
"""

import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

import asyncio
import logging
import argparse

from app.config import settings
from app.hh_client.browser_manager import BrowserManager
from app.hh_client.hh_response import HHResponseService
from app.hh_client.message_generator import generate_message

logger = logging.getLogger(__name__)


def _build_message(args) -> str:
    """
    Собирает финальный текст письма.
    Приоритет:
      1. --message (готовый текст, без генерации)
      2. --template (путь к файлу шаблона) — генерируем через message_generator
      3. settings.MESSAGE_TEMPLATE_FILE (если файл существует) — генерируем
      4. Хардкод по умолчанию.
    """
    vacancy_name = args.vacancy_name or f"вакансия {args.url}"

    if args.message:
        logger.info("✉️ Использую готовый текст из --message (без генерации)")
        return args.message

    # Определяем шаблон
    template_path = None
    if args.template:
        template_path = Path(args.template)
        if not template_path.exists():
            logger.error(f"❌ Файл шаблона не найден: {template_path}")
            sys.exit(1)
    else:
        default_path = Path(settings.MESSAGE_TEMPLATE_FILE)
        if default_path.exists():
            template_path = default_path

    if template_path is None:
        logger.warning(
            f"⚠️ Шаблон не найден (ни --template, ни {settings.MESSAGE_TEMPLATE_FILE}). "
            f"Использую запасной текст."
        )
        return "Здравствуйте! Рассмотрите, пожалуйста, мою кандидатуру"

    template = template_path.read_text(encoding="utf-8")
    logger.info(f"📄 Шаблон загружен: {template_path}")

    context = {
        "vacancy_name": vacancy_name,
        "tg_username": settings.TG_USERNAME,   # ← подтягивается из .env
    }
    logger.info(f"🔧 Контекст: vacancy_name={vacancy_name!r}, tg_username={settings.TG_USERNAME!r}")

    message = generate_message(template, context)
    logger.info(f"✉️ Сгенерированное письмо:\n{message}")
    return message


async def test_respond(
        vacancy_url: str,
        resume_id: str,
        message: str,
        cookies_file: str,
        headless: bool = False,
        use_proxy: bool = True,
) -> str:
    browser_manager = BrowserManager(headless=headless)
    await browser_manager.start()
    try:
        if use_proxy and settings.PROXY_SERVER:
            logger.info(f"🌐 Используется прокси из настроек: {settings.PROXY_SERVER}")
            context = await browser_manager.create_context(cookies_source=cookies_file)
        else:
            # Пустой dict != None, поэтому глобальный прокси из settings не применится
            context = await browser_manager.create_context(cookies_source=cookies_file, proxy={})
            if use_proxy:
                logger.info("🌐 Прокси не задан в настройках, работаем напрямую")
            else:
                logger.info("🚫 Прокси отключён (--no-proxy)")

        responder = HHResponseService()
        status = await responder.respond_to_vacancy(
            vacancy_url=f"https://hh.ru/vacancy/{vacancy_url}",
            resume_id=resume_id,
            message=message,
            context=context,
        )
        return status
    finally:
        await browser_manager.stop()


def main():
    parser = argparse.ArgumentParser(description="Тестирование отклика")

    parser.add_argument("--url", required=True, help="ID вакансии (например, 137328128)")
    parser.add_argument("--resume", default="41bf6a4cff103235840039ed1f44433836306e",
                        help="ID резюме hh.ru")
    parser.add_argument("--cookies", default="cookies/peyego1one.txt",
                        help="Путь к файлу cookies")

    # Взаимоисключающие способы задать письмо:
    msg_group = parser.add_mutually_exclusive_group()
    msg_group.add_argument("--message", default=None,
                           help="Готовый текст письма (без генерации)")
    msg_group.add_argument("--template", default=None,
                           help="Путь к файлу шаблона (сгенерируется через message_generator)")

    parser.add_argument("--vacancy-name", default=None,
                        help="Название вакансии для подстановки {vacancy_name} в шаблон")

    parser.add_argument("--no-proxy", action="store_true", help="Отключить прокси для этого запуска")
    parser.add_argument("--gui", action="store_true", help="Показать браузер (не headless)")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    headless = not args.gui
    use_proxy = not args.no_proxy

    logger.info(
        f"Запуск теста. URL: {args.url}, headless={headless}, "
        f"proxy={'off' if args.no_proxy else 'on'}, tg_username={settings.TG_USERNAME!r}"
    )

    try:
        message = _build_message(args)
    except SystemExit:
        return

    status = asyncio.run(test_respond(
        vacancy_url=args.url,
        resume_id=args.resume,
        message=message,
        cookies_file=args.cookies,
        headless=headless,
        use_proxy=use_proxy,
    ))

    if status == "success":
        logger.info("✅ Отклик с сопроводительным письмом отправлен")
    elif status == "wo_soprovod":
        logger.warning("⚠️ Отклик без сопроводительного письма")
    elif status == "test_required":
        logger.warning("🧪 Требуется тест от работодателя")
    else:
        logger.error(f"❌ Отклик не удался (status={status})")


if __name__ == "__main__":
    main()