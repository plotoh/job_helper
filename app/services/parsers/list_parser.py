import asyncio
import logging
from typing import List, Dict
from playwright.async_api import BrowserContext
from app.services.interfaces import IVacancyListParser

logger = logging.getLogger(__name__)


class HHVacancyListParser(IVacancyListParser):
    async def fetch_ids(self, url: str, max_pages: int, context: BrowserContext) -> List[Dict[str, str]]:
        vacancies = []
        page = await context.new_page()
        try:
            for page_num in range(max_pages):
                page_url = f"{url}&page={page_num}" if "?" in url else f"{url}?page={page_num}"
                logger.info(f"Переход на страницу поиска: {page_url}")
                await page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
                try:
                    await page.wait_for_selector('[data-qa="vacancy-serp__vacancy"]', timeout=10000)
                except:
                    logger.warning("Карточки не найдены, завершаем пагинацию")
                    break
                await asyncio.sleep(2)
                cards = page.locator('[data-qa="vacancy-serp__vacancy"]')
                count = await cards.count()
                logger.info(f"Найдено вакансий на странице {page_num + 1}: {count}")
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
                await asyncio.sleep(3)
        finally:
            await page.close()
        return vacancies
