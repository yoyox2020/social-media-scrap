import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.sentiments.models import Sentiment


class SentimentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_post_id(self, post_id: uuid.UUID) -> Sentiment | None:
        result = await self.db.execute(
            select(Sentiment).where(Sentiment.post_id == post_id)
        )
        return result.scalar_one_or_none()

    async def create(self, sentiment: Sentiment) -> Sentiment:
        self.db.add(sentiment)
        await self.db.flush()
        await self.db.refresh(sentiment)
        return sentiment

    async def bulk_create(self, sentiments: list[Sentiment]) -> int:
        if not sentiments:
            return 0
        rows = [
            {
                "id": s.id,
                "post_id": s.post_id,
                "comment_id": s.comment_id,
                "label": s.label,
                "score": s.score,
                "model_version": s.model_version,
            }
            for s in sentiments
        ]
        stmt = insert(Sentiment).values(rows).on_conflict_do_nothing(
            index_elements=["id"]
        )
        result = await self.db.execute(stmt)
        await self.db.flush()
        return result.rowcount

    async def delete_by_post_id(self, post_id: uuid.UUID) -> None:
        await self.db.execute(
            delete(Sentiment).where(Sentiment.post_id == post_id)
        )
        await self.db.flush()

    async def list_by_keyword(self, keyword_id: uuid.UUID) -> list[Sentiment]:
        from app.domain.posts.models import Post
        result = await self.db.execute(
            select(Sentiment)
            .join(Post, Post.id == Sentiment.post_id)
            .where(Post.keyword_id == keyword_id)
        )
        return list(result.scalars().all())

    async def count_by_label_for_keyword(self, keyword_id: uuid.UUID) -> dict[str, int]:
        """Return distribution: {'positive': N, 'negative': N, 'neutral': N}"""
        from app.domain.posts.models import Post
        result = await self.db.execute(
            select(Sentiment.label, func.count().label("cnt"))
            .join(Post, Post.id == Sentiment.post_id)
            .where(Post.keyword_id == keyword_id)
            .group_by(Sentiment.label)
        )
        rows = result.all()
        return {row.label: row.cnt for row in rows}
