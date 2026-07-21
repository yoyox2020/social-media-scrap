"""
Konfigurasi YouTube Discovery Agent -- SEMUA di Redis (bukan .env), pola
SAMA PERSIS dgn app/services/search_topics/notification_service.py
(get_threshold/set_threshold, get_lookback_days/set_lookback_days): efeknya
LANGSUNG aktif di run berikutnya, tanpa restart/deploy apa pun. Diatur dari
tab baru di /scraping-status.
"""
from __future__ import annotations

from app.infrastructure.redis.connection import get_redis

_KEY_INTERVAL_HOURS = "youtube_discovery:interval_hours"
_KEY_MODEL = "youtube_discovery:model"
_KEY_API_KEY = "youtube_discovery:api_key"
_KEY_FALLBACK_MODEL = "youtube_discovery:fallback_model"
_KEY_FALLBACK_API_KEY = "youtube_discovery:fallback_api_key"
_KEY_YOUTUBE_API_KEY = "youtube_discovery:youtube_api_key"
_KEY_FALLBACK_ENABLED = "youtube_discovery:fallback_enabled"
_KEY_LAST_RUN_AT = "youtube_discovery:last_run_at"
_KEY_RUNNING_LOCK = "youtube_discovery:running_lock"

DEFAULT_INTERVAL_HOURS = 4
# Model gratis OpenRouter -- CATATAN 2026-07-18: default sebelumnya
# (deepseek/deepseek-chat-v3-0324:free) di-nonaktifkan OpenRouter dari tier
# gratis TANPA pemberitahuan (ketahuan pas live test, 184 kandidat gagal
# divalidasi). Tier gratis OpenRouter memang bisa berubah sewaktu-waktu --
# kalau model ini suatu saat juga di-deprecate, ganti lewat
# PATCH /youtube/discovery-agent/config (real-time, tidak perlu redeploy).
DEFAULT_MODEL = "meta-llama/llama-3.3-70b-instruct:free"
# Key/model CADANGAN ("agent 2") -- dipakai validate_candidate() HANYA saat
# key/model UTAMA kena rate-limit (429), permintaan user 2026-07-18 supaya
# kandidat tidak asal di-skip cuma krn limit harian/menit key utama habis.
# nemotron adalah model REASONING (menghabiskan token utk 'reasoning' dulu
# sebelum jawaban) -- lihat max_tokens di openrouter_client.py.
DEFAULT_FALLBACK_MODEL = "nvidia/nemotron-nano-9b-v2:free"
ALLOWED_INTERVAL_HOURS = {1, 4, 8, 12}

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


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
    """Return API key MENTAH -- dipakai internal utk panggil OpenRouter,
    JANGAN diekspos ke response API (lihat mask_api_key())."""
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


async def get_fallback_model() -> str:
    redis = await get_redis()
    raw = await redis.get(_KEY_FALLBACK_MODEL)
    if raw:
        return raw if isinstance(raw, str) else raw.decode()
    await redis.set(_KEY_FALLBACK_MODEL, DEFAULT_FALLBACK_MODEL)
    return DEFAULT_FALLBACK_MODEL


async def set_fallback_model(model: str) -> str:
    model = model.strip()
    if not model:
        raise ValueError("model tidak boleh kosong")
    redis = await get_redis()
    await redis.set(_KEY_FALLBACK_MODEL, model)
    return model


async def get_fallback_api_key() -> str | None:
    """Return API key CADANGAN mentah, atau None kalau belum diatur (fallback
    TIDAK dipakai sama sekali kalau kosong -- lihat agent.py)."""
    redis = await get_redis()
    raw = await redis.get(_KEY_FALLBACK_API_KEY)
    if raw is None:
        return None
    return raw if isinstance(raw, str) else raw.decode()


async def set_fallback_api_key(api_key: str) -> None:
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("api_key tidak boleh kosong")
    redis = await get_redis()
    await redis.set(_KEY_FALLBACK_API_KEY, api_key)


async def get_fallback_enabled() -> bool:
    """Tombol ON/OFF fallback ("agent 2") -- default AKTIF begitu key
    cadangan diisi. Bisa dimatikan kapan saja dari /scraping-status TANPA
    menghapus key/model cadangan (biar gampang dinyalakan lagi)."""
    redis = await get_redis()
    raw = await redis.get(_KEY_FALLBACK_ENABLED)
    if raw is None:
        return True
    val = raw if isinstance(raw, str) else raw.decode()
    return val == "1"


async def set_fallback_enabled(enabled: bool) -> bool:
    redis = await get_redis()
    await redis.set(_KEY_FALLBACK_ENABLED, "1" if enabled else "0")
    return enabled


async def get_youtube_api_key() -> str | None:
    """YouTube Data API key KHUSUS agent ini (Redis, switchable dari
    dashboard) -- kalau belum diatur, caller (agent.py) jatuh ke
    settings.youtube_data_api_key (.env, dipakai bersama Metadata Agent dkk).
    Pola SAMA dgn app/services/views_refresh_agent/config.py (key YouTube
    terpisah per-agent, BUKAN OpenRouter -- lihat get_api_key() utk itu)."""
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
    """4 karakter terakhir doang -- API key JANGAN PERNAH kekirim utuh di
    response GET (siapa saja yg buka tab scraping-status bisa lihat)."""
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
    """SETNX-style lock -- cegah 2 run tumpang tindih kalau run sebelumnya
    belum selesai pas jadwal berikutnya sudah waktunya cek lagi. TTL 2 jam
    sbg safety net (auto-lepas kalau proses crash tanpa sempat release)."""
    redis = await get_redis()
    return bool(await redis.set(_KEY_RUNNING_LOCK, "1", nx=True, ex=7200))


async def release_running_lock() -> None:
    redis = await get_redis()
    await redis.delete(_KEY_RUNNING_LOCK)


async def is_running() -> bool:
    redis = await get_redis()
    return bool(await redis.get(_KEY_RUNNING_LOCK))
