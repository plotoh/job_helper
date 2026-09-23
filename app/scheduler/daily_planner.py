# app/scheduler/daily_planner.py
import random
from typing import List, Dict

from app.config import settings


def generate_pauses(
        work_start_hour: int = None,
        work_start_minute: int = None,
        work_end_hour: int = None,
        work_end_minute: int = None,
        pause_count: int = None,
        pause_min: int = None,
        pause_max: int = None,
        min_gap: int = None,
) -> List[Dict[str, str]]:
    work_start_hour = work_start_hour if work_start_hour is not None else settings.WORK_START_HOUR
    work_start_minute = work_start_minute if work_start_minute is not None else settings.WORK_START_MINUTE
    work_end_hour = work_end_hour if work_end_hour is not None else settings.WORK_END_HOUR
    work_end_minute = work_end_minute if work_end_minute is not None else settings.WORK_END_MINUTE
    pause_count = pause_count if pause_count is not None else settings.PAUSE_COUNT
    pause_min = pause_min if pause_min is not None else settings.PAUSE_MIN_MINUTES
    pause_max = pause_max if pause_max is not None else settings.PAUSE_MAX_MINUTES
    min_gap = min_gap if min_gap is not None else settings.MIN_GAP_MINUTES

    start_minutes = work_start_hour * 60 + work_start_minute
    end_minutes = work_end_hour * 60 + work_end_minute

    pauses = []
    attempts = 0
    max_attempts = 1000
    while len(pauses) < pause_count and attempts < max_attempts:
        durations = [random.randint(pause_min, pause_max) for _ in range(pause_count)]
        temp_pauses = []
        current_start = start_minutes
        valid = True
        for i in range(pause_count):
            max_start = end_minutes - durations[i]
            if current_start > max_start:
                valid = False
                break
            start = random.randint(current_start, max_start)
            end = start + durations[i]
            temp_pauses.append({"start": start, "end": end})
            current_start = end + min_gap
        if valid:
            pauses = [
                {
                    "start": f"{p['start'] // 60:02d}:{p['start'] % 60:02d}",
                    "end": f"{p['end'] // 60:02d}:{p['end'] % 60:02d}",
                }
                for p in temp_pauses
            ]
            break
        attempts += 1

    if not pauses:
        step = (end_minutes - start_minutes) // (pause_count + 1) if pause_count else 0
        for i in range(1, pause_count + 1):
            start = start_minutes + i * step - pause_min // 2
            end = start + pause_min
            pauses.append({
                "start": f"{start // 60:02d}:{start % 60:02d}",
                "end": f"{end // 60:02d}:{end % 60:02d}",
            })
    return pauses


def generate_daily_limit(min_limit: int = None, max_limit: int = None) -> int:
    min_limit = min_limit if min_limit is not None else settings.DAILY_LIMIT_MIN
    max_limit = max_limit if max_limit is not None else settings.DAILY_LIMIT_MAX
    return random.randint(min_limit, max_limit)
