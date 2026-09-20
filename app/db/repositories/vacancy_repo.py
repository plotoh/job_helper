# app/db/repositories/vacancy_repo.py
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import and_, desc, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import AccountVacancy, Vacancy
from app.hh_client.base_interfaces import IVacancyRepository

logger = logging.getLogger(__name__)


class VacancyRepository(IVacancyRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_if_not_exists(self, vacancy: dict, commit: bool = True) -> bool:
        """
        :param commit: True — коммит сразу (для одиночных вызовов).
                       False — коммит делает вызывающий (для батчевой вставки в _run_parsing).
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

        # skills — уже list/None. JSON-колонка сериализует сама.
        # json.dumps здесь давал двойную сериализацию → в БД лежала строка "[...]".
        skills = vacancy.get('skills') or []

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
            level=vacancy.get('level', 'regular'),
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

        # datetime.now() — Python-значение. func.now() = SQL-функция,
        # которую нельзя присваивать ORM-атрибуту без update().
        now = datetime.now()
        if existing:
            existing.responded = True
            existing.response_status = status
            existing.response_date = now
        else:
            self.session.add(AccountVacancy(
                account_id=account_id,
                hh_id=hh_id,
                responded=True,
                response_status=status,
                response_date=now,
            ))
        if commit:
            await self.session.commit()

    async def is_test_required_by_any(self, hh_id: str) -> bool:
        """True, если хоть один аккаунт уже получил test_required на эту вакансию."""
        stmt = (
            select(AccountVacancy.hh_id)
            .where(
                and_(
                    AccountVacancy.hh_id == hh_id,
                    AccountVacancy.response_status == 'test_required',
                )
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    # async def get_random_unresponded(
    #         self,
    #         account_id: int,
    #         include_junior: bool,
    #         include_senior: bool,
    # ) -> Optional[Vacancy]:
    #     """
    #     NOT EXISTS вместо NOT IN (scalar_subquery) — на больших таблицах
    #     PostgreSQL это значительно эффективнее.
    #     """
    #     cutoff = datetime.now() - timedelta(days=settings.VACANCY_TTL_DAYS)
    #
    #     allowed_levels = ['regular']
    #     if include_junior:
    #         allowed_levels.append('junior')
    #     if include_senior:
    #         allowed_levels.append('senior')
    #
    #     already = (
    #         select(AccountVacancy.hh_id)
    #         .where(
    #             and_(
    #                 AccountVacancy.account_id == account_id,
    #                 AccountVacancy.hh_id == Vacancy.hh_id,
    #             )
    #         )
    #         .exists()
    #     )
    #
    #     stmt = (
    #         select(Vacancy)
    #         .where(
    #             and_(
    #                 ~already,
    #                 Vacancy.is_actual.is_(True),
    #                 Vacancy.used_count < settings.MAX_USES_PER_VACANCY,
    #                 Vacancy.created_at >= cutoff,
    #                 Vacancy.level.in_(allowed_levels),
    #             )
    #         )
    #         .order_by(desc(Vacancy.score), desc(Vacancy.created_at))
    #         .limit(1)
    #     )
    #     result = await self.session.execute(stmt)
    #     return result.scalar_one_or_none()

    async def get_random_unresponded(
            self,
            account_id: int,
            include_junior: bool,
            include_senior: bool,
            skip_test_required_by_others: bool = True,
    ) -> Optional[Vacancy]:
        cutoff = datetime.now() - timedelta(days=settings.VACANCY_TTL_DAYS)

        allowed_levels = ['regular']
        if include_junior:
            allowed_levels.append('junior')
        if include_senior:
            allowed_levels.append('senior')

        # Этот аккаунт ещё не откликался
        already_this_account = (
            select(AccountVacancy.hh_id)
            .where(and_(
                AccountVacancy.account_id == account_id,
                AccountVacancy.hh_id == Vacancy.hh_id,
            ))
            .exists()
        )

        # Кто-то (любой аккаунт) получил test_required
        test_by_any = (
            select(AccountVacancy.hh_id)
            .where(and_(
                AccountVacancy.hh_id == Vacancy.hh_id,
                AccountVacancy.response_status == 'test_required',
            ))
            .exists()
        )

        conditions = [
            ~already_this_account,
            Vacancy.is_actual.is_(True),
            Vacancy.used_count < settings.MAX_USES_PER_VACANCY,
            Vacancy.created_at >= cutoff,
            Vacancy.level.in_(allowed_levels),
        ]
        if skip_test_required_by_others:
            conditions.append(~test_by_any)

        stmt = (
            select(Vacancy)
            .where(and_(*conditions))
            .order_by(desc(Vacancy.score), desc(Vacancy.created_at))
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def increment_used_count(self, hh_id: str) -> int:
        stmt = (
            update(Vacancy)
            .where(Vacancy.hh_id == hh_id)
            .values(used_count=Vacancy.used_count + 1)
            .returning(Vacancy.used_count)
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.scalar_one()

    async def deactivate_old_vacancies(self, days: int) -> None:
        cutoff = datetime.now() - timedelta(days=days)
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
