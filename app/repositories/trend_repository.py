import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trends.models import Trend


class TrendRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, trend_id: uuid.UUID) -> Trend | None:
        result = await self.db.execute(select(Trend).where(Trend.id == trend_id))
        return result.scalar_one_or_none()

    async def list_by_project(
        self,
        project_id: uuid.UUID,
        keyword: str | None = None,
        platform: str | None = None,
        limit: int = 50,
    ) -> list[Trend]:
        stmt = (
            select(Trend)
            .where(Trend.project_id == project_id)
            .order_by(Trend.period_start.desc())
            .limit(limit)
        )
        if keyword:
            stmt = stmt.where(Trend.keyword == keyword)
        if platform:
            stmt = stmt.where(Trend.platform == platform)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def create(self, trend: Trend) -> Trend:
        self.db.add(trend)
        await self.db.flush()
        await self.db.refresh(trend)
        return trend

    async def upsert(self, trend: Trend) -> Trend:
        """Insert atau update trend berdasarkan project+keyword+platform+period."""
        existing = await self.db.execute(
            select(Trend).where(
                Trend.project_id == trend.project_id,
                Trend.keyword == trend.keyword,
                Trend.platform == trend.platform,
                Trend.period_start == trend.period_start,
            )
        )
        existing_trend = existing.scalar_one_or_none()
        if existing_trend:
            existing_trend.post_count = trend.post_count
            existing_trend.sentiment_score = trend.sentiment_score
            existing_trend.data = trend.data
            await self.db.flush()
            return existing_trend
        return await self.create(trend)
