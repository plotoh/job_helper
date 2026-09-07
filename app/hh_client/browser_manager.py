import asyncio
import logging
from typing import Optional, Union, List, Dict
from playwright.async_api import async_playwright, Browser, BrowserContext

from app.config import settings
from app.hh_client.cookie_loader import load_cookies_full

logger = logging.getLogger(__name__)


class BrowserManager:
    """
    Управляет браузером и множественными контекстами.
    Предоставляет метод create_context для создания нового контекста с куками из файла.
    """

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.contexts: List[BrowserContext] = []  # для отслеживания созданных контекстов

    async def start(self):
        """Запускает браузер (один экземпляр)."""
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
        logger.info("Browser started")

    async def create_context(self, cookies_source: Optional[Union[str, List[dict]]] = None,
                             proxy: Optional[dict] = None) -> BrowserContext:
        """
        Создаёт новый контекст (изолированное окружение) и загружает в него куки.
        cookies_source: либо путь к файлу Netscape, либо список кук в формате Playwright.
        proxy: словарь с параметрами прокси (server, username, password) – опционально.
        Если proxy не передан, используется глобальный из настроек (settings.PROXY_SERVER).
        """
        if not self.browser:
            raise RuntimeError("Browser not started. Call start() first.")

        # Определяем прокси для этого контекста
        final_proxy = proxy
        if final_proxy is None and settings.PROXY_SERVER:
            final_proxy = {
                "server": settings.PROXY_SERVER,
            }
            if settings.PROXY_USERNAME:
                final_proxy["username"] = settings.PROXY_USERNAME
            if settings.PROXY_PASSWORD:
                final_proxy["password"] = settings.PROXY_PASSWORD

        context_options = {}
        if final_proxy:
            context_options["proxy"] = final_proxy

        context = await self.browser.new_context(**context_options)

        if cookies_source:
            if isinstance(cookies_source, str):
                cookies_list = load_cookies_full(cookies_source)
            else:
                cookies_list = cookies_source
            if cookies_list:
                await context.add_cookies(cookies_list)
                logger.info(f"Added {len(cookies_list)} cookies to new context")

        self.contexts.append(context)
        return context

    async def close_context(self, context: BrowserContext):
        """Закрывает конкретный контекст и удаляет из списка."""
        if context in self.contexts:
            await context.close()
            self.contexts.remove(context)

    async def stop(self):
        for ctx in self.contexts:
            try:
                await ctx.close()
            except Exception as e:
                logger.warning(f"Error closing context: {e}")
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()