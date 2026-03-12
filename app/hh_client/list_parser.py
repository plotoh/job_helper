# app/hh_client/list_parser.py
import asyncio
import logging
from typing import List, Dict
from playwright.async_api import BrowserContext
from app.hh_client.interfaces import IVacancyListParser

logger = logging.getLogger(__name__)


class HHVacancyListParser(IVacancyListParser):
    async def fetch_ids(self, url: str, page: int, context: BrowserContext) -> List[Dict[str, str]]:
        vacancies = []
        page_url = f"{url}&page={page}" if "?" in url else f"{url}?page={page}"
        logger.info(f"Переход на страницу поиска: {page_url}")
        page_obj = await context.new_page()
        try:
            await page_obj.goto(page_url, wait_until="domcontentloaded", timeout=30000)
            try:
                await page_obj.wait_for_selector('[data-qa="vacancy-serp__vacancy"]', timeout=10000)
            except:
                logger.warning("Карточки не найдены, страница пуста")
                return []
            await asyncio.sleep(2)
            cards = page_obj.locator('[data-qa="vacancy-serp__vacancy"]')
            count = await cards.count()
            logger.info(f"Найдено вакансий на странице {page}: {count}")
            for i in range(count):
                card = cards.nth(i)
                title = await card.locator('[data-qa="serp-item__title-text"]').text_content() or "Без названия"
                link = await card.locator('[data-qa="serp-item__title"]').get_attribute('href')
                if link and link.startswith("/"):
                    link = "https://hh.ru" + link
                vacancy_id = None
                if link and "/vacancy/" in link:
                    vacancy_id = link.split("/vacancy/")[-1].split("?")[0]
                vacancies.append({
                    "id": vacancy_id,
                    "title": title.strip(),
                    "url": link,
                })
        finally:
            await page_obj.close()
        return vacancies
