# app/db/repositories/vacancy_repo.py
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import (
    and_,
    literal_column,
    or_,
    select,
    update,
    func,
    select as sa_select,
    not_,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import AccountVacancy, Vacancy
from app.hh_client.base_interfaces import IVacancyRepository
from app.utils.time_utils import msk_now

logger = logging.getLogger(__name__)

# =============================================================
# Freshness — отдельная шкала 0-100, не входит в score.
# Считается в Postgres, чтобы ORDER BY работал на стороне БД.
# ВАЖНО: таблица называется vacancies (без алиаса), поэтому
# в SQL пишем vacancies.created_at, а не v.created_at.
# =============================================================
_FRESHNESS_SQL = """
    CASE
        WHEN vacancies.created_at >= (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Moscow') - INTERVAL '1 day'  THEN 100
        WHEN vacancies.created_at >= (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Moscow') - INTERVAL '2 days' THEN 90
        WHEN vacancies.created_at >= (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Moscow') - INTERVAL '3 days' THEN 85
        WHEN vacancies.created_at >= (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Moscow') - INTERVAL '4 days' THEN 75
        WHEN vacancies.created_at >= (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Moscow') - INTERVAL '5 days' THEN 56
        WHEN vacancies.created_at >= (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Moscow') - INTERVAL '6 days' THEN 38
        WHEN vacancies.created_at >= (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Moscow') - INTERVAL '7 days' THEN 19
        ELSE 0
    END
"""


class VacancyRepository(IVacancyRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_if_not_exists(self, vacancy: dict, commit: bool = True) -> bool:
        """
        :param commit: True — коммит сразу (для одиночных вызовов).
                       False — коммит делает вызывающий (для батчевой вставки).
        """
        stmt = select(Vacancy.hh_id).where(Vacancy.hh_id == vacancy['id'])
        result = await self.session.execute(stmt)
        if result.scalar_one_or_none() is not None:
            logger.debug(f"Vacancy {vacancy['id']} already exists")
            return False

        # Безопасное извлечение experience
        exp = vacancy.get('experience')
        exp_min = exp_max = None
        if exp is not None:
            if isinstance(exp, (tuple, list)) and len(exp) == 2:
                exp_min, exp_max = exp
            else:
                logger.warning(f"Unexpected experience format for {vacancy['id']}: {exp}")

        skills = vacancy.get('skills') or []
        level = (vacancy.get('level') or 'regular').lower()

        new_vac = Vacancy(
            hh_id=vacancy['id'],
            title=vacancy['title'],
            url=vacancy['url'],
            salary_text=vacancy.get('salary'),
            experience_min=exp_min,
            experience_max=exp_max,
            work_format=vacancy.get('work_format'),
            score=vacancy.get('score', 0),
            skills=skills,
            is_actual=True,
            level=level,
        )
        self.session.add(new_vac)
        if commit:
            await self.session.commit()
        logger.info(f"Saved new vacancy {vacancy['id']} - {vacancy['title']}")
        return True

    async def mark_responded(
            self,
            account_id: int,
            hh_id: str,
            status: str = "success",
            commit: bool = True,
    ) -> None:
        stmt = select(AccountVacancy).where(
            and_(
                AccountVacancy.account_id == account_id,
                AccountVacancy.hh_id == hh_id,
            )
        )
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()

        # MSK naive, чтобы совпадало с created_at вакансий
        now_msk = msk_now().replace(tzinfo=None)

        if existing:
            existing.responded = True
            existing.response_status = status
            existing.response_date = now_msk
        else:
            self.session.add(AccountVacancy(
                account_id=account_id,
                hh_id=hh_id,
                responded=True,
                response_status=status,
                response_date=now_msk,
            ))
        if commit:
            await self.session.commit()

    async def get_random_unresponded(
            self,
            account_id: int,
            include_junior: bool,
            include_senior: bool,
            skip_test_required_by_others: bool = True,
    ):
        """
        Выбор вакансии:
          1) freshness DESC (свежие первыми),
          2) score DESC,
          3) random() — чтобы аккаунты не дрались за одну.
        Freshness считается в PostgreSQL, отдельно от score.
        """
        levels = ['regular']
        if include_junior:
            levels.append('junior')
        if include_senior:
            levels.append('senior')

        # literal_column — обёртка, у которой есть .desc()
        freshness_expr = literal_column(_FRESHNESS_SQL)

        # NOT EXISTS: вакансия уже откликнута этим аккаунтом
        already_responded = (
            sa_select(AccountVacancy.hh_id)
            .where(AccountVacancy.hh_id == Vacancy.hh_id)
            .where(AccountVacancy.account_id == account_id)
            .exists()
        )

        stmt = (
            sa_select(Vacancy)
            .where(Vacancy.is_actual == True)  # noqa: E712
            .where(Vacancy.used_count < settings.MAX_USES_PER_VACANCY)
            .where(Vacancy.level.in_(levels))
            .where(not_(already_responded))
        )

        if skip_test_required_by_others:
            test_by_others = (
                sa_select(AccountVacancy.hh_id)
                .where(AccountVacancy.hh_id == Vacancy.hh_id)
                .where(AccountVacancy.response_status == 'test_required')
                .exists()
            )
            stmt = stmt.where(not_(test_by_others))

        stmt = (
            stmt
            .order_by(
                freshness_expr.desc(),
                Vacancy.score.desc(),
                func.random(),
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )

        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def is_test_required_by_any(self, hh_id: str) -> bool:
        """True, если хоть один аккаунт уже получил test_required на эту вакансию."""
        stmt = (
            select(AccountVacancy.hh_id)
            .where(and_(
                AccountVacancy.hh_id == hh_id,
                AccountVacancy.response_status == 'test_required',
            ))
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def increment_used_count(self, hh_id: str, commit: bool = True) -> int:
        stmt = (
            update(Vacancy)
            .where(Vacancy.hh_id == hh_id)
            .values(used_count=Vacancy.used_count + 1)
            .returning(Vacancy.used_count)
        )
        result = await self.session.execute(stmt)
        if commit:
            await self.session.commit()
        return result.scalar_one()

    async def deactivate_old_vacancies(self, days: int) -> None:
        # MSK naive — совпадает с форматом created_at
        cutoff = msk_now().replace(tzinfo=None) - timedelta(days=days)
        stmt = (
            update(Vacancy)
            .where(
                or_(
                    Vacancy.created_at < cutoff,
                    Vacancy.used_count >= settings.MAX_USES_PER_VACANCY,
                )
            )
            .values(is_actual=False)
        )
        await self.session.execute(stmt)
        await self.session.commit()
