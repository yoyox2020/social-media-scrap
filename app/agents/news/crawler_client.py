"""Child scraper News (2026-07-24) -- Firecrawl `/v1/search` (cari artikel
by keyword) + `/v1/scrape` (ambil isi lengkap per URL), rotasi key via
`third_party_apis` (provider="Firecrawl", platform_group="news") --
GANTI dari sistem pool bespoke lama (`app/services/news/config.py` di
`main`, tabel terpisah) supaya konsisten dgn platform lain sesi ini
(satu sistem rotasi generik utk semua provider).

BEDA dari platform medsos lain: artikel berita TIDAK py engagement publik
(likes/comments/shares/views SELALU 0, dikonfirmasi dari 184 post News
lama yg sudah ada) -- konsisten dilanjutkan di sini, BUKAN dipaksa isi
angka palsu. `compute_external_id()` (sha1(url)[:24]) REUSE PERSIS dari
kode lama (`main` commit 76ba889) supaya dedup konsisten dgn 184 artikel
lama yg ID-nya dibuat dgn fungsi yg sama.

Field metadata Firecrawl `/v1/scrape` (title/og:title, og:image/ogImage,
author, published-date candidates) DIVERIFIKASI LIVE 2026-07-10 (lihat
riwayat project) -- key beda2 per situs, best-effort multi-candidate."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.third_party_apis.service import get_next_available_key, mark_api_error

FIRECRAWL_BASE_URL = "https://api.firecrawl.dev/v1"
ROTATION_FAILURE_STATUS_CODES = {401, 402, 403, 429}
MAX_ROTATION_ATTEMPTS = 4
DEFAULT_SEARCH_LIMIT = 5
MAX_CONTENT_CHARS = 20000


def compute_external_id(url: str) -> str:
    """SAMA PERSIS dgn fungsi lama (`main` commit 76ba889) -- 184 artikel
    News lama di DB pakai ID hasil fungsi ini, WAJIB tetap sama supaya
    dedup jalan lintas kode lama/baru."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:24]


def _first(val: Any) -> str | None:
    if isinstance(val, list):
        for v in val:
            if v:
                return str(v)
        return None
    return str(val) if val else None


def _parse_published_at(meta: dict[str, Any]) -> datetime | None:
    candidates = [
        meta.get("datePublished"), meta.get("article:published_time"),
        meta.get("publishedTime"), meta.get("uploadDate"),
    ]
    for raw in candidates:
        raw = _first(raw)
        if not raw:
            continue
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


async def _call_firecrawl(db: AsyncSession, path: str, json_body: dict[str, Any], timeout: float) -> tuple[dict | None, str | None]:
    tried_key_ids: set = set()
    last_error: str | None = None

    for _attempt in range(MAX_ROTATION_ATTEMPTS):
        key_entry = await get_next_available_key(db, "Firecrawl", platform_group="news")
        if not key_entry or key_entry.id in tried_key_ids:
            last_error = "Semua key Firecrawl sudah dicoba & gagal -- menunggu jadwal berikutnya"
            break
        tried_key_ids.add(key_entry.id)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{FIRECRAWL_BASE_URL}{path}",
                    headers={"Authorization": f"Bearer {key_entry.api_key}"},
                    json=json_body,
                )
        except Exception as exc:
            await mark_api_error(db, key_entry.id, str(exc)[:500])
            last_error = str(exc)
            continue

        if resp.status_code in ROTATION_FAILURE_STATUS_CODES:
            await mark_api_error(db, key_entry.id, f"HTTP {resp.status_code}: {resp.text[:500]}")
            last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
            continue

        if resp.status_code != 200:
            last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
            break

        try:
            return resp.json(), None
        except ValueError:
            last_error = "response bukan JSON valid"
            break

    return None, last_error or "unknown"


async def _search_articles(db: AsyncSession, query: str, limit: int) -> tuple[list[dict], str | None]:
    data, error = await _call_firecrawl(db, "/search", {"query": query, "limit": limit}, timeout=20)
    if error:
        return [], error
    results = (data or {}).get("data") or []
    return [r for r in results if isinstance(r, dict) and r.get("url")], None


async def _scrape_article(db: AsyncSession, url: str) -> dict | None:
    data, error = await _call_firecrawl(db, "/scrape", {"url": url, "formats": ["markdown"]}, timeout=30)
    if error or not data:
        return None
    payload = (data or {}).get("data") or {}
    markdown = payload.get("markdown") or ""
    if not markdown:
        return None

    meta = payload.get("metadata") or {}
    title = _first(meta.get("title")) or _first(meta.get("og:title"))
    image = _first(meta.get("og:image")) or _first(meta.get("ogImage"))
    author = _first(meta.get("author"))
    published_at = _parse_published_at(meta)

    return {
        "external_id": compute_external_id(url),
        "content": markdown[:MAX_CONTENT_CHARS],
        "author": author,
        "url": url,
        "title": title,
        "image_url": image,
        "published_at": published_at,
        "metrics": {"views": 0, "likes": 0, "comments": 0, "shares": 0},
        "raw_data": {"search_meta": meta},
    }


async def _fetch_keyword(db: AsyncSession, keyword: str, max_articles: int) -> tuple[list[dict], str | None]:
    search_results, error = await _search_articles(db, keyword, max_articles)
    if error:
        return [], error

    articles: list[dict] = []
    for result in search_results:
        article = await _scrape_article(db, result["url"])
        if article:
            articles.append(article)
    return articles, None


async def fetch_via_keywords(db: AsyncSession, keywords: list[str], max_articles_per_keyword: int = DEFAULT_SEARCH_LIMIT) -> dict:
    all_posts: list[dict] = []
    errors: list[dict] = []
    for kw in keywords:
        posts, error = await _fetch_keyword(db, kw, max_articles_per_keyword)
        if error:
            errors.append({"keyword": kw, "error": error})
        else:
            all_posts.extend(posts)
    return {"success": True, "posts": all_posts, "errors": errors}
