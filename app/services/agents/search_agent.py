"""
SearchAgent — mencari post yang relevan menggunakan dua metode:
1. Semantic search   via pgvector (embedding similarity)
2. Full-text search  via PostgreSQL tsvector
"""
from __future__ import annotations

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.posts.models import Post
from app.services.agents.base import BaseAgent
from app.services.agents.schemas import AgentContext, AgentResult


class SearchAgent(BaseAgent):
    name = "search"
    description = "Mencari post yang relevan dengan pertanyaan menggunakan embedding + full-text"

    def __init__(self, db: AsyncSession, limit: int = 5):
        super().__init__(db)
        self.limit = limit

    async def run(self, context: AgentContext) -> AgentResult:
        try:
            results = []

            # ── 1. Semantic search via pgvector ──────────────────────────────
            semantic = await self._semantic_search(context)
            results.extend(semantic)

            # ── 2. Full-text search via PostgreSQL ────────────────────────────
            fulltext = await self._fulltext_search(context)
            # merge: tambahkan yang belum ada
            existing_ids = {r["post_id"] for r in results}
            for r in fulltext:
                if r["post_id"] not in existing_ids:
                    results.append(r)
                    existing_ids.add(r["post_id"])

            results = results[: self.limit * 2]

            return self._ok(
                data={"posts": results, "total_found": len(results)},
                summary=(
                    f"Ditemukan {len(results)} post relevan untuk: '{context.question[:80]}'"
                ),
                sources=[{"post_id": r["post_id"], "excerpt": r["excerpt"]} for r in results],
            )
        except Exception as exc:
            return self._err(str(exc))

    async def _semantic_search(self, context: AgentContext) -> list[dict]:
        """Cari post menggunakan embedding similarity (pgvector)."""
        try:
            from app.services.ai.embedding_generator import EmbeddingGenerator

            query_embedding = EmbeddingGenerator.get_instance().generate(context.question)
        except Exception:
            return []

        stmt = (
            select(
                Post.id,
                Post.content,
                Post.cleaned_content,
                Post.author,
                Post.platform,
                Post.published_at,
                Post.embedding.op("<=>")(query_embedding).label("distance"),
            )
            .where(
                Post.keyword_id == context.keyword_id,
                Post.embedding.is_not(None),
            )
            .order_by(text("distance ASC"))
            .limit(self.limit)
        )
        result = await self.db.execute(stmt)
        rows = result.all()
        return [
            {
                "post_id": str(row.id),
                "excerpt": (row.cleaned_content or row.content or "")[:200],
                "author": row.author,
                "platform": row.platform,
                "published_at": row.published_at.isoformat() if row.published_at else None,
                "similarity_score": round(1 - float(row.distance), 4),
                "search_type": "semantic",
            }
            for row in rows
        ]

    async def _fulltext_search(self, context: AgentContext) -> list[dict]:
        """Full-text search menggunakan PostgreSQL to_tsvector."""
        query_words = " | ".join(context.question.split()[:5])  # ambil 5 kata pertama

        stmt = (
            select(Post)
            .where(
                Post.keyword_id == context.keyword_id,
                Post.cleaned_content.is_not(None),
                func.to_tsvector("simple", Post.cleaned_content).op("@@")(
                    func.to_tsquery("simple", query_words)
                ),
            )
            .order_by(Post.published_at.desc())
            .limit(self.limit)
        )
        try:
            result = await self.db.execute(stmt)
            rows = result.scalars().all()
            return [
                {
                    "post_id": str(p.id),
                    "excerpt": (p.cleaned_content or p.content or "")[:200],
                    "author": p.author,
                    "platform": p.platform,
                    "published_at": p.published_at.isoformat() if p.published_at else None,
                    "similarity_score": None,
                    "search_type": "fulltext",
                }
                for p in rows
            ]
        except Exception:
            return []
