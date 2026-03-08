import json
from typing import Optional

from sqlalchemy import insert, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Vacancy, AccountVacancy
from app.hh_client.interfaces import IVacancyRepository
from sqlalchemy import select, and_, not_


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
        stmt = select(AccountVacancy).where(
            and_(
                AccountVacancy.account_id == account_id,
                AccountVacancy.hh_id == hh_id
            )
        )
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            existing.responded = True
            existing.response_status = status
            existing.response_date = func.now()
        else:
            new = AccountVacancy(
                account_id=account_id,
                hh_id=hh_id,
                responded=True,
                response_status=status,
                response_date=func.now()
            )
            self.session.add(new)
        await self.session.commit()

    async def get_random_unresponded(self, account_id: int) -> Optional[Vacancy]:
        """Вернуть случайную вакансию, на которую аккаунт ещё не откликался (нет записи в account_vacancy)"""
        # Подзапрос: все hh_id, которые уже есть в account_vacancy для данного account_id
        subquery = select(AccountVacancy.hh_id).where(AccountVacancy.account_id == account_id).scalar_subquery()
        stmt = select(Vacancy).where(not_(Vacancy.hh_id.in_(subquery))).order_by(func.random()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

