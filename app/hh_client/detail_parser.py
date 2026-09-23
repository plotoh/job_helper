# app/hh_client/detail_parser.py
import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from playwright.async_api import BrowserContext

from app.hh_client.interfaces import IVacancyDetailParser

logger = logging.getLogger(__name__)


def _parse_experience(exp_text: str) -> Optional[Tuple[int, int]]:
    if not exp_text:
        return None
    text = exp_text.lower().strip()
    if "нет опыта" in text or "без опыта" in text:
        return 0, 0
    numbers = re.findall(r'\d+', text)
    if len(numbers) == 2:
        return int(numbers[0]), int(numbers[1])
    elif len(numbers) == 1:
        num = int(numbers[0])
        return num, num
    return None


class HHVacancyDetailParser(IVacancyDetailParser):
    async def _handle_dialogs(self, page):
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

    async def _dump_page_debug(self, page, url: str):
        debug_dir = Path("logs/debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        m = re.search(r'/vacancy/(\d+)', url)
        vac_id = m.group(1) if m else "unknown"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        html_path = debug_dir / f"{ts}_detail_fail_{vac_id}.html"
        try:
            html = await page.content()
            html_path.write_text(html, encoding="utf-8")
            logger.info(f"🖼️ Debug сохранён: {html_path.name}")
        except Exception as e:
            logger.warning(f"Не удалось сохранить debug: {e}")

    async def parse(self, url: str, context: BrowserContext) -> Dict[str, Any]:
        details = {
            "salary": None,
            "experience": None,
            "work_format": None,
            "skills": [],
            "description": None,
        }
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await self._handle_dialogs(page)

            # --- Ожидаем заголовок по одному из селекторов ---
            title_found = False
            for sel in [
                '[data-qa="vacancy-title"]',
                'h1[data-qa="vacancy-title"]',
                'h1.bloko-header-section-1',
                'h1',
            ]:
                try:
                    await page.wait_for_selector(sel, timeout=10000, state="visible")
                    title_found = True
                    break
                except Exception:
                    continue

            if not title_found:
                logger.warning(f"Заголовок не найден на {url} — вероятно архив или регион")
                await self._dump_page_debug(page, url)
                return details

            await asyncio.sleep(1.5)

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
                    if text and ("удален" in text.lower() or "гибрид" in text.lower()
                                 or "офис" in text.lower()):
                        details["work_format"] = text.strip()
                        break

            # Навыки
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

            # Описание
            desc_elem = page.locator('[data-qa="vacancy-description"]')
            if await desc_elem.count() > 0:
                description = await desc_elem.text_content()
                details["description"] = description.strip() if description else None

        except Exception as e:
            logger.error(f"Ошибка при парсинге {url}: {e}")
            raise
        finally:
            await page.close()
        return details
