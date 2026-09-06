import pytest
from unittest.mock import patch, AsyncMock
from app.hh_client.browser_manager import BrowserManager


async def test_browser_manager_start_stop():
    # Создаём вложенные моки
    mock_playwright = AsyncMock()
    mock_chromium = AsyncMock()
    mock_browser = AsyncMock()

    # Настраиваем цепочку: async_playwright() -> playwright_obj
    # playwright_obj.start() -> playwright_obj (возвращаем self)
    mock_playwright.start = AsyncMock(return_value=mock_playwright)
    mock_playwright.chromium = mock_chromium
    mock_chromium.launch.return_value = mock_browser

    with patch('app.hh_client.browser_manager.async_playwright', return_value=mock_playwright):
        manager = BrowserManager(headless=True)
        await manager.start()
        # Теперь manager.browser — это mock_browser
        assert manager.browser is mock_browser
        await manager.stop()
        mock_browser.close.assert_awaited_once()
        mock_playwright.stop.assert_awaited_once()


async def test_create_context():
    mock_playwright = AsyncMock()
    mock_chromium = AsyncMock()
    mock_browser = AsyncMock()
    mock_context = AsyncMock()

    mock_playwright.start = AsyncMock(return_value=mock_playwright)
    mock_playwright.chromium = mock_chromium
    mock_chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context

    with patch('app.hh_client.browser_manager.async_playwright', return_value=mock_playwright):
        # Отключаем прокси для теста
        with patch('app.hh_client.browser_manager.settings.PROXY_SERVER', None):
            manager = BrowserManager()
            await manager.start()
            cookies = [{"name": "test", "value": "1", "domain": ".hh.ru", "path": "/"}]
            with patch('app.hh_client.browser_manager.load_cookies_full', return_value=cookies):
                context = await manager.create_context(cookies)
            assert context is mock_context
            mock_browser.new_context.assert_awaited_once_with()  # теперь без прокси
            mock_context.add_cookies.assert_awaited_once_with(cookies)