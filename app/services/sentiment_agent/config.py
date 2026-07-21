"""
Konfigurasi Sentiment Agent -- SEMUA di Redis (bukan .env), pola SAMA
PERSIS dgn app/services/youtube_metadata/config.py, namespace TERPISAH
(`sentiment_agent:*`) krn model+key OpenRouter-nya sendiri (user kasih key
terpisah lagi, 2026-07-18).
"""
from __future__ import annotations

from app.infrastructure.redis.connection import get_redis

_KEY_INTERVAL_MINUTES = "sentiment_agent:interval_minutes"
_KEY_MODEL = "sentiment_agent:model"
_KEY_API_KEY = "sentiment_agent:api_key"
_KEY_BATCH_SIZE = "sentiment_agent:batch_size"
_KEY_RUNNING_LOCK = "sentiment_agent:running_lock"
_KEY_LAST_RUN_AT = "sentiment_agent:last_run_at"
_KEY_TIEBREAKER_MODEL = "sentiment_agent:tiebreaker_model"
_KEY_TIEBREAKER_API_KEY = "sentiment_agent:tiebreaker_api_key"

DEFAULT_INTERVAL_MINUTES = 30
# openrouter/auto-beta (usulan awal user) TERNYATA berbayar (pricing -1 =
# "tergantung model yg dipilih otomatis", BUKAN gratis) -- dikonfirmasi via
# GET https://openrouter.ai/api/v1/models. User pilih openai/gpt-oss-20b:free
# sbg default (model SAMA yg sudah terbukti stabil di Metadata Agent, 95%
# sukses saat live test, drpd meta-llama/llama-3.3-70b-instruct:free yg
# beberapa kali kena rate-limit 429 di sesi ini).
DEFAULT_MODEL = "openai/gpt-oss-20b:free"
ALLOWED_INTERVAL_MINUTES = {15, 30, 60, 240}
DEFAULT_BATCH_SIZE = 20
ALLOWED_BATCH_SIZE = {10, 20, 50, 100}

# Tie-breaker (2026-07-18): LLM KEDUA dipanggil HANYA saat lexicon vs LLM
# pertama tidak sepakat -- provider BEDA dari DEFAULT_MODEL (openai/gpt-oss)
# supaya penilaiannya genuinely independen, bukan model yg sama menilai
# ulang. google/gemma-4-31b-it:free (percobaan pertama) DAN
# qwen/qwen3-next-80b-a3b-instruct:free ternyata sedang congested di sisi
# provider (Google AI Studio / Venice, "temporarily rate-limited upstream")
# saat live test -- ganti ke nvidia/nemotron-nano-9b-v2:free yg TERBUKTI
# jalan saat live test (2026-07-18).
DEFAULT_TIEBREAKER_MODEL = "nvidia/nemotron-nano-9b-v2:free"

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


async def get_tiebreaker_model() -> str:
    redis = await get_redis()
    raw = await redis.get(_KEY_TIEBREAKER_MODEL)
    if raw:
        return raw if isinstance(raw, str) else raw.decode()
    await redis.set(_KEY_TIEBREAKER_MODEL, DEFAULT_TIEBREAKER_MODEL)
    return DEFAULT_TIEBREAKER_MODEL


async def set_tiebreaker_model(model: str) -> str:
    model = model.strip()
    if not model:
        raise ValueError("tiebreaker_model tidak boleh kosong")
    redis = await get_redis()
    await redis.set(_KEY_TIEBREAKER_MODEL, model)
    return model


async def get_tiebreaker_api_key() -> str | None:
    redis = await get_redis()
    raw = await redis.get(_KEY_TIEBREAKER_API_KEY)
    if raw is None:
        return None
    return raw if isinstance(raw, str) else raw.decode()


async def set_tiebreaker_api_key(api_key: str) -> None:
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("tiebreaker_api_key tidak boleh kosong")
    redis = await get_redis()
    await redis.set(_KEY_TIEBREAKER_API_KEY, api_key)
