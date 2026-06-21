import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.models import Entity


class EntityRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, entity_id: uuid.UUID) -> Entity | None:
        result = await self.db.execute(select(Entity).where(Entity.id == entity_id))
        return result.scalar_one_or_none()

    async def list_by_post_id(self, post_id: uuid.UUID) -> list[Entity]:
        result = await self.db.execute(
            select(Entity)
            .where(Entity.post_id == post_id)
            .order_by(Entity.start_char.asc())
        )
        return list(result.scalars().all())

    async def count_by_post_id(self, post_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count()).where(Entity.post_id == post_id)
        )
        return result.scalar_one()

    async def bulk_create(self, entities: list[Entity]) -> int:
        if not entities:
            return 0
        for entity in entities:
            self.db.add(entity)
        await self.db.flush()
        return len(entities)

    async def delete_by_post_id(self, post_id: uuid.UUID) -> None:
        await self.db.execute(delete(Entity).where(Entity.post_id == post_id))
        await self.db.flush()

    async def list_by_keyword(
        self,
        keyword_id: uuid.UUID,
        entity_type: str | None = None,
        limit: int = 100,
    ) -> list[Entity]:
        from app.domain.posts.models import Post

        stmt = (
            select(Entity)
            .join(Post, Post.id == Entity.post_id)
            .where(Post.keyword_id == keyword_id)
        )
        if entity_type:
            stmt = stmt.where(Entity.entity_type == entity_type)
        stmt = stmt.limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def top_entities_by_keyword(
        self,
        keyword_id: uuid.UUID,
        entity_type: str | None = None,
        top_n: int = 20,
    ) -> list[dict]:
        """Return top N entity texts berdasarkan frekuensi kemunculan."""
        from app.domain.posts.models import Post

        stmt = (
            select(Entity.text, Entity.entity_type, func.count().label("count"))
            .join(Post, Post.id == Entity.post_id)
            .where(Post.keyword_id == keyword_id)
        )
        if entity_type:
            stmt = stmt.where(Entity.entity_type == entity_type)
        stmt = stmt.group_by(Entity.text, Entity.entity_type).order_by(
            func.count().desc()
        ).limit(top_n)

        result = await self.db.execute(stmt)
        return [
            {"text": row.text, "entity_type": row.entity_type, "count": row.count}
            for row in result.all()
        ]
