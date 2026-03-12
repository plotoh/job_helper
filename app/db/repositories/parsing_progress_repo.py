from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import ParsingProgress


class ParsingProgressRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(self, search_url: str) -> ParsingProgress:
        stmt = select(ParsingProgress).where(ParsingProgress.search_url == search_url)
        result = await self.session.execute(stmt)
        progress = result.scalar_one_or_none()
        if not progress:
            progress = ParsingProgress(search_url=search_url, last_page=0)
            self.session.add(progress)
            await self.session.commit()
            await self.session.refresh(progress)
        return progress

    async def update_last_page(self, search_url: str, page: int):
        stmt = update(ParsingProgress).where(ParsingProgress.search_url == search_url).values(last_page=page)
        await self.session.execute(stmt)
        await self.session.commit()
