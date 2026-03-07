# app/services/hh_parser.py

import asyncio
import logging
from typing import List, Dict
from playwright.async_api import BrowserContext

logger = logging.getLogger(__name__)


async def fetch_vacancy_ids(
        search_url: str,
        max_pages: int,
        context: BrowserContext
) -> List[Dict[str, str]]:
    """
    Собирает ID, название и ссылку на вакансию со страниц поиска.
    Использует переданный контекст браузера.
    """
    vacancies = []
    page = await context.new_page()

    try:
        for page_num in range(max_pages):
            url = f"{search_url}&page={page_num}" if "?" in search_url else f"{search_url}?page={page_num}"
            logger.info(f"Переходим на страницу поиска: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

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

                title_elem = card.locator('[data-qa="serp-item__title-text"]')
                title = await title_elem.text_content() or "Без названия"

                link_elem = card.locator('[data-qa="serp-item__title"]')
                link = await link_elem.get_attribute('href')
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
        logger.debug("Страница поиска закрыта")

    return vacancies
