from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import ResponseError


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
        """Количество ошибок для аккаунта за последний час"""
        one_hour_ago = now - timedelta(hours=1)
        stmt = select(func.count(ResponseError.id)).where(
            and_(
                ResponseError.account_id == account_id,
                ResponseError.created_at >= one_hour_ago
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar()