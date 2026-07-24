"""Query database utk hasil Sentiment Agent (2026-07-24) -- baca dari
`lexicon_analyses` (JOIN comments+posts), TIDAK ada logika bisnis di
sini, murni akses data (dipanggil dari app/api/v1/sentiment.py).

`sentiment` efektif tiap baris = COALESCE(final_label, label) -- pola
SAMA PERSIS dgn yg sudah dipakai smart-search sebelumnya
([[project_smart_search_sentiment_coalesce]]): kalau LLM tiebreaker
SUDAH memutuskan (final_label terisi), itu yg dipakai (lebih akurat,
sudah lewat 2-3 opini); kalau belum direview LLM sama sekali,
fallback ke label lexicon asli (masih ada opininya, instan)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.comments.models import Comment
from app.domain.posts.models import Post
from app.domain.youtube_analysis.models import LexiconAnalysis

_EFFECTIVE_LABEL = func.coalesce(LexiconAnalysis.final_label, LexiconAnalysis.label)


class SentimentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _base_query(self):
        return (
            select(LexiconAnalysis, Comment, Post)
            .join(Comment, Comment.id == LexiconAnalysis.comment_id)
            .join(Post, Post.id == Comment.post_id)
        )

    def _apply_filters(self, stmt, platform: str | None, sentiment: str | None,
                        date_from: datetime | None, date_to: datetime | None):
        if platform:
            stmt = stmt.where(Post.platform == platform)
        if sentiment:
            stmt = stmt.where(_EFFECTIVE_LABEL == sentiment)
        if date_from:
            stmt = stmt.where(LexiconAnalysis.created_at >= date_from)
        if date_to:
            stmt = stmt.where(LexiconAnalysis.created_at <= date_to)
        return stmt

    async def get_by_comment_id(self, comment_id: uuid.UUID) -> tuple[LexiconAnalysis, Comment, Post] | None:
        row = (await self.db.execute(
            self._base_query().where(LexiconAnalysis.comment_id == comment_id)
        )).one_or_none()
        return tuple(row) if row else None  # type: ignore[return-value]

    async def list_results(
        self, platform: str | None = None, sentiment: str | None = None,
        date_from: datetime | None = None, date_to: datetime | None = None,
        page: int = 1, limit: int = 20,
    ) -> tuple[list[tuple[LexiconAnalysis, Comment, Post]], int]:
        base = self._apply_filters(self._base_query(), platform, sentiment, date_from, date_to)

        total = await self.db.scalar(select(func.count()).select_from(base.subquery()))

        offset = (max(page, 1) - 1) * limit
        stmt = base.order_by(LexiconAnalysis.created_at.desc()).offset(offset).limit(limit)
        rows = (await self.db.execute(stmt)).all()
        return [tuple(r) for r in rows], total or 0  # type: ignore[misc]

    async def get_statistics(
        self, platform: str | None = None,
        date_from: datetime | None = None, date_to: datetime | None = None,
    ) -> dict:
        stmt = self._apply_filters(
            select(_EFFECTIVE_LABEL.label("sentiment"), func.count().label("count"))
            .select_from(LexiconAnalysis)
            .join(Comment, Comment.id == LexiconAnalysis.comment_id)
            .join(Post, Post.id == Comment.post_id),
            platform, None, date_from, date_to,
        ).group_by(_EFFECTIVE_LABEL)

        rows = (await self.db.execute(stmt)).all()
        counts = {row.sentiment: row.count for row in rows}
        return {
            "positif": counts.get("positif", 0),
            "negatif": counts.get("negatif", 0),
            "netral": counts.get("netral", 0),
            "total": sum(counts.values()),
        }

    async def get_unanalyzed_count(self) -> int:
        """Komentar yg SUDAH py lexicon TAPI belum direview LLM sama
        sekali -- backlog Sentiment Agent (LLM tiebreaker)."""
        return await self.db.scalar(
            select(func.count()).select_from(LexiconAnalysis).where(LexiconAnalysis.llm_checked_at.is_(None))
        ) or 0
