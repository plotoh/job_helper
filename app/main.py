import asyncio
import logging
from typing import List

from app.config import settings
from app.db.database import AsyncSessionLocal, init_db
from app.services.browser_manager import BrowserManager
from app.services.cookie_loader import load_cookies_from_netscape
from app.services.hh_list_parser import HHVacancyListParser
from app.services.hh_detail_parser import HHVacancyDetailParser
from app.services.filter import VacancyFilter
from app.services.vacancy_repository import VacancyRepository

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL))
logger = logging.getLogger(__name__)


async def process_vacancies(
        list_parser, detail_parser, filter_service, browser_manager, max_concurrent: int
) -> List[dict]:
    # Сбор ID
    vacancies_list = await list_parser.fetch_ids(
        settings.HH_SEARCH_URL, settings.MAX_PAGES, browser_manager.context
    )
    logger.info(f"Всего собрано: {len(vacancies_list)}")

    # Фильтрация
    suitable = [v for v in vacancies_list if filter_service.is_match(v['title'])]
    logger.info(f"После фильтрации: {len(suitable)}")

    # Парсинг деталей с ограничением конкурентности
    semaphore = asyncio.Semaphore(max_concurrent)

    async def parse_one(vac):
        async with semaphore:
            logger.info(f"Парсим детали: {vac['id']} - {vac['title']}")
            try:
                details = await detail_parser.parse(vac['url'], browser_manager.context)
                return {**vac, **details}
            except Exception as e:
                logger.error(f"Не удалось распарсить {vac['id']}: {e}")
                return None

    tasks = [parse_one(v) for v in suitable]
    results = await asyncio.gather(*tasks)
    successful = [r for r in results if r is not None]
    logger.info(f"Успешно распаршено: {len(successful)}")
    return successful


async def save_vacancies(vacancies: List[dict]):
    async with AsyncSessionLocal() as session:
        repo = VacancyRepository(session)
        for vac in vacancies:
            try:
                inserted = await repo.save_if_not_exists(vac)
                if inserted:
                    logger.info(f"✅ Вакансия {vac['id']} сохранена")
                else:
                    logger.info(f"⏭️ Вакансия {vac['id']} уже существует")
            except Exception as e:
                logger.error(f"Ошибка сохранения {vac['id']}: {e}")


async def main():
    await init_db()
    cookies = None
    if settings.HH_COOKIES_FILE:
        cookies = load_cookies_from_netscape(settings.HH_COOKIES_FILE)
    async with BrowserManager(headless=settings.PLAYWRIGHT_HEADLESS, cookies=cookies) as browser_manager:
        list_parser = HHVacancyListParser()
        detail_parser = HHVacancyDetailParser()
        filter_service = VacancyFilter()

        vacancies = await process_vacancies(
            list_parser, detail_parser, filter_service, browser_manager, settings.MAX_CONCURRENT
        )
        await save_vacancies(vacancies)
        # Доп. вывод или JSON можно сохранить по желанию


if __name__ == "__main__":
    asyncio.run(main())
