# app/services/browser_manager.py

import asyncio
import logging
from typing import Dict, Optional
from playwright.async_api import async_playwright, Browser, BrowserContext

logger = logging.getLogger(__name__)


class BrowserManager:
    """
    Управляет одним браузером и контекстом на всё время работы скрипта.
    """

    def __init__(self, headless: bool = True, cookies: Optional[Dict[str, str]] = None):
        self.headless = headless
        self.cookies = cookies
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None

    async def start(self):
        """Запускает браузер и создаёт контекст с куками."""
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless)
        self.context = await self.browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        if self.cookies:
            cookies_list = [
                {
                    "name": name,
                    "value": value,
                    "domain": ".hh.ru",
                    "path": "/",
                    "secure": False,
                    "httpOnly": False,
                    "sameSite": "Lax"
                }
                for name, value in self.cookies.items()
            ]
            await self.context.add_cookies(cookies_list)
            logger.info(f"Добавлено {len(cookies_list)} кук в контекст")

        logger.info("Браузер и контекст успешно запущены")

    async def stop(self):
        """Закрывает браузер и останавливает playwright."""
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        logger.info("Браузер остановлен")

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()