import asyncio
import logging
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.database import AsyncSessionLocal
from app.db.models import Account, Vacancy, AccountVacancy
from app.services import (
    BrowserManager,
    generate_message,
    HHResponseService,
    VacancyRepository
)
from app.services.cookie_loader import load_cookies_from_netscape

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def get_unresponded_vacancies(session: AsyncSession, account_id: int):
    """Возвращает список вакансий, на которые аккаунт ещё не откликался."""
    subquery = select(AccountVacancy.hh_id).where(
        AccountVacancy.account_id == account_id,
        AccountVacancy.responded == True
    ).subquery()
    stmt = select(Vacancy).where(Vacancy.hh_id.not_in(subquery))
    result = await session.execute(stmt)
    return result.scalars().all()


async def respond_for_account(account: Account):
    """Выполнить отклики для одного аккаунта."""
    logger.info(f"Начинаем отклики для аккаунта {account.id} (tg: {account.tg_username})")

    # 1. Загружаем куки: из поля account.cookies
    cookies = account.cookies
    if not cookies:
        logger.warning(f"У аккаунта {account.id} нет кук, пропускаем")
        return

    # 2. ID резюме (берём из поля default_resume_id или из переменной окружения)
    resume_id = account.default_resume_id or settings.DEFAULT_RESUME_ID
    if not resume_id:
        logger.error(f"Не указан resume_id для аккаунта {account.id}")
        return

    # 3. Загружаем шаблон письма
    template_path = Path(settings.MESSAGE_TEMPLATE_FILE)
    if not template_path.exists():
        logger.error(f"Файл шаблона {template_path} не найден")
        return
    template = template_path.read_text(encoding='utf-8')

    async with BrowserManager(headless=settings.PLAYWRIGHT_HEADLESS, cookies=cookies) as browser:
        response_service = HHResponseService()
        async with AsyncSessionLocal() as session:
            repo = VacancyRepository(session)
            vacancies = await get_unresponded_vacancies(session, account.id)
            logger.info(f"Найдено {len(vacancies)} вакансий для отклика")

            for vacancy in vacancies:
                context = {
                    "vacancy_name": vacancy.title,
                    "tg_username": account.tg_username or ""
                }
                message = generate_message(template, context)
                success = await response_service.respond_to_vacancy(
                    vacancy.url, resume_id, message, browser.context
                )
                status = "success" if success else "failed"
                await repo.mark_responded(account.id, vacancy.hh_id, status)
                logger.info(f"Отклик на {vacancy.title} ({vacancy.hh_id}): {status}")
                await asyncio.sleep(5)  # задержка между откликами


async def main(account_id: Optional[int] = None):
    """Основная функция. Если account_id не указан, обрабатывает все аккаунты."""
    async with AsyncSessionLocal() as session:
        if account_id:
            stmt = select(Account).where(Account.id == account_id)
        else:
            stmt = select(Account)
        result = await session.execute(stmt)
        accounts = result.scalars().all()

    for account in accounts:
        await respond_for_account(account)


if __name__ == "__main__":
    import sys
    account_id = int(sys.argv[1]) if len(sys.argv) > 1 else None
    asyncio.run(main(account_id))