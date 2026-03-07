import json
from sqlalchemy import insert, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Vacancy, AccountVacancy
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

    async def mark_responded(self, account_id: int, hh_id: str, status: str = "success"):
        stmt = insert(AccountVacancy).values(
            account_id=account_id,
            hh_id=hh_id,
            responded=True,
            response_status=status,
            response_date=func.now()
        ).on_conflict_do_update(
            index_elements=['account_id', 'hh_id'],
            set_={'responded': True, 'response_status': status, 'response_date': func.now()}
        )
        await self.session.execute(stmt)
        await self.session.commit()
