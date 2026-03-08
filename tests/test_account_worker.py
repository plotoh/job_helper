# tests/test_account_worker.py
import pytest
from datetime import datetime, time, timedelta
from unittest.mock import AsyncMock, patch
from app.scheduler.account_worker import AccountWorker, WORK_START, WORK_END
from app.db.models import AccountDailyState


@pytest.fixture
def worker(mock_browser_context):
    return AccountWorker(
        account_id=1,
        resume_id="res123",
        browser_context=mock_browser_context,
        template="Hello {vacancy_name}"
    )


async def test_check_pause(worker):
    pauses = [{"start": "12:00", "end": "13:00"}]
    # внутри паузы
    inside = time(12, 30)
    in_pause, end = worker._check_pause(inside, pauses)
    assert in_pause is True
    assert end == time(13, 0)
    # вне паузы
    outside = time(14, 0)
    in_pause, end = worker._check_pause(outside, pauses)
    assert in_pause is False
    assert end is None


async def test_next_pause_start(worker):
    pauses = [{"start": "12:00", "end": "13:00"}, {"start": "15:00", "end": "16:00"}]
    current = time(13, 30)
    next_start = worker._next_pause_start(current, pauses)
    assert next_start == time(15, 0)
    current = time(16, 30)
    next_start = worker._next_pause_start(current, pauses)
    assert next_start is None


async def test_do_response_success(worker, mocker):
    # мок репозиториев
    mock_vacancy_repo = mocker.AsyncMock()
    mock_vacancy_repo.get_random_unresponded.return_value = mocker.Mock(hh_id="123", url="url", title="Title")
    mock_error_repo = mocker.AsyncMock()
    mock_state_repo = mocker.AsyncMock()
    mock_state_repo.increment_done.return_value = 5

    mock_session = mocker.AsyncMock()
    mocker.patch('app.scheduler.account_worker.VacancyRepository', return_value=mock_vacancy_repo)
    mocker.patch('app.scheduler.account_worker.ResponseErrorRepository', return_value=mock_error_repo)
    mocker.patch('app.scheduler.account_worker.AccountStateRepository', return_value=mock_state_repo)

    worker.response_service.respond_to_vacancy = AsyncMock(return_value=True)

    state = mocker.Mock(daily_limit=10)
    await worker._do_response(mock_session, state, datetime.now().date())

    mock_state_repo.increment_done.assert_awaited_once()
    mock_vacancy_repo.mark_responded.assert_awaited_once_with(1, "123", status="success")


async def test_do_response_failure(worker, mocker):
    mock_vacancy_repo = mocker.AsyncMock()
    mock_vacancy_repo.get_random_unresponded.return_value = mocker.Mock(
        hh_id="123",
        url="url",
        title="Python Developer"  # обязательно строка
    )
    mock_error_repo = mocker.AsyncMock()
    mock_state_repo = mocker.AsyncMock()

    mocker.patch('app.scheduler.account_worker.VacancyRepository', return_value=mock_vacancy_repo)
    mocker.patch('app.scheduler.account_worker.ResponseErrorRepository', return_value=mock_error_repo)
    mocker.patch('app.scheduler.account_worker.AccountStateRepository', return_value=mock_state_repo)

    worker.response_service.respond_to_vacancy = AsyncMock(return_value=False)

    mock_session = mocker.AsyncMock()
    state = mocker.Mock(daily_limit=10)
    await worker._do_response(mock_session, state, datetime.now().date())

    mock_error_repo.add.assert_awaited_once()
    mock_state_repo.increment_done.assert_not_called()
