"""
Apify Facebook SEARCH by keyword — actor `danek/facebook-search-ppr`
(settings.facebook_search_actor_id), BEDA dari app/integrations/apify/facebook.py
(`scrape_facebook_via_apify`) yang cuma bisa scrape profil yang SUDAH diketahui
namanya. Actor ini genuinely search Facebook by kata kunci, hasilnya post-post
nyata + data author (id/url) terstruktur — jadi TIDAK perlu AI menebak akun
sama sekali (beda dengan app/ai/llm/viral_discovery_service.py yang harus
menebak dari teks bebas hasil web search umum).

Dipanggil oleh app/services/facebook/trend_scrape_service.py:
discover_facebook_topic_by_keyword() — dipicu dari POST /facebook/discover.

Harga: pay-per-result (~$0.003/hasil per Juli 2026, cek pricing terbaru di
apify.com/danek/facebook-search-ppr). Akun Apify FREE dibatasi 5 hasil saja
(readme actor: "Free users are limited to 5 results only").
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from apify_client import ApifyClient

from app.shared.config import settings
from app.shared.exceptions import ExternalAPIError

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Ekstraksi identifier akun dari data `author` (JSON terstruktur, BUKAN regex
# di teks bebas seperti di viral_discovery_service.py — di sini datanya sudah
# rapi dari Apify, cuma perlu handle 2 bentuk URL Facebook yang beda).
#
# GAMPANG DIMODIFIKASI: tambah pattern baru di _RESERVED_SLUGS kalau ternyata
# ada bentuk URL author lain yang bukan identifier valid.
# ─────────────────────────────────────────────────────────────────────────────
_FB_PEOPLE_URL_RE = re.compile(r"facebook\.com/people/[^/]+/(\d+)", re.IGNORECASE)
_FB_SIMPLE_URL_RE = re.compile(r"facebook\.com/([A-Za-z0-9_.\-]+)", re.IGNORECASE)
_RESERVED_SLUGS = {"people", "profile.php", "groups", "watch", "permalink.php", "share", "photo.php"}


def extract_identifier(author: dict[str, Any] | None) -> str | None:
    """
    Ambil identifier yang bisa dipakai ulang untuk
    scrape_facebook_posts_via_provider() (butuh slug/ID valid untuk URL
    https://facebook.com/{identifier}).

    Prioritas:
    1. URL bentuk ".../people/<nama>/<id_numerik>/" -> ambil ID numeriknya
    2. URL bentuk ".../<slug>" biasa -> ambil slug-nya (kalau bukan reserved)
    3. Fallback: author['id'] kalau murni angka (Page ID resmi)
    """
    author = author or {}
    url = author.get("url", "") or ""

    m = _FB_PEOPLE_URL_RE.search(url)
    if m:
        return m.group(1)

    m = _FB_SIMPLE_URL_RE.search(url)
    if m and m.group(1).lower() not in _RESERVED_SLUGS:
        return m.group(1)

    raw_id = str(author.get("id", ""))
    if raw_id.isdigit():
        return raw_id

    return None


def _run_search_sync(query: str, max_results: int, recent_only: bool, location: str | None) -> list[dict[str, Any]]:
    if not settings.apify_api_token:
        raise ExternalAPIError(service="Apify", message="APIFY_API_TOKEN belum di-set di .env")

    client = ApifyClient(settings.apify_api_token)
    run_input: dict[str, Any] = {
        "query": query,
        "search_type": "posts",
        "max_posts": max_results,
        "recent_posts": recent_only,
    }
    if location:
        run_input["location"] = location

    logger.info("[Apify] facebook-search-ppr query=%r input=%s", query, run_input)
    run = client.actor(settings.facebook_search_actor_id).call(run_input=run_input)

    if run.status != "SUCCEEDED":
        raise ExternalAPIError(service="Apify", message=f"Run status={run.status} untuk query={query!r}")

    return list(client.dataset(run.default_dataset_id).iterate_items())


async def search_facebook_by_keyword(
    query: str,
    max_results: int = 10,
    recent_only: bool = True,
    location: str | None = None,
) -> list[dict[str, Any]]:
    """
    Search Facebook langsung by keyword (BUKAN scrape profil yang sudah
    diketahui) — return list post mentah dari Apify, masing-masing punya
    field `author` (dict dengan id/url/name) yang bisa diparse via
    extract_identifier().
    """
    return await asyncio.to_thread(_run_search_sync, query, max_results, recent_only, location)
