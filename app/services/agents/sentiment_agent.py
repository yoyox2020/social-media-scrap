"""
SentimentAgent — menganalisis distribusi dan tren sentimen untuk satu keyword.
Mengambil data dari tabel sentiments yang sudah diisi oleh AI Service (Phase 4).
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.posts.models import Post
from app.domain.sentiments.models import Sentiment
from app.services.agents.base import BaseAgent
from app.services.agents.schemas import AgentContext, AgentResult


class SentimentAgent(BaseAgent):
    name = "sentiment"
    description = "Menganalisis distribusi dan tren sentimen dari hasil IndoBERT"

    async def run(self, context: AgentContext) -> AgentResult:
        try:
            distribution = await self._get_distribution(context)
            examples = await self._get_examples(context)

            total = sum(distribution.values())
            if total == 0:
                return self._ok(
                    data={"total": 0, "distribution": {}, "percentages": {}},
                    summary="Belum ada analisis sentimen untuk keyword ini.",
                )

            percentages = {
                label: round(count / total * 100, 1)
                for label, count in distribution.items()
            }

            dominant = max(distribution, key=distribution.get) if distribution else "neutral"

            summary = (
                f"Dari {total} post yang dianalisis, sentimen {dominant} mendominasi "
                f"({percentages.get(dominant, 0)}%). "
                f"Positif: {percentages.get('positive', 0)}%, "
                f"Negatif: {percentages.get('negative', 0)}%, "
                f"Netral: {percentages.get('neutral', 0)}%."
            )

            return self._ok(
                data={
                    "total_analyzed": total,
                    "distribution": distribution,
                    "percentages": percentages,
                    "dominant_sentiment": dominant,
                    "examples": examples,
                },
                summary=summary,
                sources=[{"post_id": e["post_id"]} for e in examples],
            )
        except Exception as exc:
            return self._err(str(exc))

    async def _get_distribution(self, context: AgentContext) -> dict[str, int]:
        stmt = (
            select(Sentiment.label, func.count().label("cnt"))
            .join(Post, Post.id == Sentiment.post_id)
            .where(Post.keyword_id == context.keyword_id)
        )
        if context.platform:
            stmt = stmt.where(Post.platform == context.platform)
        if context.date_from:
            stmt = stmt.where(Post.published_at >= context.date_from)
        if context.date_to:
            stmt = stmt.where(Post.published_at <= context.date_to)
        stmt = stmt.group_by(Sentiment.label)

        result = await self.db.execute(stmt)
        rows = result.all()
        dist: dict[str, int] = {"positive": 0, "negative": 0, "neutral": 0}
        for row in rows:
            dist[row.label] = row.cnt
        return dist

    async def _get_examples(self, context: AgentContext) -> list[dict]:
        """Ambil 1 contoh post per label sentimen."""
        examples = []
        for label in ["positive", "negative", "neutral"]:
            stmt = (
                select(Post.id, Post.cleaned_content, Post.author, Post.platform, Sentiment.score)
                .join(Sentiment, Sentiment.post_id == Post.id)
                .where(
                    Post.keyword_id == context.keyword_id,
                    Sentiment.label == label,
                )
                .order_by(Sentiment.score.desc())
                .limit(1)
            )
            result = await self.db.execute(stmt)
            row = result.first()
            if row:
                examples.append({
                    "post_id": str(row.id),
                    "label": label,
                    "score": row.score,
                    "excerpt": (row.cleaned_content or "")[:150],
                    "author": row.author,
                    "platform": row.platform,
                })
        return examples
