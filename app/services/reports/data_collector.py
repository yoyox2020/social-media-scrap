"""Kumpulkan semua data yang dibutuhkan untuk membuat laporan."""

import uuid
from datetime import datetime

from sqlalchemy import Integer, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.models import Entity
from app.domain.keywords.models import Keyword
from app.domain.posts.models import Post
from app.domain.sentiments.models import Sentiment
from app.services.reports.schemas import (
    EntityData,
    ReportData,
    SentimentData,
    TrendData,
)


class ReportDataCollector:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def collect(
        self,
        keyword_id: uuid.UUID,
        report_id: uuid.UUID,
        title: str,
        period: str = "day",
        posts_sample_size: int = 5,
    ) -> ReportData:
        data = ReportData(
            report_id=report_id,
            keyword_id=keyword_id,
            generated_at=datetime.utcnow(),
            period=period,
        )

        await self._fill_keyword_info(data)
        data.title = title or f"Laporan: {data.keyword_text}"

        await self._fill_post_stats(data)
        await self._fill_sentiment(data)
        await self._fill_entities(data)
        await self._fill_trend(data)
        await self._fill_top_posts(data, posts_sample_size)

        return data

    # ── Private helpers ────────────────────────────────────────────────────────

    async def _fill_keyword_info(self, data: ReportData) -> None:
        result = await self.db.execute(
            select(Keyword).where(Keyword.id == data.keyword_id)
        )
        kw = result.scalar_one_or_none()
        if kw:
            data.keyword_text = kw.keyword

    async def _fill_post_stats(self, data: ReportData) -> None:
        raw = await self.db.execute(
            text("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(cleaned_content) AS processed,
                    SUM(CASE WHEN is_near_duplicate THEN 1 ELSE 0 END) AS dupes
                FROM posts
                WHERE keyword_id = :kid
            """),
            {"kid": data.keyword_id},
        )
        row = raw.first()
        if row:
            data.total_posts = row.total or 0
            data.processed_posts = row.processed or 0
            data.near_duplicates = row.dupes or 0

        # Language breakdown
        lang_result = await self.db.execute(
            text("""
                SELECT language, COUNT(*) AS cnt
                FROM posts
                WHERE keyword_id = :kid AND language IS NOT NULL
                GROUP BY language
            """),
            {"kid": data.keyword_id},
        )
        data.language_breakdown = {row.language: row.cnt for row in lang_result.all()}

    async def _fill_sentiment(self, data: ReportData) -> None:
        dist_result = await self.db.execute(
            text("""
                SELECT s.label, COUNT(*) AS cnt
                FROM sentiments s
                JOIN posts p ON p.id = s.post_id
                WHERE p.keyword_id = :kid
                GROUP BY s.label
            """),
            {"kid": data.keyword_id},
        )
        dist: dict[str, int] = {row.label: row.cnt for row in dist_result.all()}
        total = sum(dist.values())
        pct: dict[str, float] = {}
        if total > 0:
            pct = {label: round(cnt / total * 100, 1) for label, cnt in dist.items()}
        dominant = max(dist, key=lambda k: dist[k]) if dist else "neutral"

        # 1 example post per sentiment label
        examples: list[dict] = []
        for label in dist:
            ex_result = await self.db.execute(
                text("""
                    SELECT p.content, p.platform, p.url, s.score
                    FROM sentiments s
                    JOIN posts p ON p.id = s.post_id
                    WHERE p.keyword_id = :kid AND s.label = :label
                    LIMIT 1
                """),
                {"kid": data.keyword_id, "label": label},
            )
            ex = ex_result.first()
            if ex:
                examples.append({
                    "label": label,
                    "score": float(ex.score) if ex.score is not None else 0.0,
                    "platform": ex.platform,
                    "content": (ex.content or "")[:200],
                    "url": ex.url,
                })

        data.sentiment = SentimentData(
            distribution=dist,
            percentages=pct,
            dominant=dominant,
            total_analyzed=total,
            examples=examples,
        )

    async def _fill_entities(self, data: ReportData) -> None:
        entity_result = await self.db.execute(
            text("""
                SELECT e.text, e.entity_type, COUNT(*) AS cnt
                FROM entities e
                JOIN posts p ON p.id = e.post_id
                WHERE p.keyword_id = :kid
                GROUP BY e.text, e.entity_type
                ORDER BY cnt DESC
                LIMIT 100
            """),
            {"kid": data.keyword_id},
        )
        by_type: dict[str, list[dict]] = {}
        total_unique = 0
        seen: set[str] = set()
        for row in entity_result.all():
            key = f"{row.entity_type}::{row.text}"
            if key not in seen:
                seen.add(key)
                total_unique += 1
            etype = row.entity_type
            if etype not in by_type:
                by_type[etype] = []
            if len(by_type[etype]) < 10:
                by_type[etype].append({"text": row.text, "count": row.cnt})

        data.entities = EntityData(by_type=by_type, total_unique=total_unique)

    async def _fill_trend(self, data: ReportData) -> None:
        trunc = data.period  # day | week | month

        vol_result = await self.db.execute(
            text(f"""
                SELECT
                    DATE_TRUNC('{trunc}', published_at) AS period,
                    platform,
                    COUNT(*) AS cnt
                FROM posts
                WHERE keyword_id = :kid AND published_at IS NOT NULL
                GROUP BY period, platform
                ORDER BY period ASC
            """),
            {"kid": data.keyword_id},
        )
        volume: list[dict] = []
        platform_breakdown: dict[str, int] = {}
        for row in vol_result.all():
            period_str = row.period.isoformat() if row.period else ""
            volume.append({"period": period_str, "platform": row.platform, "count": row.cnt})
            platform_breakdown[row.platform] = platform_breakdown.get(row.platform, 0) + row.cnt

        # Sentiment over time
        sent_result = await self.db.execute(
            text(f"""
                SELECT
                    DATE_TRUNC('{trunc}', p.published_at) AS period,
                    s.label,
                    COUNT(*) AS cnt
                FROM sentiments s
                JOIN posts p ON p.id = s.post_id
                WHERE p.keyword_id = :kid AND p.published_at IS NOT NULL
                GROUP BY period, s.label
                ORDER BY period ASC
            """),
            {"kid": data.keyword_id},
        )
        sentiment_trend: list[dict] = [
            {
                "period": (row.period.isoformat() if row.period else ""),
                "label": row.label,
                "count": row.cnt,
            }
            for row in sent_result.all()
        ]

        # Direction: bandingkan total post paruh pertama vs kedua
        direction = "stabil"
        if len(volume) >= 4:
            mid = len(volume) // 2
            first_half = sum(v["count"] for v in volume[:mid])
            second_half = sum(v["count"] for v in volume[mid:])
            if second_half > first_half * 1.1:
                direction = "naik"
            elif second_half < first_half * 0.9:
                direction = "turun"

        data.trend = TrendData(
            volume=volume,
            sentiment=sentiment_trend,
            platform_breakdown=platform_breakdown,
            direction=direction,
            total_posts=sum(v["count"] for v in volume),
        )

    async def _fill_top_posts(self, data: ReportData, n: int) -> None:
        result = await self.db.execute(
            text("""
                SELECT p.content, p.platform, p.url, p.published_at,
                       p.author, s.label, s.score
                FROM posts p
                LEFT JOIN sentiments s ON s.post_id = p.id
                WHERE p.keyword_id = :kid
                  AND p.content IS NOT NULL
                ORDER BY p.published_at DESC NULLS LAST
                LIMIT :n
            """),
            {"kid": data.keyword_id, "n": n},
        )
        data.top_posts = [
            {
                "platform": row.platform,
                "author": row.author,
                "content": (row.content or "")[:300],
                "url": row.url,
                "published_at": row.published_at.isoformat() if row.published_at else None,
                "sentiment_label": row.label,
                "sentiment_score": float(row.score) if row.score is not None else None,
            }
            for row in result.all()
        ]

