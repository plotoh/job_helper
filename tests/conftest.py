import asyncio
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.db.models import Base
from app.config import settings

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


# engine теперь создаётся для каждой тестовой функции (scope="function")
@pytest.fixture(scope="function")
async def engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine):
    # Создаём сессию без begin() – управление транзакциями оставляем тестам
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
        # Откатываем незакоммиченные изменения после теста
        await session.rollback()
        await session.close()


@pytest.fixture
def mock_browser_context(mocker):
    context = mocker.AsyncMock()
    context.new_page = mocker.AsyncMock(return_value=mocker.AsyncMock())
    return context


@pytest.fixture
def mock_page(mocker):
    page = mocker.AsyncMock()
    page.locator.return_value.count = mocker.AsyncMock(return_value=1)
    page.locator.return_value.text_content = mocker.AsyncMock(return_value="text")
    page.locator.return_value.get_attribute = mocker.AsyncMock(return_value="https://hh.ru/vacancy/123")
    page.goto = mocker.AsyncMock()
    page.wait_for_selector = mocker.AsyncMock()
    page.close = mocker.AsyncMock()

    return page
