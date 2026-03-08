import random
from datetime import time
from typing import List, Dict

from app.config import settings


def generate_pauses() -> List[Dict[str, str]]:
    """
    Генерирует 3 непересекающиеся паузы в рабочем интервале.
    Возвращает список словарей [{"start": "HH:MM", "end": "HH:MM"}, ...]
    """
    # Конвертируем время в минуты от начала дня
    start_minutes = settings.WORK_START_HOUR * 60 + settings.WORK_START_MINUTE
    end_minutes = settings.WORK_END_HOUR * 60 + settings.WORK_END_MINUTE

    pauses = []
    attempts = 0
    max_attempts = 1000
    while len(pauses) < settings.PAUSE_COUNT and attempts < max_attempts:
        # Генерируем случайные длительности пауз
        durations = [random.randint(settings.PAUSE_MIN_MINUTES, settings.PAUSE_MAX_MINUTES)
                     for _ in range(settings.PAUSE_COUNT)]
        temp_pauses = []
        current_start = start_minutes
        valid = True
        for i in range(settings.PAUSE_COUNT):
            max_start = end_minutes - durations[i]
            if current_start > max_start:
                valid = False
                break
            start = random.randint(current_start, max_start)
            end = start + durations[i]
            temp_pauses.append({"start": start, "end": end})
            current_start = end + settings.MIN_GAP_MINUTES
        if valid:
            pauses = [
                {
                    "start": f"{p['start']//60:02d}:{p['start']%60:02d}",
                    "end": f"{p['end']//60:02d}:{p['end']%60:02d}"
                }
                for p in temp_pauses
            ]
            break
        attempts += 1

    if not pauses:
        # Запасной вариант
        step = (end_minutes - start_minutes) // (settings.PAUSE_COUNT + 1)
        for i in range(1, settings.PAUSE_COUNT + 1):
            start = start_minutes + i * step - settings.PAUSE_MIN_MINUTES // 2
            end = start + settings.PAUSE_MIN_MINUTES
            pauses.append({
                "start": f"{start//60:02d}:{start%60:02d}",
                "end": f"{end//60:02d}:{end%60:02d}"
            })
    return pauses


def generate_daily_limit() -> int:
    """Генерирует случайный дневной лимит из диапазона."""
    return random.randint(settings.DAILY_LIMIT_MIN, settings.DAILY_LIMIT_MAX)