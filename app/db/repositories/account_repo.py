# app/db/repositories/account_repo.py
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account


class AccountRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(self, only_enabled: bool = False) -> list[Account]:
        stmt = select(Account).order_by(Account.id)
        if only_enabled:
            stmt = stmt.where(Account.is_enabled.is_(True))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get(self, account_id: int) -> Optional[Account]:
        return await self.session.get(Account, account_id)

    async def get_by_username(self, tg_username: str) -> Optional[Account]:
        stmt = select(Account).where(Account.tg_username == tg_username)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, data: dict) -> Account:
        acc = Account(**data)
        self.session.add(acc)
        await self.session.commit()
        await self.session.refresh(acc)
        return acc

    async def update(self, account_id: int, data: dict) -> Optional[Account]:
        acc = await self.session.get(Account, account_id)
        if not acc:
            return None
        for k, v in data.items():
            if k in ('id', 'created_at'):
                continue
            if hasattr(acc, k):
                setattr(acc, k, v)
        await self.session.commit()
        await self.session.refresh(acc)
        return acc

    async def delete(self, account_id: int) -> bool:
        acc = await self.session.get(Account, account_id)
        if not acc:
            return False
        await self.session.delete(acc)
        await self.session.commit()
        return True
