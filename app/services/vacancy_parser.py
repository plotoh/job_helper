# app/services/vacancy_parser.py

import asyncio
import logging
import re
from typing import Dict, Any, Optional, Tuple
from playwright.async_api import BrowserContext

logger = logging.getLogger(__name__)


def _parse_experience(exp_text: str) -> Optional[Tuple[int, int]]:
    if not exp_text:
        return None
    text = exp_text.lower().strip()
    if "нет опыта" in text or "без опыта" in text:
        return (0, 0)
    numbers = re.findall(r'\d+', text)
    if len(numbers) == 2:
        return (int(numbers[0]), int(numbers[1]))
    elif len(numbers) == 1:
        num = int(numbers[0])
        return (num, num)
    else:
        return None


async def parse_vacancy_details(
        vacancy_url: str,
        context: BrowserContext
) -> Dict[str, Any]:
    """
    Открывает страницу вакансии и извлекает детали.
    Использует переданный контекст браузера.
    """
    details = {
        "salary": None,
        "experience": None,
        "work_format": None,
        "skills": []
    }
    page = await context.new_page()
    try:
        await page.goto(vacancy_url, wait_until="domcontentloaded", timeout=30000)

        # Обработка диалогов
        try:
            region_confirm = page.locator('button[data-qa="confirm-phone-region"]')
            if await region_confirm.count() > 0:
                await region_confirm.click()
                await asyncio.sleep(1)
            cookie_agree = page.locator('button[data-qa="cookies-policy-agree"]')
            if await cookie_agree.count() > 0:
                await cookie_agree.click()
                await asyncio.sleep(1)
        except Exception:
            pass

        await page.wait_for_selector('[data-qa="vacancy-title"]', timeout=15000, state="visible")
        await asyncio.sleep(2)

        # Зарплата
        salary_elem = page.locator('[data-qa="vacancy-salary"]')
        if await salary_elem.count() > 0:
            salary_text = await salary_elem.text_content()
            details["salary"] = salary_text.strip() if salary_text else None

        # Опыт
        exp_elem = page.locator('[data-qa="vacancy-experience"]')
        if await exp_elem.count() > 0:
            exp_text = await exp_elem.text_content()
            details["experience"] = _parse_experience(exp_text)

        # Формат работы
        format_elem = page.locator('[data-qa="vacancy-employment-mode"]')
        if await format_elem.count() > 0:
            work_format = await format_elem.text_content()
            details["work_format"] = work_format.strip() if work_format else None
        else:
            info_items = page.locator('[data-qa="vacancy-view-raw-info__item"]')
            count = await info_items.count()
            for i in range(count):
                item = info_items.nth(i)
                text = await item.text_content()
                if text and ("удален" in text.lower() or "гибрид" in text.lower() or "офис" in text.lower()):
                    details["work_format"] = text.strip()
                    break

        # Ключевые навыки
        skill_list = page.locator('[data-qa="vacancy-skill-list"]')
        if await skill_list.count() > 0:
            skill_items = skill_list.locator('[data-qa="bloko-tag__text"]')
            count = await skill_items.count()
            for i in range(count):
                skill_text = await skill_items.nth(i).text_content()
                if skill_text:
                    details["skills"].append(skill_text.strip())
        else:
            all_tags = page.locator('[data-qa="bloko-tag__text"]')
            count = await all_tags.count()
            for i in range(count):
                skill_text = await all_tags.nth(i).text_content()
                if skill_text:
                    details["skills"].append(skill_text.strip())

    except Exception as e:
        content = await page.content()
        logger.error(f"Ошибка при парсинге {vacancy_url}: {e}")
        logger.debug(f"Содержимое страницы (первые 2000 символов): {content[:2000]}")
        details["error"] = str(e)
    finally:
        await page.close()
        logger.debug(f"Страница вакансии {vacancy_url} закрыта")

    return details
