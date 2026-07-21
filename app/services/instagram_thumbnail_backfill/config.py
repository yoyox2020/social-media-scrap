"""
Konfigurasi Instagram Thumbnail Backfill Agent -- worker BARU khusus isi
ulang foto post Instagram LAMA yang genuinely tidak punya photo_url (di-
scrape lewat provider Apify lama sebelum fix 2026-07-20, lihat
[[reference_instagram_post_scraper_actor]]). SEMUA di Redis (pola SAMA
dgn app/services/youtube_discovery/config.py) -- efeknya LANGSUNG aktif
di run berikutnya, tanpa restart.
"""
from __future__ import annotations

from app.infrastructure.redis.connection import get_redis

_KEY_DAILY_BUDGET = "ig_thumbnail_backfill:daily_budget"
_KEY_ENABLED = "ig_thumbnail_backfill:enabled"
_KEY_LAST_RUN_AT = "ig_thumbnail_backfill:last_run_at"

DEFAULT_DAILY_BUDGET = 5  # jumlah akun Instagram di-backfill per run -- kendali biaya Apify/EnsembleData


async def get_daily_budget() -> int:
    redis = await get_redis()
    raw = await redis.get(_KEY_DAILY_BUDGET)
    if raw is not None:
        return int(raw)
    await redis.set(_KEY_DAILY_BUDGET, str(DEFAULT_DAILY_BUDGET))
    return DEFAULT_DAILY_BUDGET


async def set_daily_budget(budget: int) -> int:
    if budget <= 0:
        raise ValueError("daily_budget harus > 0")
    redis = await get_redis()
    await redis.set(_KEY_DAILY_BUDGET, str(budget))
    return budget


async def get_enabled() -> bool:
    redis = await get_redis()
    raw = await redis.get(_KEY_ENABLED)
    if raw is None:
        return True
    val = raw if isinstance(raw, str) else raw.decode()
    return val == "1"


async def set_enabled(enabled: bool) -> bool:
    redis = await get_redis()
    await redis.set(_KEY_ENABLED, "1" if enabled else "0")
    return enabled


async def get_last_run_at() -> str | None:
    redis = await get_redis()
    val = await redis.get(_KEY_LAST_RUN_AT)
    if val is None:
        return None
    return val if isinstance(val, str) else val.decode()


async def set_last_run_at(iso_timestamp: str) -> None:
    redis = await get_redis()
    await redis.set(_KEY_LAST_RUN_AT, iso_timestamp)
