# tests/test_account_worker.py
import pytest
from datetime import datetime, time
from unittest.mock import AsyncMock

from app.scheduler.account_worker import AccountWorker


@pytest.fixture
def worker(mock_browser_context):
    return AccountWorker(
        account_id=1,
        resume_id="res123",
        browser_context=mock_browser_context,
        template="Hello {vacancy_name}",
    )


def test_check_pause(worker):
    pauses = [{"start": "12:00", "end": "13:00"}]
    in_pause, end = worker._check_pause(time(12, 30), pauses)
    assert in_pause is True
    assert end == time(13, 0)

    in_pause, end = worker._check_pause(time(14, 0), pauses)
    assert in_pause is False
    assert end is None


def test_next_pause_start(worker):
    pauses = [{"start": "12:00", "end": "13:00"}, {"start": "15:00", "end": "16:00"}]
    assert worker._next_pause_start(time(13, 30), pauses) == time(15, 0)
    assert worker._next_pause_start(time(16, 30), pauses) is None


@pytest.mark.parametrize("status,expect_done", [
    ("success", True),
    ("wo_soprovod", True),
    ("test_required", False),
    ("already_responded", False),
    ("failed", False),
])
async def test_do_response_statuses(worker, mocker, status, expect_done):
    mock_vacancy_repo = mocker.AsyncMock()
    mock_vacancy_repo.get_random_unresponded.return_value = mocker.Mock(
        hh_id="123", url="https://hh.ru/vacancy/123", title="Python Developer"
    )
    mock_error_repo = mocker.AsyncMock()
    mock_state_repo = mocker.AsyncMock()
    mock_state_repo.increment_done.return_value = 5

    mocker.patch('app.scheduler.account_worker.VacancyRepository', return_value=mock_vacancy_repo)
    mocker.patch('app.scheduler.account_worker.ResponseErrorRepository', return_value=mock_error_repo)
    mocker.patch('app.scheduler.account_worker.AccountStateRepository', return_value=mock_state_repo)
    mocker.patch.object(worker, '_log_response', new=AsyncMock())

    worker.response_service.respond_to_vacancy = AsyncMock(return_value=status)

    mock_session = mocker.AsyncMock()
    state = mocker.Mock(daily_limit=10)
    await worker._do_response(mock_session, state, datetime.now().date())

    if expect_done:
        mock_state_repo.increment_done.assert_awaited_once()
    else:
        mock_state_repo.increment_done.assert_not_called()

    # Проверяем финальный статус в mark_responded
    final_statuses = [call.kwargs.get("status") for call in mock_vacancy_repo.mark_responded.await_args_list]
    assert status in final_statuses or (status == "success" and "success" in final_statuses)