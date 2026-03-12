from datetime import datetime, date
import zoneinfo

MSK_TZ = zoneinfo.ZoneInfo("Europe/Moscow")


def msk_now() -> datetime:
    """Возвращает текущее московское время с таймзоной."""
    return datetime.now(MSK_TZ)


def msk_today() -> date:
    """Возвращает сегодняшнюю дату по московскому времени."""
    return msk_now().date()
