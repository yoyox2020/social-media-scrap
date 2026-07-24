"""Konfigurasi Sentiment Agent (LLM tiebreaker) -- model+batch_size di
Redis (pola SAMA dgn youtube_metadata/config.py, bisa diubah tanpa
redeploy), TAPI api_key SEKARANG lewat `rotation_key_bank` (agent_name
"agent_sentiment"/"agent_sentiment_tiebreaker") -- BUKAN raw key
tersimpan di Redis spt kode lama (`main` branch), krn arsitektur v2 saat
ini sudah py mekanisme rotasi+auto-retry key generik yg SAMA dipakai
TikTok/YouTube AI-summary (app/services/rotation_key_bank/service.py),
lebih baik drpd 1 key statis tanpa rotasi."""
from __future__ import annotations

from app.infrastructure.redis.connection import get_redis

_KEY_MODEL = "sentiment_agent:model"
_KEY_BATCH_SIZE = "sentiment_agent:batch_size"
_KEY_TIEBREAKER_MODEL = "sentiment_agent:tiebreaker_model"
_KEY_RUNNING_LOCK = "sentiment_agent:running_lock"

# Model default SAMA dgn yg sudah terbukti stabil di Metadata Agent/TikTok
# AI-summary (95% sukses saat live test dulu) -- BUKAN pilihan baru.
DEFAULT_MODEL = "openai/gpt-oss-20b:free"
# Tie-breaker WAJIB provider/model BEDA dari DEFAULT_MODEL supaya
# penilaiannya independen (bukan model sama menilai ulang dirinya sendiri).
DEFAULT_TIEBREAKER_MODEL = "nvidia/nemotron-nano-9b-v2:free"
DEFAULT_BATCH_SIZE = 20
ALLOWED_BATCH_SIZE = {10, 20, 50, 100, 200}

AGENT_NAME_PRIMARY = "agent_sentiment"
AGENT_NAME_TIEBREAKER = "agent_sentiment_tiebreaker"


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


async def acquire_running_lock() -> bool:
    redis = await get_redis()
    return bool(await redis.set(_KEY_RUNNING_LOCK, "1", nx=True, ex=3600))


async def release_running_lock() -> None:
    redis = await get_redis()
    await redis.delete(_KEY_RUNNING_LOCK)


async def is_running() -> bool:
    redis = await get_redis()
    return bool(await redis.get(_KEY_RUNNING_LOCK))
