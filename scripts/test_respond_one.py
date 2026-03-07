import asyncio
import logging
from pathlib import Path

from app.db.database import AsyncSessionLocal, init_db
from app.db.models import Account
from app.services import BrowserManager, HHResponseService, generate_message
from app.services.cookie_loader import load_cookies_from_netscape
from app.config import settings
from sqlalchemy import select

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Параметры аккаунта по умолчанию (можно изменить при необходимости)
DEFAULT_ACCOUNT_ID = 1
DEFAULT_TG_USERNAME = "pefaaha1301"
DEFAULT_RESUME_ID = "41bf6a4cff103235840039ed1f44433836306e"
COOKIES_FILE = "cookies.txt"  # файл должен лежать в корне проекта (/app/cookies.txt)


async def ensure_account_exists():
    """Создаёт таблицы и проверяет наличие аккаунта с id=1. Если нет — создаёт."""
    await init_db()  # создаст таблицы, если их нет

    async with AsyncSessionLocal() as session:
        # Проверяем, есть ли аккаунт с id=1
        result = await session.execute(select(Account).where(Account.id == DEFAULT_ACCOUNT_ID))
        account = result.scalar_one_or_none()
        if account:
            logger.info(f"Аккаунт с id={DEFAULT_ACCOUNT_ID} уже существует")
            return account

        # Создаём новый аккаунт
        logger.info(f"Аккаунт с id={DEFAULT_ACCOUNT_ID} не найден, создаём...")
        cookies = load_cookies_from_netscape(COOKIES_FILE)
        new_account = Account(
            id=DEFAULT_ACCOUNT_ID,
            tg_username=DEFAULT_TG_USERNAME,
            cookies=cookies,
            resume_id=DEFAULT_RESUME_ID
        )
        session.add(new_account)
        await session.commit()
        logger.info(f"✅ Аккаунт с id={DEFAULT_ACCOUNT_ID} успешно создан")
        return new_account


async def test_respond(account_id: int, vacancy_url: str, resume_id: str):
    # Убеждаемся, что аккаунт существует
    account = await ensure_account_exists()

    # Загружаем шаблон письма
    template_path = Path(settings.MESSAGE_TEMPLATE_FILE)
    if not template_path.exists():
        logger.error(f"Файл шаблона {template_path} не найден")
        return
    template = template_path.read_text(encoding='utf-8')

    # Получаем название вакансии (можно парсить, но для теста оставим константу)
    vacancy_name = "Специалист по логистике"  # в идеале парсить из страницы
    message = generate_message(template, {
        "vacancy_name": vacancy_name,
        "tg_username": account.tg_username or ""
    })

    async with BrowserManager(headless=settings.PLAYWRIGHT_HEADLESS, cookies=account.cookies) as browser:
        responder = HHResponseService()
        success = await responder.respond_to_vacancy(
            vacancy_url, resume_id, message, browser.context
        )
        if success:
            logger.info("✅ Отклик успешно отправлен!")
        else:
            logger.error("❌ Отклик не удался")


if __name__ == "__main__":
    # Параметры теста
    ACCOUNT_ID = 1
    VACANCY_URL = "https://hh.ru/vacancy/131029953"
    RESUME_ID = DEFAULT_RESUME_ID

    asyncio.run(test_respond(ACCOUNT_ID, VACANCY_URL, RESUME_ID))
