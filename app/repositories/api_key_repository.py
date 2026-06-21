"""All database queries for ApiKey model live here."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import ApiKey


class ApiKeyRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, key_id: uuid.UUID) -> ApiKey | None:
        result = await self.db.execute(select(ApiKey).where(ApiKey.id == key_id))
        return result.scalar_one_or_none()

    async def get_by_hash(self, key_hash: str) -> ApiKey | None:
        result = await self.db.execute(
            select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.is_active == True)  # noqa: E712
        )
        return result.scalar_one_or_none()

    async def list_by_user(self, user_id: uuid.UUID) -> list[ApiKey]:
        result = await self.db.execute(
            select(ApiKey).where(ApiKey.user_id == user_id).order_by(ApiKey.created_at.desc())
        )
        return list(result.scalars().all())

    async def create(self, api_key: ApiKey) -> ApiKey:
        self.db.add(api_key)
        await self.db.flush()
        await self.db.refresh(api_key)
        return api_key

    async def deactivate(self, key_id: uuid.UUID, user_id: uuid.UUID) -> None:
        await self.db.execute(
            update(ApiKey)
            .where(ApiKey.id == key_id, ApiKey.user_id == user_id)
            .values(is_active=False)
        )
        await self.db.flush()

    async def update_last_used(self, key_id: uuid.UUID) -> None:
        await self.db.execute(
            update(ApiKey).where(ApiKey.id == key_id).values(last_used_at=datetime.now(timezone.utc))
        )
        await self.db.flush()
