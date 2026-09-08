# app/db/database.py
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.db.models import Base, Setting
from app.config import settings

engine = create_async_engine(str(settings.database_dsn), echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Создаём записи настроек, если их нет
    async with AsyncSessionLocal() as session:
        for key, default in [('responses_enabled', 'true'), ('force_parsing', 'false')]:
            stmt = select(Setting).where(Setting.key == key)
            result = await session.execute(stmt)
            if not result.scalar_one_or_none():
                session.add(Setting(key=key, value=default))
        await session.commit()