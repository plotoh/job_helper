import asyncio
import logging
from typing import Optional, Union, List, Dict
from playwright.async_api import async_playwright, Browser, BrowserContext

from app.services.cookie_loader import load_cookies_full

logger = logging.getLogger(__name__)


class BrowserManager:
    """
    Управляет браузером и контекстом.
    Параметр cookies может быть:
      - строкой (путь к файлу cookies) – тогда они загружаются через load_cookies_full;
      - списком словарей (готовые cookies для Playwright);
      - None – cookies не добавляются.
    """

    def __init__(self, headless: bool = True, cookies: Optional[Union[str, List[dict]]] = None):
        self.headless = headless
        self.cookies_input = cookies
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None

    async def start(self):
        """Запускает браузер и создаёт контекст с куками."""
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-gpu'
            ]
        )
        # Не задаём кастомный user-agent – используется дефолтный Playwright
        self.context = await self.browser.new_context()

        # Загружаем cookies
        if self.cookies_input:
            if isinstance(self.cookies_input, str):
                cookies_list = load_cookies_full(self.cookies_input)
            else:
                cookies_list = self.cookies_input
            if cookies_list:
                await self.context.add_cookies(cookies_list)
                logger.info(f"Добавлено {len(cookies_list)} кук в контекст")
        else:
            logger.info("Cookies не переданы, работаем без них")

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
