"""
EntityAgent — menganalisis entitas yang paling sering muncul dalam kumpulan post.
Mengambil data dari tabel entities yang sudah diisi oleh AI Service (Phase 4).
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.models import Entity
from app.domain.posts.models import Post
from app.services.agents.base import BaseAgent
from app.services.agents.schemas import AgentContext, AgentResult


class EntityAgent(BaseAgent):
    name = "entity"
    description = "Menganalisis named entities (orang, organisasi, lokasi, produk) paling sering muncul"

    def __init__(self, db: AsyncSession, top_n: int = 10):
        super().__init__(db)
        self.top_n = top_n

    async def run(self, context: AgentContext) -> AgentResult:
        try:
            top_by_type = await self._get_top_entities_by_type(context)
            total_entities = sum(len(v) for v in top_by_type.values())

            if total_entities == 0:
                return self._ok(
                    data={"by_type": {}, "total_entities": 0},
                    summary="Belum ada entitas yang diekstrak untuk keyword ini.",
                )

            # Buat ringkasan dari top entities
            highlights = []
            for etype, entities in top_by_type.items():
                if entities:
                    top_names = ", ".join(e["text"] for e in entities[:3])
                    highlights.append(f"{etype}: {top_names}")

            summary = f"Top entities terdeteksi — " + "; ".join(highlights[:4]) + "."

            return self._ok(
                data={
                    "by_type": top_by_type,
                    "total_unique_entities": total_entities,
                },
                summary=summary,
            )
        except Exception as exc:
            return self._err(str(exc))

    async def _get_top_entities_by_type(self, context: AgentContext) -> dict[str, list[dict]]:
        entity_types = ["PERSON", "ORGANIZATION", "LOCATION", "PRODUCT", "EVENT"]
        result: dict[str, list[dict]] = {}

        for etype in entity_types:
            stmt = (
                select(Entity.text, func.count().label("count"))
                .join(Post, Post.id == Entity.post_id)
                .where(
                    Post.keyword_id == context.keyword_id,
                    Entity.entity_type == etype,
                )
            )
            if context.platform:
                stmt = stmt.where(Post.platform == context.platform)
            if context.date_from:
                stmt = stmt.where(Post.published_at >= context.date_from)
            if context.date_to:
                stmt = stmt.where(Post.published_at <= context.date_to)

            stmt = (
                stmt.group_by(Entity.text)
                .order_by(func.count().desc())
                .limit(self.top_n)
            )

            rows = (await self.db.execute(stmt)).all()
            if rows:
                result[etype] = [{"text": row.text, "count": row.count} for row in rows]

        return result
