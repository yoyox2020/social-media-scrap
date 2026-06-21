"""
TrendAgent — menghitung tren volume post dan sentimen per periode waktu.
"""
from __future__ import annotations

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.posts.models import Post
from app.domain.sentiments.models import Sentiment
from app.services.agents.base import BaseAgent
from app.services.agents.schemas import AgentContext, AgentResult

_VALID_PERIODS = {"day", "week", "month"}


class TrendAgent(BaseAgent):
    name = "trend"
    description = "Menghitung tren volume post dan sentimen per hari/minggu/bulan"

    def __init__(self, db: AsyncSession, period: str = "day"):
        super().__init__(db)
        self.period = period if period in _VALID_PERIODS else "day"

    async def run(self, context: AgentContext) -> AgentResult:
        try:
            volume_trend = await self._get_volume_trend(context)
            sentiment_trend = await self._get_sentiment_trend(context)
            platform_breakdown = await self._get_platform_breakdown(context)

            if not volume_trend:
                return self._ok(
                    data={"volume": [], "sentiment": [], "platforms": {}},
                    summary="Belum cukup data untuk menampilkan tren.",
                )

            total = sum(p["count"] for p in volume_trend)
            periods = len(volume_trend)
            avg_per_period = round(total / periods, 1) if periods > 0 else 0

            if periods >= 2:
                half = periods // 2
                first_half = sum(p["count"] for p in volume_trend[:half])
                second_half = sum(p["count"] for p in volume_trend[half:])
                direction = "naik" if second_half > first_half else "turun"
            else:
                direction = "stabil"

            summary = (
                f"Volume post {direction} dalam {periods} periode "
                f"(rata-rata {avg_per_period} post/{self.period}). "
                f"Total: {total} post."
            )
            if platform_breakdown:
                top_platform = max(platform_breakdown, key=platform_breakdown.get)
                summary += f" Platform dominan: {top_platform}."

            return self._ok(
                data={
                    "volume_trend": volume_trend,
                    "sentiment_trend": sentiment_trend,
                    "platform_breakdown": platform_breakdown,
                    "period": self.period,
                    "total_posts": total,
                    "trend_direction": direction,
                },
                summary=summary,
            )
        except Exception as exc:
            return self._err(str(exc))

    async def _get_volume_trend(self, context: AgentContext) -> list[dict]:
        stmt = (
            select(
                func.date_trunc(self.period, Post.published_at).label("period"),
                func.count().label("count"),
            )
            .where(
                Post.keyword_id == context.keyword_id,
                Post.published_at.is_not(None),
            )
            .group_by(text("period"))
            .order_by(text("period ASC"))
        )
        if context.platform:
            stmt = stmt.where(Post.platform == context.platform)
        if context.date_from:
            stmt = stmt.where(Post.published_at >= context.date_from)
        if context.date_to:
            stmt = stmt.where(Post.published_at <= context.date_to)

        result = await self.db.execute(stmt)
        return [
            {
                "period": row.period.date().isoformat() if row.period else None,
                "count": row.count,
            }
            for row in result.all()
        ]

    async def _get_sentiment_trend(self, context: AgentContext) -> list[dict]:
        stmt = (
            select(
                func.date_trunc(self.period, Post.published_at).label("period"),
                Sentiment.label,
                func.count().label("count"),
            )
            .join(Sentiment, Sentiment.post_id == Post.id)
            .where(
                Post.keyword_id == context.keyword_id,
                Post.published_at.is_not(None),
            )
            .group_by(text("period"), Sentiment.label)
            .order_by(text("period ASC"))
        )
        if context.platform:
            stmt = stmt.where(Post.platform == context.platform)

        result = await self.db.execute(stmt)
        rows = result.all()

        trend: dict[str, dict] = {}
        for row in rows:
            key = row.period.date().isoformat() if row.period else "unknown"
            if key not in trend:
                trend[key] = {"period": key, "positive": 0, "negative": 0, "neutral": 0}
            trend[key][row.label] = row.count

        return list(trend.values())

    async def _get_platform_breakdown(self, context: AgentContext) -> dict[str, int]:
        result = await self.db.execute(
            select(Post.platform, func.count().label("count"))
            .where(Post.keyword_id == context.keyword_id)
            .group_by(Post.platform)
        )
        return {row.platform: row.count for row in result.all()}
