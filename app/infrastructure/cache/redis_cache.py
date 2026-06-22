import json
from typing import Any

from app.infrastructure.redis.connection import get_redis


async def cache_get(key: str) -> Any | None:
    redis = await get_redis()
    raw = await redis.get(key)
    if raw is None:
        return None
    return json.loads(raw)


async def cache_set(key: str, value: Any, ex: int = 300) -> None:
    redis = await get_redis()
    await redis.set(key, json.dumps(value, default=str), ex=ex)


async def cache_delete(key: str) -> None:
    redis = await get_redis()
    await redis.delete(key)


async def cache_delete_pattern(pattern: str) -> None:
    """Hapus semua key yang cocok dengan pattern (gunakan dengan hati-hati di prod)."""
    redis = await get_redis()
    keys = await redis.keys(pattern)
    if keys:
        await redis.delete(*keys)
