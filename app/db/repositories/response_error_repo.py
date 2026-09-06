from datetime import datetime, timedelta
from typing import Optional, List, Dict
from sqlalchemy import select, func, and_, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import ResponseError, Vacancy


class ResponseErrorRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, account_id: int, vacancy_id: str, error_text: str, error_type: Optional[str] = None):
        error = ResponseError(
            account_id=account_id,
            vacancy_id=vacancy_id,
            error_text=error_text,
            error_type=error_type
        )
        self.session.add(error)
        await self.session.commit()
        return error

    async def count_for_account_last_hour(self, account_id: int, now: datetime) -> int:
        if now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        one_hour_ago = now - timedelta(hours=1)
        stmt = select(func.count(ResponseError.id)).where(
            and_(
                ResponseError.account_id == account_id,
                ResponseError.created_at >= one_hour_ago
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar()

    async def get_unreported_errors(self, account_id: int) -> List[Dict]:
        stmt = (
            select(ResponseError, Vacancy.url, Vacancy.title)
            .join(Vacancy, ResponseError.vacancy_id == Vacancy.hh_id)
            .where(and_(ResponseError.account_id == account_id, ResponseError.reported == False))
            .order_by(ResponseError.created_at.desc())
        )
        result = await self.session.execute(stmt)
        rows = result.all()
        return [
            {
                "id": err.id,
                "vacancy_id": err.vacancy_id,
                "title": title,
                "url": url,
                "error_text": err.error_text,
                "error_type": err.error_type,
                "created_at": err.created_at,
            }
            for err, url, title in rows
        ]

    async def mark_as_reported(self, error_ids: List[int]):
        stmt = (
            update(ResponseError)
            .where(ResponseError.id.in_(error_ids))
            .values(reported=True, reported_at=func.now())
        )
        await self.session.execute(stmt)
        await self.session.commit()