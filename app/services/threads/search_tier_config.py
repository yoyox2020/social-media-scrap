"""
Konfigurasi alur tier pencarian Threads (Fase 1+2, 2026-07-20) --
lihat docs/threads-redesign-schema.md. Pola SAMA dgn config module
agent lain di project ini: Redis, get/set async, default kalau kosong,
efek LANGSUNG aktif tanpa restart.
"""
from __future__ import annotations

from app.infrastructure.redis.connection import get_redis

_KEY_CACHE_FRESHNESS_HOURS = "threads_search:cache_freshness_hours"
_KEY_MAX_CONCURRENT_JOBS = "threads_search:max_concurrent_jobs"
_KEY_QUEUE_MAX_ATTEMPTS = "threads_search:queue_max_attempts"

DEFAULT_CACHE_FRESHNESS_HOURS = 24
DEFAULT_MAX_CONCURRENT_JOBS = 2
DEFAULT_QUEUE_MAX_ATTEMPTS = 48  # ~8 jam kalau drain tiap 10 menit


async def get_cache_freshness_hours() -> int:
    redis = await get_redis()
    raw = await redis.get(_KEY_CACHE_FRESHNESS_HOURS)
    return int(raw) if raw is not None else DEFAULT_CACHE_FRESHNESS_HOURS


async def set_cache_freshness_hours(hours: int) -> int:
    if hours < 1:
        raise ValueError("cache_freshness_hours harus >= 1")
    redis = await get_redis()
    await redis.set(_KEY_CACHE_FRESHNESS_HOURS, str(hours))
    return hours


async def get_max_concurrent_jobs() -> int:
    redis = await get_redis()
    raw = await redis.get(_KEY_MAX_CONCURRENT_JOBS)
    return int(raw) if raw is not None else DEFAULT_MAX_CONCURRENT_JOBS


async def set_max_concurrent_jobs(value: int) -> int:
    if value < 1:
        raise ValueError("max_concurrent_jobs harus >= 1")
    redis = await get_redis()
    await redis.set(_KEY_MAX_CONCURRENT_JOBS, str(value))
    return value


async def get_queue_max_attempts() -> int:
    redis = await get_redis()
    raw = await redis.get(_KEY_QUEUE_MAX_ATTEMPTS)
    return int(raw) if raw is not None else DEFAULT_QUEUE_MAX_ATTEMPTS


async def set_queue_max_attempts(value: int) -> int:
    if value < 1:
        raise ValueError("queue_max_attempts harus >= 1")
    redis = await get_redis()
    await redis.set(_KEY_QUEUE_MAX_ATTEMPTS, str(value))
    return value
