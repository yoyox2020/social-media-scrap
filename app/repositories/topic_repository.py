import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.topics.models import Topic


class TopicRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, topic_id: uuid.UUID) -> Topic | None:
        result = await self.db.execute(select(Topic).where(Topic.id == topic_id))
        return result.scalar_one_or_none()

    async def list_by_project(self, project_id: uuid.UUID) -> list[Topic]:
        result = await self.db.execute(
            select(Topic)
            .where(Topic.project_id == project_id)
            .order_by(Topic.post_count.desc())
        )
        return list(result.scalars().all())

    async def create(self, topic: Topic) -> Topic:
        self.db.add(topic)
        await self.db.flush()
        await self.db.refresh(topic)
        return topic

    async def create_many(self, topics: list[Topic]) -> list[Topic]:
        for t in topics:
            self.db.add(t)
        await self.db.flush()
        return topics

    async def delete_by_project(self, project_id: uuid.UUID) -> None:
        from sqlalchemy import delete
        await self.db.execute(delete(Topic).where(Topic.project_id == project_id))
        await self.db.flush()
