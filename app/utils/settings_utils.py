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
