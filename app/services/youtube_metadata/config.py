"""
Konfigurasi Metadata Agent -- SEMUA di Redis (bukan .env), pola SAMA
PERSIS dgn app/services/youtube_discovery/config.py, TAPI namespace/key
TERPISAH krn ini agent yg berbeda dgn model+key OpenRouter sendiri
(user eksplisit kasih key terpisah utk agent ini, 2026-07-18).
"""
from __future__ import annotations

from app.infrastructure.redis.connection import get_redis

_KEY_INTERVAL_MINUTES = "youtube_metadata:interval_minutes"
_KEY_MODEL = "youtube_metadata:model"
_KEY_API_KEY = "youtube_metadata:api_key"
_KEY_RUNNING_LOCK = "youtube_metadata:running_lock"
_KEY_LAST_RUN_AT = "youtube_metadata:last_run_at"
_KEY_REFRESH_AGE_HOURS = "youtube_metadata:refresh_age_hours"
_KEY_REFRESH_BATCH_SIZE = "youtube_metadata:refresh_batch_size"
_KEY_ENRICH_BATCH_SIZE = "youtube_metadata:enrich_batch_size"

DEFAULT_INTERVAL_MINUTES = 30
# CATATAN 2026-07-18: meta-llama/llama-3.3-70b-instruct:free (default awal,
# sama dgn Discovery Agent) ternyata rate-limited TERUS-MENERUS oleh provider
# backing-nya ("Venice") saat live test -- 100% gagal utk 20 kandidat
# pertama. Ganti ke model gratis LAIN (provider beda) supaya tidak berbagi
# bottleneck yg sama dgn Discovery Agent.
DEFAULT_MODEL = "openai/gpt-oss-20b:free"
ALLOWED_INTERVAL_MINUTES = {15, 30, 60, 240}

# Batch fase ENRICH (post YouTube baru yg blm py baris youtube_video_metadata
# -- ini yg menentukan kecepatan mengejar backlog). Awalnya konstanta hardcode
# `BATCH_SIZE=20`, dijadikan Redis-configurable 2026-07-18 (permintaan user
# naikkan throughput utk mengejar backlog 8735 post demi analisis) -- nama
# lama `BATCH_SIZE` DIHAPUS, semua caller pakai get_enrich_batch_size().
DEFAULT_ENRICH_BATCH_SIZE = 20
ALLOWED_ENRICH_BATCH_SIZE = {10, 20, 50, 100}

# Stage 2 (2026-07-18, permintaan user "agar data lengkap dan selalu
# terauto refresh datanya semua"): baris youtube_video_metadata yg SUDAH
# ter-enrich tapi fetched_at-nya lebih tua dari refresh_age_hours akan
# di-refresh ulang (views/likes/comments/subscriber + komentar baru) tiap
# run, jadi TIDAK cuma sekali ambil lalu basi selamanya.
DEFAULT_REFRESH_AGE_HOURS = 6
DEFAULT_REFRESH_BATCH_SIZE = 20
ALLOWED_REFRESH_AGE_HOURS = {1, 3, 6, 12, 24}
ALLOWED_REFRESH_BATCH_SIZE = {10, 20, 50, 100}

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


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


async def get_model() -> str:
    redis = await get_redis()
    raw = await redis.get(_KEY_MODEL)
    if raw:
        return raw if isinstance(raw, str) else raw.decode()
    await redis.set(_KEY_MODEL, DEFAULT_MODEL)
    return DEFAULT_MODEL


async def set_model(model: str) -> str:
    model = model.strip()
    if not model:
        raise ValueError("model tidak boleh kosong")
    redis = await get_redis()
    await redis.set(_KEY_MODEL, model)
    return model


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


async def get_refresh_batch_size() -> int:
    redis = await get_redis()
    raw = await redis.get(_KEY_REFRESH_BATCH_SIZE)
    if raw is not None:
        return int(raw)
    await redis.set(_KEY_REFRESH_BATCH_SIZE, str(DEFAULT_REFRESH_BATCH_SIZE))
    return DEFAULT_REFRESH_BATCH_SIZE


async def set_refresh_batch_size(size: int) -> int:
    if size not in ALLOWED_REFRESH_BATCH_SIZE:
        raise ValueError(f"refresh_batch_size harus salah satu dari {sorted(ALLOWED_REFRESH_BATCH_SIZE)}")
    redis = await get_redis()
    await redis.set(_KEY_REFRESH_BATCH_SIZE, str(size))
    return size


async def get_enrich_batch_size() -> int:
    redis = await get_redis()
    raw = await redis.get(_KEY_ENRICH_BATCH_SIZE)
    if raw is not None:
        return int(raw)
    await redis.set(_KEY_ENRICH_BATCH_SIZE, str(DEFAULT_ENRICH_BATCH_SIZE))
    return DEFAULT_ENRICH_BATCH_SIZE


async def set_enrich_batch_size(size: int) -> int:
    if size not in ALLOWED_ENRICH_BATCH_SIZE:
        raise ValueError(f"enrich_batch_size harus salah satu dari {sorted(ALLOWED_ENRICH_BATCH_SIZE)}")
    redis = await get_redis()
    await redis.set(_KEY_ENRICH_BATCH_SIZE, str(size))
    return size
