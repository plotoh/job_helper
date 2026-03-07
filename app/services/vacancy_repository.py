import json
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Vacancy
from app.services.interfaces import IVacancyRepository


class VacancyRepository(IVacancyRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_if_not_exists(self, vacancy: dict) -> bool:
        stmt = insert(Vacancy).values(
            hh_id=vacancy['id'],
            title=vacancy['title'],
            url=vacancy['url'],
            salary_text=vacancy.get('salary'),
            experience_min=vacancy.get('experience', (None, None))[0],
            experience_max=vacancy.get('experience', (None, None))[1],
            work_format=vacancy.get('work_format'),
            skills=json.dumps(vacancy.get('skills', []))
        ).on_conflict_do_nothing(index_elements=['hh_id'])
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.rowcount > 0
