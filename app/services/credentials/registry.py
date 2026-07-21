"""
Registry TERPUSAT semua credential third-party yang bisa dikelola lewat
satu halaman dashboard ("Kelola API Key", permintaan user 2026-07-18) --
sebelumnya tersebar di banyak tab agent berbeda (9 credential) DAN 9 lagi
yang HANYA bisa diubah lewat .env + rebuild image sama sekali.

Dua jenis entry:
  - "native": SUDAH punya config module Redis sendiri (per-agent, get/set
    async spesifik) -- registry ini cuma MENDELEGASIKAN ke fungsi yg sudah
    ada, TIDAK duplikasi logic penyimpanan. Efek LANGSUNG aktif (sudah
    dibaca live oleh agent masing2 tiap run).
  - "env_override": SEBELUMNYA cuma bisa lewat .env (Apify, EnsembleData,
    Anthropic, OpenAI, Firecrawl, Tavily, YouTube global, Facebook,
    Instagram) -- sekarang disimpan di Redis key "credentials:<name>",
    dibaca via property Settings.<name> (lihat app/shared/config.py
    _resolve_credential()) yg otomatis dipakai oleh SEMUA titik kode yg
    SUDAH baca settings.<name> (69 lokasi, TIDAK ADA yg perlu diubah).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from app.infrastructure.redis.connection import get_redis
from app.shared.config import OVERRIDABLE_CREDENTIAL_NAMES, settings


def mask_value(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


@dataclass(frozen=True)
class CredentialEntry:
    id: str
    label: str
    category: str
    used_by: str
    kind: str  # "native" | "env_override"


# ── env_override: nama HARUS SAMA PERSIS dgn key di
# app.shared.config.OVERRIDABLE_CREDENTIAL_NAMES (dicek via test) ───────────
_ENV_OVERRIDE_ENTRIES = [
    CredentialEntry("apify_api_token", "Apify API Token", "Apify",
                     "Facebook/Instagram/TikTok/Twitter search & scraping (SATU token dipakai bersama)", "env_override"),
    CredentialEntry("ensemble_data_api_token", "EnsembleData API Token", "EnsembleData",
                     "Instagram/YouTube scraping fallback", "env_override"),
    CredentialEntry("anthropic_api_key", "Anthropic API Key", "Anthropic",
                     "AI viral-discovery provider (Claude)", "env_override"),
    CredentialEntry("openai_api_key", "OpenAI API Key", "OpenAI",
                     "AI viral-discovery provider alternatif", "env_override"),
    CredentialEntry("firecrawl_api_key", "Firecrawl API Key", "Firecrawl",
                     "Web search/scrape utk provider Ollama + News Fase 2", "env_override"),
    CredentialEntry("tavily_api_key", "Tavily API Key", "Tavily",
                     "Fallback web search utk provider Ollama", "env_override"),
    CredentialEntry("youtube_data_api_key", "YouTube Data API Key (global)", "YouTube Data API v3",
                     "Key default/fallback -- dipakai kalau agent tidak punya key sendiri", "env_override"),
    CredentialEntry("facebook_access_token", "Facebook Access Token", "Facebook / Meta Graph API",
                     "GET /facebook/posts (Page yang dikelola sendiri)", "env_override"),
    CredentialEntry("instagram_session_id", "Instagram Session ID", "Instagram (cookie)",
                     "Scraping tanpa EnsembleData (legacy/alt)", "env_override"),
    CredentialEntry("instagram_csrf_token", "Instagram CSRF Token", "Instagram (cookie)",
                     "Pasangan session_id di atas", "env_override"),
]
assert {e.id for e in _ENV_OVERRIDE_ENTRIES} == set(OVERRIDABLE_CREDENTIAL_NAMES)

_NATIVE_ENTRIES = [
    CredentialEntry("yt_discovery1_openrouter", "OpenRouter (utama)", "OpenRouter",
                     "YouTube Discovery Agent 1 -- validasi kandidat", "native"),
    CredentialEntry("yt_discovery1_openrouter_fallback", "OpenRouter (cadangan/agent 2)", "OpenRouter",
                     "Discovery Agent 1 -- dipakai otomatis saat key utama rate-limit", "native"),
    CredentialEntry("yt_discovery1_youtube", "YouTube Data API", "YouTube Data API v3",
                     "Discovery Agent 1 -- jatuh ke key global kalau kosong", "native"),
    CredentialEntry("yt_discovery2_openrouter", "OpenRouter", "OpenRouter",
                     "YouTube Discovery Agent 2 (terpisah, HANYA topic-guided)", "native"),
    CredentialEntry("yt_discovery2_youtube", "YouTube Data API", "YouTube Data API v3",
                     "Discovery Agent 2 -- WAJIB diisi sendiri, tanpa fallback .env", "native"),
    CredentialEntry("yt_metadata_openrouter", "OpenRouter", "OpenRouter",
                     "Metadata Agent -- viral_context video", "native"),
    CredentialEntry("sentiment_openrouter", "OpenRouter (primer)", "OpenRouter",
                     "Sentiment Agent -- opini kedua LLM utk lexicon", "native"),
    CredentialEntry("sentiment_openrouter_tiebreaker", "OpenRouter (tie-breaker)", "OpenRouter",
                     "Sentiment Agent -- suara ketiga saat lexicon vs LLM primer tidak sepakat", "native"),
    CredentialEntry("views_refresh_youtube", "YouTube Data API", "YouTube Data API v3",
                     "Views Refresh Agent -- project Google Cloud terpisah", "native"),
    CredentialEntry("whatsapp_fonnte_token", "Fonnte Device Token", "WhatsApp (Fonnte)",
                     "Kirim notifikasi topik viral baru ke WhatsApp tiap jam", "native"),
    CredentialEntry("whatsapp_target_numbers", "Nomor Tujuan WA", "WhatsApp (Fonnte)",
                     "Daftar nomor penerima (pisahkan koma kalau lebih dari 1), mis. 6281234567890,6289876543210", "native"),
]

ALL_ENTRIES: list[CredentialEntry] = _NATIVE_ENTRIES + _ENV_OVERRIDE_ENTRIES


async def _native_getset(entry_id: str) -> tuple[Callable[[], Awaitable[str | None]], Callable[[str], Awaitable[None]]]:
    if entry_id == "yt_discovery1_openrouter":
        from app.services.youtube_discovery import config as m
        return m.get_api_key, m.set_api_key
    if entry_id == "yt_discovery1_openrouter_fallback":
        from app.services.youtube_discovery import config as m
        return m.get_fallback_api_key, m.set_fallback_api_key
    if entry_id == "yt_discovery1_youtube":
        from app.services.youtube_discovery import config as m
        return m.get_youtube_api_key, m.set_youtube_api_key
    if entry_id == "yt_discovery2_openrouter":
        from app.services.youtube_discovery import agent2_config as m
        return m.get_api_key, m.set_api_key
    if entry_id == "yt_discovery2_youtube":
        from app.services.youtube_discovery import agent2_config as m
        return m.get_youtube_api_key, m.set_youtube_api_key
    if entry_id == "yt_metadata_openrouter":
        from app.services.youtube_metadata import config as m
        return m.get_api_key, m.set_api_key
    if entry_id == "sentiment_openrouter":
        from app.services.sentiment_agent import config as m
        return m.get_api_key, m.set_api_key
    if entry_id == "sentiment_openrouter_tiebreaker":
        from app.services.sentiment_agent import config as m
        return m.get_tiebreaker_api_key, m.set_tiebreaker_api_key
    if entry_id == "views_refresh_youtube":
        from app.services.views_refresh_agent import config as m
        return m.get_api_key, m.set_api_key
    if entry_id == "whatsapp_fonnte_token":
        from app.services.whatsapp_notify import config as m
        return m.get_fonnte_token, m.set_fonnte_token
    if entry_id == "whatsapp_target_numbers":
        from app.services.whatsapp_notify import config as m

        async def _get() -> str | None:
            numbers = await m.get_target_numbers()
            return ",".join(numbers) if numbers else None

        return _get, m.set_target_numbers
    raise KeyError(f"credential id tidak dikenal: {entry_id}")


async def get_credential_value(entry: CredentialEntry) -> str | None:
    if entry.kind == "native":
        get_fn, _ = await _native_getset(entry.id)
        return await get_fn()
    # env_override
    redis = await get_redis()
    val = await redis.get(f"credentials:{entry.id}")
    if val:
        return val if isinstance(val, str) else val.decode()
    return getattr(settings, f"{entry.id}_env") or None


async def set_credential_value(entry: CredentialEntry, value: str) -> None:
    value = value.strip()
    if not value:
        raise ValueError("Nilai tidak boleh kosong")
    if entry.kind == "native":
        _, set_fn = await _native_getset(entry.id)
        await set_fn(value)
        return
    redis = await get_redis()
    await redis.set(f"credentials:{entry.id}", value)


async def list_credentials() -> list[dict]:
    result = []
    for entry in ALL_ENTRIES:
        value = await get_credential_value(entry)
        result.append({
            "id": entry.id,
            "label": entry.label,
            "category": entry.category,
            "used_by": entry.used_by,
            "is_set": bool(value),
            "masked_value": mask_value(value),
            "live_effect": (
                "Langsung aktif, tanpa restart" if entry.kind == "native"
                else "Langsung aktif (dibaca semua kode yg sudah pakai settings ini), tanpa restart"
            ),
        })
    return result
