
import asyncio
import logging
from pathlib import Path
from sqlalchemy import select

from app.config import settings
from app.db import AsyncSessionLocal, Vacancy, AccountVacancy, VacancyRepository

from app.hh_client import (
    BrowserManager,
    generate_message,
    HHResponseService,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ID аккаунта, под которым будут сохраняться отклики (можно задать в .env, если нужно)
DEFAULT_ACCOUNT_ID = 1


async def get_unresponded_vacancies(session):
    """
    Возвращает список вакансий, на которые ещё не было откликов
    для аккаунта DEFAULT_ACCOUNT_ID.
    """
    subquery = select(AccountVacancy.hh_id).where(
        AccountVacancy.account_id == DEFAULT_ACCOUNT_ID,
        AccountVacancy.responded == True
    ).subquery()
    stmt = select(Vacancy).where(Vacancy.hh_id.not_in(subquery))
    result = await session.execute(stmt)
    return result.scalars().all()


async def respond_to_vacancies():
    """Основная функция: загружает всё из конфига, запускает браузер и откликается."""
    logger.info("🚀 Начинаем пакетный отклик")

    # 1. Проверяем наличие обязательных настроек
    if not settings.RESUME_ID:
        logger.error("❌ RESUME_ID не задан в .env")
        return

    # 2. Загружаем шаблон письма
    template_path = Path(settings.MESSAGE_TEMPLATE_FILE)
    if not template_path.exists():
        logger.error(f"❌ Файл шаблона {template_path} не найден")
        return
    template = template_path.read_text(encoding='utf-8')

    # 3. Запускаем браузер с куками из файла (путь берётся из .env)
    async with BrowserManager(
            headless=settings.PLAYWRIGHT_HEADLESS,
            cookies=settings.HH_COOKIES_FILE  # передаём строку – BrowserManager сам загрузит полные куки
    ) as browser:
        response_service = HHResponseService()
        async with AsyncSessionLocal() as session:
            repo = VacancyRepository(session)
            vacancies = await get_unresponded_vacancies(session)
            logger.info(f"📋 Найдено {len(vacancies)} вакансий для отклика")

            for vacancy in vacancies:
                context = {
                    "vacancy_name": vacancy.title,
                    "tg_username": settings.TG_USERNAME,
                }
                message = generate_message(template, context)
                logger.info(f"✉️ Письмо для {vacancy.title}:\n{message}")

                success = await response_service.respond_to_vacancy(
                    vacancy.url,
                    settings.RESUME_ID,
                    message,
                    browser.context
                )
                status = "success" if success else "failed"
                await repo.mark_responded(DEFAULT_ACCOUNT_ID, vacancy.hh_id, status)
                logger.info(f"{'✅' if success else '❌'} Отклик на {vacancy.title} ({vacancy.hh_id}): {status}")
                await asyncio.sleep(5)


async def main():
    await respond_to_vacancies()


if __name__ == "__main__":
    asyncio.run(main())
