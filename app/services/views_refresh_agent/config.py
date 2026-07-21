"""
Konfigurasi Views Refresh Agent -- SEMUA di Redis, pola SAMA dgn
app/services/youtube_metadata/config.py, TAPI namespace TERPISAH
(`views_refresh_agent:*`) krn agent ini pakai API KEY YOUTUBE DATA API
TERPISAH dari Metadata Agent (project Google Cloud sendiri, kuota
10.000/hari SENDIRI -- 2026-07-18, permintaan user demi kejar tenggat).

BEDA dari youtube_metadata/config.py: TIDAK ada model/api_key OpenRouter
sama sekali -- agent ini MURNI panggil YouTube Data API (views/likes/
comments/subscriber), TIDAK ada viral_context/LLM apa pun.
"""
from __future__ import annotations

from app.infrastructure.redis.connection import get_redis

_KEY_INTERVAL_MINUTES = "views_refresh_agent:interval_minutes"
_KEY_API_KEY = "views_refresh_agent:api_key"
_KEY_BATCH_SIZE = "views_refresh_agent:batch_size"
_KEY_REFRESH_AGE_HOURS = "views_refresh_agent:refresh_age_hours"
_KEY_RUNNING_LOCK = "views_refresh_agent:running_lock"
_KEY_LAST_RUN_AT = "views_refresh_agent:last_run_at"

DEFAULT_INTERVAL_MINUTES = 30
ALLOWED_INTERVAL_MINUTES = {15, 30, 60, 240}
DEFAULT_BATCH_SIZE = 50
ALLOWED_BATCH_SIZE = {10, 20, 50, 100}
DEFAULT_REFRESH_AGE_HOURS = 6
ALLOWED_REFRESH_AGE_HOURS = {1, 3, 6, 12, 24}


async def get_interval_minutes() -> int:
    redis = await get_redis()
    raw = await redis.get(_KEY_INTERVAL_MINUTES)
    if raw is not None:
        return int(raw)
    await redis.set(_KEY_INTERVAL_MINUTES, str(DEFAULT_INTERVAL_MINUTES))
    return DEFAULT_INTERVAL_MINUTES


async def set_interval_minutes(minutes: int) -> int:
    if minutes not in ALLOWED_INTERVAL_MINUTES:
        raise ValueError(f"interval_minutes harus salah satu dari {sorted(ALLOWED_INTERVAL_MINUTES)}")
    redis = await get_redis()
    await redis.set(_KEY_INTERVAL_MINUTES, str(minutes))
    return minutes


async def get_api_key() -> str | None:
    redis = await get_redis()
    raw = await redis.get(_KEY_API_KEY)
    if raw is None:
        return None
    return raw if isinstance(raw, str) else raw.decode()


async def set_api_key(api_key: str) -> None:
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("api_key tidak boleh kosong")
    redis = await get_redis()
    await redis.set(_KEY_API_KEY, api_key)


def mask_api_key(api_key: str | None) -> str | None:
    if not api_key:
        return None
    if len(api_key) <= 4:
        return "*" * len(api_key)
    return "*" * (len(api_key) - 4) + api_key[-4:]


async def get_batch_size() -> int:
    redis = await get_redis()
    raw = await redis.get(_KEY_BATCH_SIZE)
    if raw is not None:
        return int(raw)
    await redis.set(_KEY_BATCH_SIZE, str(DEFAULT_BATCH_SIZE))
    return DEFAULT_BATCH_SIZE


async def set_batch_size(size: int) -> int:
    if size not in ALLOWED_BATCH_SIZE:
        raise ValueError(f"batch_size harus salah satu dari {sorted(ALLOWED_BATCH_SIZE)}")
    redis = await get_redis()
    await redis.set(_KEY_BATCH_SIZE, str(size))
    return size


async def get_refresh_age_hours() -> int:
    redis = await get_redis()
    raw = await redis.get(_KEY_REFRESH_AGE_HOURS)
    if raw is not None:
        return int(raw)
    await redis.set(_KEY_REFRESH_AGE_HOURS, str(DEFAULT_REFRESH_AGE_HOURS))
    return DEFAULT_REFRESH_AGE_HOURS


async def set_refresh_age_hours(hours: int) -> int:
    if hours not in ALLOWED_REFRESH_AGE_HOURS:
        raise ValueError(f"refresh_age_hours harus salah satu dari {sorted(ALLOWED_REFRESH_AGE_HOURS)}")
    redis = await get_redis()
    await redis.set(_KEY_REFRESH_AGE_HOURS, str(hours))
    return hours


async def get_last_run_at() -> str | None:
    redis = await get_redis()
    val = await redis.get(_KEY_LAST_RUN_AT)
    if val is None:
        return None
    return val if isinstance(val, str) else val.decode()


async def set_last_run_at(iso_timestamp: str) -> None:
    redis = await get_redis()
    await redis.set(_KEY_LAST_RUN_AT, iso_timestamp)


async def acquire_running_lock() -> bool:
    redis = await get_redis()
    return bool(await redis.set(_KEY_RUNNING_LOCK, "1", nx=True, ex=3600))


async def release_running_lock() -> None:
    redis = await get_redis()
    await redis.delete(_KEY_RUNNING_LOCK)


async def is_running() -> bool:
    redis = await get_redis()
    return bool(await redis.get(_KEY_RUNNING_LOCK))
