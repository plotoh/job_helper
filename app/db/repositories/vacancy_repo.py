import json
from typing import Optional
from sqlalchemy import select, func, and_, not_
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Vacancy, AccountVacancy
from app.hh_client.base_interfaces import IVacancyRepository
import logging

logger = logging.getLogger(__name__)


class VacancyRepository(IVacancyRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_if_not_exists(self, vacancy: dict) -> bool:
        # Проверяем существование
        stmt = select(Vacancy).where(Vacancy.hh_id == vacancy['id'])
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            logger.debug(f"Vacancy {vacancy['id']} already exists")
            return False

        # Безопасное извлечение experience
        exp = vacancy.get('experience')
        exp_min = exp_max = None
        if exp is not None:
            if isinstance(exp, (tuple, list)) and len(exp) == 2:
                exp_min, exp_max = exp
            else:
                logger.warning(f"Unexpected experience format for vacancy {vacancy['id']}: {exp}")

        # Безопасное извлечение skills
        skills = vacancy.get('skills')
        skills_json = json.dumps(skills) if skills is not None else '[]'

        # Создаём объект Vacancy (все поля, которые могут быть None, передаются как есть)
        new_vac = Vacancy(
            hh_id=vacancy['id'],
            title=vacancy['title'],
            url=vacancy['url'],
            salary_text=vacancy.get('salary'),
            experience_min=exp_min,
            experience_max=exp_max,
            work_format=vacancy.get('work_format'),
            skills=skills_json
        )
        self.session.add(new_vac)
        await self.session.commit()
        logger.info(f"Saved new vacancy {vacancy['id']} - {vacancy['title']}")
        return True

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
        subquery = select(AccountVacancy.hh_id).where(AccountVacancy.account_id == account_id).scalar_subquery()
        stmt = select(Vacancy).where(not_(Vacancy.hh_id.in_(subquery))).order_by(func.random()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
