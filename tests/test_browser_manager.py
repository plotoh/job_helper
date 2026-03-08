import pytest
from unittest.mock import patch, AsyncMock
from app.hh_client.browser_manager import BrowserManager


async def test_browser_manager_start_stop():
    # Создаём моки
    mock_playwright = AsyncMock()
    mock_chromium = AsyncMock()
    mock_browser = AsyncMock()
    mock_chromium.launch.return_value = mock_browser
    mock_playwright.chromium = mock_chromium

    with patch('app.hh_client.browser_manager.async_playwright', return_value=mock_playwright):
        manager = BrowserManager(headless=True)
        await manager.start()
        assert manager.browser is mock_browser
        await manager.stop()
        mock_browser.close.assert_awaited_once()
        mock_playwright.stop.assert_awaited_once()


async def test_create_context():
    mock_playwright = AsyncMock()
    mock_chromium = AsyncMock()
    mock_browser = AsyncMock()
    mock_context = AsyncMock()
    mock_browser.new_context.return_value = mock_context
    mock_chromium.launch.return_value = mock_browser
    mock_playwright.chromium = mock_chromium

    with patch('app.hh_client.browser_manager.async_playwright', return_value=mock_playwright):
        manager = BrowserManager()
        await manager.start()
        cookies = [{"name": "test", "value": "1", "domain": ".hh.ru", "path": "/"}]
        context = await manager.create_context(cookies)
        assert context is mock_context
        mock_browser.new_context.assert_awaited_once()
        mock_context.add_cookies.assert_awaited_once_with(cookies)
