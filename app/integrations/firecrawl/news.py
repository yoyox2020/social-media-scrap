"""
Firecrawl untuk News — search artikel by keyword + scrape isi artikel penuh.

`search_news_by_keyword()` pakai endpoint yang SAMA (`/v1/search`) dengan
`_firecrawl_search()` di app/ai/llm/viral_discovery_service.py, TAPI
diimplementasi ulang independen di sini (bukan import dari situ) — modul AI
discovery itu urusan viral_discovery, modul ini murni integrasi HTTP untuk
app/services/news/, supaya dua concern ini tidak saling bergantung.

`scrape_article()` (endpoint `/v1/scrape`) BARU, diverifikasi LIVE
2026-07-10 (bukan cuma baca dokumentasi Firecrawl): response bentuknya
`{"success": bool, "data": {"markdown": str, "metadata": {...}}}`.
`metadata` isinya OG-tags mentah situs asal (TIDAK ada skema baku, beda-beda
per situs) — kode ini ambil best-effort beberapa kandidat key umum
(`title`/`og:title`, `og:image`/`ogImage`, `author`), JANGAN diasumsikan
semua field selalu ada.

settings.firecrawl_api_key SAMA dengan yang sudah dipakai AI viral discovery
provider Ollama — tidak perlu API key baru.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.shared.config import settings
from app.shared.exceptions import ExternalAPIError

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.firecrawl.dev/v1"


def _first(val: Any) -> str | None:
    """Beberapa field metadata Firecrawl bisa berupa list (situs punya
    beberapa tag OG yang sama, misal og:image ganda) -- ambil elemen
    pertama yang non-kosong."""
    if isinstance(val, list):
        for v in val:
            if v:
                return str(v)
        return None
    return str(val) if val else None


async def search_news_by_keyword(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """
    Search artikel berita by keyword — return list mentah
    `[{"title", "description", "url"}, ...]` dari Firecrawl. Snippet-nya
    pendek (~150 karakter), untuk isi LENGKAP panggil scrape_article() per
    URL hasil ini.
    """
    if not settings.firecrawl_api_key:
        raise ExternalAPIError(service="Firecrawl", message="FIRECRAWL_API_KEY belum di-set di .env")

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{_BASE_URL}/search",
            headers={"Authorization": f"Bearer {settings.firecrawl_api_key}"},
            json={"query": query, "limit": max_results},
        )
        resp.raise_for_status()
        data = resp.json()

    return data.get("data") or []


async def scrape_article(url: str) -> dict[str, Any] | None:
    """
    Ambil isi LENGKAP satu artikel (markdown bersih + metadata) via Firecrawl
    `/v1/scrape`. Return None (BUKAN raise) kalau gagal — dipanggil per-artikel
    dalam batch oleh pemanggil, satu URL gagal tidak boleh menggagalkan semua.
    """
    if not settings.firecrawl_api_key:
        raise ExternalAPIError(service="Firecrawl", message="FIRECRAWL_API_KEY belum di-set di .env")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{_BASE_URL}/scrape",
                headers={"Authorization": f"Bearer {settings.firecrawl_api_key}"},
                json={"url": url, "formats": ["markdown"]},
            )
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:
        logger.warning("[Firecrawl] scrape_article gagal untuk url=%r: %s", url, exc)
        return None

    data = payload.get("data") or {}
    markdown = data.get("markdown") or ""
    if not markdown:
        return None

    meta = data.get("metadata") or {}
    title = _first(meta.get("title")) or _first(meta.get("og:title"))
    image = _first(meta.get("og:image")) or _first(meta.get("ogImage"))
    author = _first(meta.get("author"))

    return {
        "url": url,
        "title": title,
        "content": markdown,
        "image_url": image,
        "author": author,
        "raw_metadata": meta,
    }
