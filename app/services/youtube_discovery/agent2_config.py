"""
Konfigurasi YouTube Discovery Agent 2 -- AGENT TERPISAH dari Discovery Agent
utama (lihat config.py), BUKAN sekadar key cadangan. Permintaan user
2026-07-18: "harusnya discovery ada dua agent dengan membawa masing-masing
api key data youtube v3" -- Agent 2 punya YouTube Data API key SENDIRI +
OpenRouter key/model SENDIRI + jadwal SENDIRI (default tiap 1 jam) + lock
SENDIRI, TIDAK berbagi kuota apa pun dgn Agent 1 (config.py). Agent 2 HANYA
topic-guided (cari video baru terkait topic-search yg SUDAH ada di sistem),
TIDAK ada mode free-discovery -- lihat run_discovery_agent_2() di agent.py.

SEMUA di Redis (bukan .env), pola SAMA dgn config.py: efeknya langsung aktif
di run berikutnya tanpa restart/deploy. Diatur dari tab /scraping-status.
"""
from __future__ import annotations

from app.infrastructure.redis.connection import get_redis

_KEY_INTERVAL_HOURS = "youtube_discovery_agent2:interval_hours"
_KEY_MODEL = "youtube_discovery_agent2:model"
_KEY_API_KEY = "youtube_discovery_agent2:api_key"
_KEY_YOUTUBE_API_KEY = "youtube_discovery_agent2:youtube_api_key"
_KEY_ENABLED = "youtube_discovery_agent2:enabled"
_KEY_LAST_RUN_AT = "youtube_discovery_agent2:last_run_at"
_KEY_RUNNING_LOCK = "youtube_discovery_agent2:running_lock"

DEFAULT_INTERVAL_HOURS = 1
# nemotron -- model REASONING (habiskan token dulu utk 'reasoning' sblm
# jawaban), max_tokens di openrouter_client.py SUDAH dinaikkan ke 600 utk
# mengakomodasi ini (ditemukan 2026-07-18 pas tes model ini via API langsung).
DEFAULT_MODEL = "nvidia/nemotron-nano-9b-v2:free"
ALLOWED_INTERVAL_HOURS = {1, 4, 8, 12}


async def get_interval_hours() -> int:
    redis = await get_redis()
    raw = await redis.get(_KEY_INTERVAL_HOURS)
    if raw is not None:
        return int(raw)
    await redis.set(_KEY_INTERVAL_HOURS, str(DEFAULT_INTERVAL_HOURS))
    return DEFAULT_INTERVAL_HOURS


async def set_interval_hours(hours: int) -> int:
    if hours not in ALLOWED_INTERVAL_HOURS:
        raise ValueError(f"interval_hours harus salah satu dari {sorted(ALLOWED_INTERVAL_HOURS)}")
    redis = await get_redis()
    await redis.set(_KEY_INTERVAL_HOURS, str(hours))
    return hours


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
    """API key OpenRouter MILIK Agent 2 -- kuota terpisah TOTAL dari Agent 1
    (config.py), supaya rate-limit salah satu TIDAK memengaruhi yg lain."""
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


async def get_youtube_api_key() -> str | None:
    """YouTube Data API key MILIK Agent 2 -- kuota terpisah dari key yg
    dipakai Agent 1 (settings.youtube_data_api_key / config.get_youtube_api_key()).
    TIDAK ada fallback ke .env global -- Agent 2 memang didesain bawa key
    sendiri (permintaan eksplisit user), kalau kosong agent ini idle."""
    redis = await get_redis()
    raw = await redis.get(_KEY_YOUTUBE_API_KEY)
    if raw is None:
        return None
    return raw if isinstance(raw, str) else raw.decode()


async def set_youtube_api_key(api_key: str) -> None:
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("api_key tidak boleh kosong")
    redis = await get_redis()
    await redis.set(_KEY_YOUTUBE_API_KEY, api_key)


def mask_api_key(api_key: str | None) -> str | None:
    if not api_key:
        return None
    if len(api_key) <= 4:
        return "*" * len(api_key)
    return "*" * (len(api_key) - 4) + api_key[-4:]


async def get_enabled() -> bool:
    """Tombol ON/OFF Agent 2 -- default AKTIF. Bisa dimatikan kapan saja dari
    /scraping-status TANPA menghapus key/model yg tersimpan."""
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


async def acquire_running_lock() -> bool:
    redis = await get_redis()
    return bool(await redis.set(_KEY_RUNNING_LOCK, "1", nx=True, ex=7200))


async def release_running_lock() -> None:
    redis = await get_redis()
    await redis.delete(_KEY_RUNNING_LOCK)


async def is_running() -> bool:
    redis = await get_redis()
    return bool(await redis.get(_KEY_RUNNING_LOCK))
