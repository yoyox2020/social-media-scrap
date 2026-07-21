"""
Registry TERPUSAT credential third-party generik (2026-07-22, API v2).

CATATAN RESTRUKTURISASI: entry "native" (per-agent, mendelegasikan ke
config module masing2 -- YouTube Discovery/Metadata/Views Refresh/
Sentiment/WhatsApp) DIHAPUS bersamaan dgn service-service tsb (lihat
docstring app/main.py). Yang tersisa cuma "env_override": credential
generik disimpan di Redis key "credentials:<name>", dibaca via property
Settings.<name>. Dipakai app/services/agent_registry utk key agent
custom yg linked ke slot generik ini (mis. "apify_api_token").
"""
from __future__ import annotations

from dataclasses import dataclass

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

ALL_ENTRIES: list[CredentialEntry] = _ENV_OVERRIDE_ENTRIES


async def get_credential_value(entry: CredentialEntry) -> str | None:
    redis = await get_redis()
    val = await redis.get(f"credentials:{entry.id}")
    if val:
        return val if isinstance(val, str) else val.decode()
    return getattr(settings, f"{entry.id}_env") or None


async def set_credential_value(entry: CredentialEntry, value: str) -> None:
    value = value.strip()
    if not value:
        raise ValueError("Nilai tidak boleh kosong")
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
