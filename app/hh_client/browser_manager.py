# app/hh_client/browser_manager.py
import asyncio
import logging
from typing import Optional, Union, List, Dict

from playwright.async_api import async_playwright, Browser, BrowserContext

from app.config import settings
from app.hh_client.cookie_loader import load_cookies_full
from app.utils.proxy_utils import normalize_proxy_server

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
        self.contexts: List[BrowserContext] = []

    async def start(self):
        """Запускает браузер (один экземпляр)."""
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-gpu',
            ]
        )
        logger.info(f"Browser started (headless={self.headless})")

    async def create_context(
            self,
            cookies_source: Optional[Union[str, List[dict]]] = None,
            proxy: Optional[dict] = None,
    ) -> BrowserContext:
        """
        Создаёт новый контекст и загружает cookies.

        cookies_source: путь к Netscape-файлу или список cookies в формате Playwright.
        proxy: словарь параметров прокси ИЛИ None (взять глобальный из settings) ИЛИ {} (без прокси).
        """
        if not self.browser:
            raise RuntimeError("Browser not started. Call start() first.")

        # --- Определяем прокси ---
        final_proxy = proxy

        if final_proxy is None and settings.PROXY_SERVER:
            server = normalize_proxy_server(settings.PROXY_SERVER)  # ← нормализация!
            final_proxy = {"server": server}
            if settings.PROXY_USERNAME:
                final_proxy["username"] = settings.PROXY_USERNAME
            if settings.PROXY_PASSWORD:
                final_proxy["password"] = settings.PROXY_PASSWORD
            logger.info(f"🌐 Using global proxy: {server}")

        context_options = {}
        if final_proxy:
            context_options["proxy"] = final_proxy
            logger.info(
                f"🌐 Context proxy: {final_proxy.get('server')} "
                f"(auth={'yes' if 'username' in final_proxy else 'no'})"
            )
        else:
            logger.info("🌐 Context без прокси (direct)")

        context = await self.browser.new_context(**context_options)

        # --- Дефолтные таймауты ---
        context.set_default_timeout(20000)  # 20 сек
        context.set_default_navigation_timeout(45000)  # 45 сек

        # --- Загружаем cookies ---
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
        logger.info("Browser stopped")

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()
