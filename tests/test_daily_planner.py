# tests/test_daily_planner.py
import pytest
from app.config import settings
from app.scheduler.daily_planner import generate_pauses, generate_daily_limit


def test_generate_daily_limit():
    limit = generate_daily_limit()
    assert settings.DAILY_LIMIT_MIN <= limit <= settings.DAILY_LIMIT_MAX


def test_generate_pauses():
    pauses = generate_pauses()
    assert len(pauses) == settings.PAUSE_COUNT
    for p in pauses:
        assert "start" in p and "end" in p
        # Проверка формата HH:MM
        start_h, start_m = map(int, p["start"].split(":"))
        end_h, end_m = map(int, p["end"].split(":"))
        assert 0 <= start_h <= 23 and 0 <= start_m <= 59
        assert 0 <= end_h <= 23 and 0 <= end_m <= 59
        # Проверка, что пауза не выходит за рабочий день
        assert (start_h * 60 + start_m) >= (settings.WORK_START_HOUR * 60 + settings.WORK_START_MINUTE)
        assert (end_h * 60 + end_m) <= (settings.WORK_END_HOUR * 60 + settings.WORK_END_MINUTE)
