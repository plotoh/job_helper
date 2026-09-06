# tests/test_repositories.py
import pytest
from datetime import date, datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Account, Vacancy, AccountDailyState, ResponseError
from app.db.repositories.account_state_repo import AccountStateRepository
from app.db.repositories.response_error_repo import ResponseErrorRepository
from app.db.repositories.vacancy_repo import VacancyRepository


@pytest.fixture
async def test_account(session: AsyncSession):
    acc = Account(id=1, tg_username="test", resume_id="res123")
    session.add(acc)
    await session.commit()
    return acc


@pytest.fixture
async def test_vacancy(session: AsyncSession):
    vac = Vacancy(hh_id="123", title="Python Developer", url="https://hh.ru/vacancy/123")
    session.add(vac)
    await session.commit()
    return vac


async def test_account_state_repo_create(session: AsyncSession, test_account):
    repo = AccountStateRepository(session)
    today = date.today()
    data = {"daily_limit": 100, "pauses": [{"start": "12:00", "end": "13:00"}], "status": "pending"}
    state = await repo.create_or_update(test_account.id, today, data)
    assert state.account_id == test_account.id
    assert state.daily_limit == 100
    assert state.pauses == [{"start": "12:00", "end": "13:00"}]


async def test_increment_done(session: AsyncSession, test_account):
    repo = AccountStateRepository(session)
    today = date.today()
    data = {"daily_limit": 10, "pauses": [], "done_count": 5}
    await repo.create_or_update(test_account.id, today, data)
    new_done = await repo.increment_done(test_account.id, today)
    assert new_done == 6


async def test_mark_completed(session: AsyncSession, test_account):
    repo = AccountStateRepository(session)
    today = date.today()
    data = {"daily_limit": 10, "pauses": [], "status": "active"}
    await repo.create_or_update(test_account.id, today, data)
    await repo.mark_completed(test_account.id, today)
    state = await repo.get_for_today(test_account.id, today)
    assert state.status == "completed"
    assert state.completed_at is not None


async def test_response_error_add(session: AsyncSession, test_account, test_vacancy):
    repo = ResponseErrorRepository(session)
    error = await repo.add(test_account.id, test_vacancy.hh_id, "Test error", "test_type")
    assert error.id is not None
    assert error.error_text == "Test error"


async def test_vacancy_repo_get_random_unresponded(session: AsyncSession, test_account, monkeypatch):
    # Устанавливаем настройки, чтобы вакансия гарантированно подходила
    monkeypatch.setattr(settings, 'VACANCY_TTL_DAYS', 100)
    monkeypatch.setattr(settings, 'MAX_USES_PER_VACANCY', 2)

    # Создаём свежую вакансию с правильными полями
    vac = Vacancy(
        hh_id="test123",
        title="Python Developer",
        url="https://hh.ru/vacancy/test123",
        used_count=0,
        is_actual=True,
        created_at=datetime.now()  # свежая дата
    )
    session.add(vac)
    await session.commit()

    repo = VacancyRepository(session)
    result = await repo.get_random_unresponded(test_account.id)
    assert result is not None
    assert result.hh_id == "test123"