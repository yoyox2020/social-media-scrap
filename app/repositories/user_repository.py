"""All database queries for User model live here."""
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User


class UserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        result = await self.db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        result = await self.db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_by_username(self, username: str) -> User | None:
        result = await self.db.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    async def create(self, user: User) -> User:
        self.db.add(user)
        await self.db.flush()
        await self.db.refresh(user)
        return user

    async def update(self, user: User) -> User:
        await self.db.flush()
        await self.db.refresh(user)
        return user

    async def delete(self, user_id: uuid.UUID) -> None:
        """HARD delete -- HATI-HATI, Project.user_id & ApiKey.user_id pakai
        ondelete='CASCADE', jadi ini ikut menghapus seluruh Project (+keyword/
        post/comment turunannya) milik user itu. Dipakai internal/skrip saja;
        endpoint admin (app/api/v1/users.py) SENGAJA pakai soft-delete
        (is_active=False, lewat update()) supaya data tidak ikut hilang."""
        user = await self.get_by_id(user_id)
        if user:
            await self.db.delete(user)
            await self.db.flush()

    async def search(
        self, query: str | None, limit: int, offset: int
    ) -> tuple[list[User], int]:
        """Cari user berdasarkan email/username (ILIKE substring), atau semua
        user kalau query kosong. Return (baris, total_count)."""
        filters = []
        if query:
            pattern = f"%{query}%"
            filters.append(or_(User.email.ilike(pattern), User.username.ilike(pattern)))

        count_stmt = select(func.count(User.id))
        list_stmt = select(User).order_by(User.created_at.desc()).offset(offset).limit(limit)
        for f in filters:
            count_stmt = count_stmt.where(f)
            list_stmt = list_stmt.where(f)

        total = (await self.db.execute(count_stmt)).scalar() or 0
        rows = (await self.db.execute(list_stmt)).scalars().all()
        return list(rows), total
