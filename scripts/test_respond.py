#!/usr/bin/env python3
"""
Скрипт для тестирования отклика с использованием тех же компонентов,
что и основной воркер (BrowserManager + HHResponseService + message_generator).

Запуск:
    # С прокси из .env и GUI (рекомендуемый сценарий для отладки)
    python -m scripts.test_respond --url "https://perm.hh.ru/vacancy/136005918" --gui

    # Без прокси, но с GUI
    python -m scripts.test_respond --url "https://perm.hh.ru/vacancy/136005918" --gui --no-proxy

    # С конкретным cookies файлом
    python -m scripts.test_respond --url "https://perm.hh.ru/vacancy/136005918" --cookies cookies/plato_sh.txt --gui

    # С готовым письмом (без генерации)
    python -m scripts.test_respond --url "https://perm.hh.ru/vacancy/136005918" --message "Привет!" --gui

    # С шаблоном
    python -m scripts.test_respond --url "https://perm.hh.ru/vacancy/136005918" --template app/message_template.txt --gui

Примечание:
    --url принимает ПОЛНЫЙ URL вакансии (а не только ID). Это важно, т.к. hh.ru
    использует разные поддомены (hh.ru, perm.hh.ru, spb.hh.ru), и cookies от
    одного могут не работать на другом.
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


def _extract_vacancy_name_from_url(url: str) -> str:
    """Извлекает ID вакансии из URL. Fallback — 'вакансия'."""
    import re
    m = re.search(r'/vacancy/(\d+)', url)
    if m:
        return f"вакансия {m.group(1)}"
    return "вакансия"


def _build_message(args) -> str:
    """Собирает финальный текст письма."""
    vacancy_name = args.vacancy_name or _extract_vacancy_name_from_url(args.url)

    # --- Определяем TG-username: --tg > имя cookies > .env ---
    tg_username = args.tg
    if not tg_username:
        cookies_stem = Path(args.cookies).stem  # "plato_sh"
        if cookies_stem:
            tg_username = cookies_stem if cookies_stem.startswith("@") else f"@{cookies_stem}"
            logger.info(f"📇 TG username из имени cookies: {tg_username}")
    if not tg_username:
        tg_username = settings.TG_USERNAME
        logger.info(f"📇 TG username из .env: {tg_username}")

    if args.message:
        logger.info("✉️ Использую готовый текст из --message (без генерации)")
        return args.message

    # --- Определяем шаблон ---
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
        logger.warning("⚠️ Шаблон не найден. Использую запасной текст.")
        return "Здравствуйте! Рассмотрите, пожалуйста, мою кандидатуру"

    template = template_path.read_text(encoding="utf-8")
    logger.info(f"📄 Шаблон загружен: {template_path}")

    context = {
        "vacancy_name": vacancy_name,
        "tg_username": tg_username,   # ← ИСПОЛЬЗУЕМ вычисленный
    }
    logger.info(f"🔧 Контекст: vacancy_name={vacancy_name!r}, tg_username={tg_username!r}")

    message = generate_message(template, context)
    logger.info(f"✉️ Сгенерированное письмо:\n{message}")
    return message


def _validate_url(url: str) -> str:
    """Проверяет, что URL — полный, а не просто ID."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        logger.error(
            f"❌ --url должен быть полным URL (https://...), получено: {url!r}\n"
            f"   Пример: https://perm.hh.ru/vacancy/136005918"
        )
        sys.exit(1)
    if "/vacancy/" not in url:
        logger.warning(f"⚠️ В URL нет '/vacancy/': {url!r}. Убедитесь, что это ссылка на вакансию.")
    return url


def _log_environment(args):
    """Печатает в лог ключевые настройки — чтобы сразу видеть, что подхватилось."""
    cookies_path = Path(args.cookies)
    logger.info("=" * 70)
    logger.info("📋 КОНФИГУРАЦИЯ ЗАПУСКА:")
    logger.info(f"   URL вакансии:      {args.url}")
    logger.info(f"   Cookies файл:      {cookies_path.resolve()}")
    logger.info(f"   Cookies существует: {cookies_path.exists()}")
    logger.info(f"   Resume ID:         {args.resume}")
    logger.info(f"   Headless:          {not args.gui}")
    logger.info(f"   Use proxy:         {not args.no_proxy}")
    logger.info(f"   PROXY_SERVER:      {settings.PROXY_SERVER or '<не задан>'}")
    logger.info(f"   PROXY_USERNAME:    {settings.PROXY_USERNAME or '<не задан>'}")
    logger.info(f"   PROXY_PASSWORD:    {'<задан>' if settings.PROXY_PASSWORD else '<не задан>'}")
    logger.info(f"   DB_HOST:           {settings.DB_HOST}")
    logger.info(f"   MESSAGE_TEMPLATE:  {settings.MESSAGE_TEMPLATE_FILE}")
    logger.info(f"   TG_USERNAME:       {settings.TG_USERNAME}")
    logger.info("=" * 70)

    if not cookies_path.exists():
        logger.error(f"❌ Файл cookies не найден: {cookies_path.resolve()}")
        logger.error(f"   Проверьте путь или запустите из корня проекта.")
        sys.exit(1)


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
        from app.utils.proxy_utils import normalize_proxy_server

        if use_proxy:
            if settings.PROXY_SERVER:
                proxy_url = normalize_proxy_server(settings.PROXY_SERVER)
                logger.info(f"🌐 Используется прокси из .env: {proxy_url}")
                if settings.PROXY_USERNAME:
                    logger.info(f"🌐 Прокси username: {settings.PROXY_USERNAME}")
                # proxy=None → create_context сам возьмёт settings.PROXY_SERVER
                context = await browser_manager.create_context(cookies_source=cookies_file)
            else:
                logger.info("🌐 Прокси не задан в .env, работаем напрямую")
                context = await browser_manager.create_context(cookies_source=cookies_file, proxy={})
        else:
            logger.info("🚫 Прокси отключён (--no-proxy)")
            context = await browser_manager.create_context(cookies_source=cookies_file, proxy={})

        responder = HHResponseService()
        status = await responder.respond_to_vacancy(
            vacancy_url=vacancy_url,  # ← как передали, без пересборки
            resume_id=resume_id,
            message=message,
            context=context,
        )
        return status
    finally:
        await browser_manager.stop()


def main():
    parser = argparse.ArgumentParser(
        description="Тестирование отклика на hh.ru",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--url", required=True,
        help="ПОЛНЫЙ URL вакансии (например, https://perm.hh.ru/vacancy/136005918)",
    )
    parser.add_argument(
        "--resume",
        default="dc325d36ff10bdb3870039ed1f343061483477",
        help="ID резюме hh.ru",
    )
    parser.add_argument(
        "--cookies", default="cookies/plato_sh.txt",
        help="Путь к файлу cookies (Netscape format)",
    )
    parser.add_argument("--tg", default=None,
                        help="Telegram-ник для письма (по умолчанию — из имени cookies)")

    # Взаимоисключающие способы задать письмо
    msg_group = parser.add_mutually_exclusive_group()
    msg_group.add_argument("--message", default=None,
                           help="Готовый текст письма (без генерации)")
    msg_group.add_argument("--template", default=None,
                           help="Путь к файлу шаблона (сгенерируется через message_generator)")

    parser.add_argument("--vacancy-name", default=None,
                        help="Название вакансии для подстановки {vacancy_name} в шаблон")
    parser.add_argument("--no-proxy", action="store_true",
                        help="Отключить прокси для этого запуска")
    parser.add_argument("--gui", action="store_true",
                        help="Показать браузер (не headless)")

    args = parser.parse_args()

    # Валидация URL
    args.url = _validate_url(args.url)

    # Логирование
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Проверка окружения
    _log_environment(args)

    headless = not args.gui
    use_proxy = not args.no_proxy

    try:
        message = _build_message(args)
    except SystemExit:
        return

    try:
        status = asyncio.run(test_respond(
            vacancy_url=args.url,
            resume_id=args.resume,
            message=message,
            cookies_file=args.cookies,
            headless=headless,
            use_proxy=use_proxy,
        ))
    except KeyboardInterrupt:
        logger.warning("\n⚠️ Прервано пользователем (Ctrl+C)")
        return

    # Итог
    if status == "success":
        logger.info("✅ Отклик с сопроводительным письмом отправлен")
    elif status == "wo_soprovod":
        logger.warning("⚠️ Отклик без сопроводительного письма")
    elif status == "test_required":
        logger.warning("🧪 Требуется тест от работодателя")
    elif status == "already_responded":
        logger.info("♻️ Отклик уже был сделан ранее")
    elif status == "vacancy_archived":
        logger.info("📦 Вакансия в архиве")
    elif status == "other_country_skipped":
        logger.info("🌍 Вакансия в другой стране — пропущено")
    elif status == "auth_required":
        logger.error("🔐 Требуется авторизация (cookies протухли)")
    else:
        logger.error(f"❌ Отклик не удался (status={status})")


if __name__ == "__main__":
    main()
