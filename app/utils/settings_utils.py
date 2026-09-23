from app.db import AsyncSessionLocal
from app.db.models import Setting
from sqlalchemy import select


async def get_setting(key: str, default: str = None) -> str:
    async with AsyncSessionLocal() as session:
        stmt = select(Setting).where(Setting.key == key)
        result = await session.execute(stmt)
        setting = result.scalar_one_or_none()
        return setting.value if setting else default


async def set_setting(key: str, value: str) -> None:
    async with AsyncSessionLocal() as session:
        stmt = select(Setting).where(Setting.key == key)
        result = await session.execute(stmt)
        setting = result.scalar_one_or_none()
        if setting:
            setting.value = value
        else:
            setting = Setting(key=key, value=value)
            session.add(setting)
        await session.commit()


async def get_include_junior() -> bool:
    val = await get_setting('include_junior', 'true')
    return val == 'true'


async def get_include_senior() -> bool:
    val = await get_setting('include_senior', 'true')
    return val == 'true'


def effective_settings(account, settings) -> dict:
    """Возвращает настройки аккаунта с fallback на глобальные."""
    return {
        "work_start_hour": account.work_start_hour or settings.WORK_START_HOUR,
        "work_start_minute": account.work_start_minute if account.work_start_minute is not None else settings.WORK_START_MINUTE,
        "work_end_hour": account.work_end_hour or settings.WORK_END_HOUR,
        "work_end_minute": account.work_end_minute if account.work_end_minute is not None else settings.WORK_END_MINUTE,
        "pause_count": account.pause_count or settings.PAUSE_COUNT,
        "pause_min_minutes": account.pause_min_minutes or settings.PAUSE_MIN_MINUTES,
        "pause_max_minutes": account.pause_max_minutes or settings.PAUSE_MAX_MINUTES,
        "min_gap_minutes": account.min_gap_minutes or settings.MIN_GAP_MINUTES,
        "daily_limit_min": account.daily_limit_min or settings.DAILY_LIMIT_MIN,
        "daily_limit_max": account.daily_limit_max or settings.DAILY_LIMIT_MAX,
    }
