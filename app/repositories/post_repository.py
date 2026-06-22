"""All database queries for Post model live here."""
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.posts.models import Post


class PostRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, post_id: uuid.UUID) -> Post | None:
        result = await self.db.execute(select(Post).where(Post.id == post_id))
        return result.scalar_one_or_none()

    async def get_by_external_id(self, external_id: str, platform: str) -> Post | None:
        result = await self.db.execute(
            select(Post).where(Post.external_id == external_id, Post.platform == platform)
        )
        return result.scalar_one_or_none()

    async def get_existing_external_ids(self, external_ids: list[str], platform: str) -> set[str]:
        """Ambil external_id yang sudah ada — untuk deduplication bulk."""
        if not external_ids:
            return set()
        result = await self.db.execute(
            select(Post.external_id).where(
                Post.external_id.in_(external_ids),
                Post.platform == platform,
            )
        )
        return set(result.scalars().all())

    async def list_by_keyword(self, keyword_id: uuid.UUID, offset: int = 0, limit: int = 20) -> list[Post]:
        result = await self.db.execute(
            select(Post)
            .where(Post.keyword_id == keyword_id)
            .order_by(Post.published_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_by_keyword(self, keyword_id: uuid.UUID) -> int:
        from sqlalchemy import func
        result = await self.db.execute(
            select(func.count()).where(Post.keyword_id == keyword_id)
        )
        return result.scalar_one()

    async def create(self, post: Post) -> Post:
        self.db.add(post)
        await self.db.flush()
        await self.db.refresh(post)
        return post

    async def bulk_create(self, posts: list[Post]) -> int:
        """Insert banyak post sekaligus, skip duplikat. Return jumlah yang berhasil diinsert."""
        if not posts:
            return 0
        rows = [
            {
                "id": p.id,
                "keyword_id": p.keyword_id,
                "external_id": p.external_id,
                "platform": p.platform,
                "content": p.content,
                "author": p.author,
                "url": p.url,
                "metadata": p.metadata_,
                "raw_data": p.raw_data,
                "published_at": p.published_at,
                "collected_at": p.collected_at,
            }
            for p in posts
        ]
        stmt = insert(Post).values(rows).on_conflict_do_nothing(
            index_elements=["external_id", "platform"]
        )
        # on_conflict_do_nothing requires unique constraint on (external_id, platform)
        # Migration 002 akan menambah constraint ini
        result = await self.db.execute(stmt)
        await self.db.flush()
        return result.rowcount

    async def update_embedding(self, post_id: uuid.UUID, embedding: list[float]) -> None:
        """Simpan embedding vector ke posts.embedding (pgvector)."""
        from sqlalchemy import update
        await self.db.execute(
            update(Post).where(Post.id == post_id).values(embedding=embedding)
        )
        await self.db.flush()

    async def list_processed_by_keyword(
        self,
        keyword_id: uuid.UUID,
        force: bool = False,
    ) -> list[Post]:
        """
        Return post yang sudah diproses (is_processed=True) dan siap untuk AI inference.
        force=True mengembalikan semua post termasuk yang sudah ada embedding/sentimentnya.
        """
        stmt = (
            select(Post)
            .where(Post.keyword_id == keyword_id, Post.is_processed == True)  # noqa: E712
            .order_by(Post.published_at.asc())
        )
        if not force:
            # Hanya post yang belum ada embedding-nya
            stmt = stmt.where(Post.embedding.is_(None))
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def search_by_embedding(
        self,
        embedding: list[float],
        keyword_id: uuid.UUID | None = None,
        limit: int = 10,
        ef_search: int = 40,
    ) -> list[Post]:
        """
        Semantic search menggunakan pgvector cosine distance (<=>).
        Menggunakan HNSW index (migration 004) untuk ANN search yang cepat.

        ef_search: trade-off kecepatan vs akurasi (default 40, max 200)
        """
        from sqlalchemy import text

        # Set ef_search per session untuk kontrol akurasi HNSW
        await self.db.execute(text(f"SET LOCAL hnsw.ef_search = {ef_search}"))

        stmt = (
            select(Post)
            .where(Post.embedding.is_not(None))
            .order_by(Post.embedding.op("<=>")(embedding))
            .limit(limit)
        )
        if keyword_id:
            stmt = stmt.where(Post.keyword_id == keyword_id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
