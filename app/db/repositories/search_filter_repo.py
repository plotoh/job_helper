# app/db/repositories/search_filter_repo.py
from typing import Optional, List
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SearchFilter


class SearchFilterRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(self) -> List[SearchFilter]:
        stmt = select(SearchFilter).order_by(SearchFilter.is_active.desc(), SearchFilter.id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get(self, filter_id: int) -> Optional[SearchFilter]:
        stmt = select(SearchFilter).where(SearchFilter.id == filter_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active(self) -> Optional[SearchFilter]:
        stmt = select(SearchFilter).where(SearchFilter.is_active == True).limit(1)  # noqa: E712
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, name: str, url: str, is_active: bool = False) -> SearchFilter:
        if is_active:
            await self._deactivate_all()
        new = SearchFilter(name=name, url=url, is_active=is_active)
        self.session.add(new)
        await self.session.commit()
        await self.session.refresh(new)
        return new

    async def update(self, filter_id: int, name: str = None, url: str = None) -> Optional[SearchFilter]:
        obj = await self.get(filter_id)
        if not obj:
            return None
        if name is not None:
            obj.name = name
        if url is not None:
            obj.url = url
        await self.session.commit()
        await self.session.refresh(obj)
        return obj

    async def activate(self, filter_id: int) -> Optional[SearchFilter]:
        obj = await self.get(filter_id)
        if not obj:
            return None
        await self._deactivate_all()
        obj.is_active = True
        await self.session.commit()
        await self.session.refresh(obj)
        return obj

    async def delete(self, filter_id: int) -> bool:
        obj = await self.get(filter_id)
        if not obj:
            return False
        await self.session.delete(obj)
        await self.session.commit()
        return True

    async def _deactivate_all(self) -> None:
        stmt = update(SearchFilter).where(SearchFilter.is_active == True).values(is_active=False)  # noqa: E712
        await self.session.execute(stmt)