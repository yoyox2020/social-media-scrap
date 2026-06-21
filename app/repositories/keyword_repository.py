"""All database queries for Keyword model live here."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.keywords.models import Keyword


class KeywordRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, keyword_id: uuid.UUID) -> Keyword | None:
        result = await self.db.execute(select(Keyword).where(Keyword.id == keyword_id))
        return result.scalar_one_or_none()

    async def list_by_project(self, project_id: uuid.UUID) -> list[Keyword]:
        result = await self.db.execute(
            select(Keyword).where(Keyword.project_id == project_id).order_by(Keyword.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_active_by_project(self, project_id: uuid.UUID) -> list[Keyword]:
        result = await self.db.execute(
            select(Keyword).where(Keyword.project_id == project_id, Keyword.is_active == True)  # noqa: E712
        )
        return list(result.scalars().all())

    async def create(self, keyword: Keyword) -> Keyword:
        self.db.add(keyword)
        await self.db.flush()
        await self.db.refresh(keyword)
        return keyword

    async def update(self, keyword: Keyword) -> Keyword:
        await self.db.flush()
        await self.db.refresh(keyword)
        return keyword

    async def delete(self, keyword_id: uuid.UUID) -> None:
        keyword = await self.get_by_id(keyword_id)
        if keyword:
            await self.db.delete(keyword)
            await self.db.flush()
