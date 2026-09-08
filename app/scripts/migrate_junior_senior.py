import asyncio
from app.db import AsyncSessionLocal
from app.db.models import JuniorVacancy, SeniorVacancy, Vacancy
from sqlalchemy import select

async def migrate():
    async with AsyncSessionLocal() as session:
        # Перенос junior
        juniors = await session.execute(select(JuniorVacancy))
        for row in juniors.scalars():
            exists = await session.execute(select(Vacancy).where(Vacancy.hh_id == row.hh_id))
            if not exists.scalar_one_or_none():
                new_vac = Vacancy(
                    hh_id=row.hh_id,
                    title=row.title,
                    url=row.url,
                    created_at=row.created_at,
                    level='junior',
                    is_actual=True,
                    used_count=0,
                    score=0
                )
                session.add(new_vac)
        # Аналогично для senior
        seniors = await session.execute(select(SeniorVacancy))
        for row in seniors.scalars():
            exists = await session.execute(select(Vacancy).where(Vacancy.hh_id == row.hh_id))
            if not exists.scalar_one_or_none():
                new_vac = Vacancy(
                    hh_id=row.hh_id,
                    title=row.title,
                    url=row.url,
                    created_at=row.created_at,
                    level='senior',
                    is_actual=True,
                    used_count=0,
                    score=0
                )
                session.add(new_vac)
        await session.commit()
        print("Миграция завершена")

if __name__ == '__main__':
    asyncio.run(migrate())