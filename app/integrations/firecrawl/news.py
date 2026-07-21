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

Key Firecrawl: SEJAK 2026-07-19, pakai POOL key khusus News (lihat
app/services/news/config.py) dgn AUTO-ROTASI kalau key aktif kena
quota/rate-limit (HTTP 429/402) -- permintaan user "auto switch jika kuota
habis, minimal 5 key firecrawl bisa dipakai". Kalau pool KOSONG (belum
pernah diisi), fallback ke settings.firecrawl_api_key (satu key, TANPA
rotasi, SAMA dgn yg dipakai AI viral discovery provider Ollama) --
backward-compatible.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from app.services.news import config as news_cfg
from app.shared.config import settings
from app.shared.exceptions import ExternalAPIError

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.firecrawl.dev/v1"

# HTTP status yg dianggap sinyal "kuota/rate-limit habis" utk key ini --
# 429 (rate limit, paling umum) + 402 (payment required, dipakai sejumlah
# provider utk "insufficient credits"). Status LAIN (500, network error,
# dll) TIDAK memicu rotasi -- itu bukan masalah quota, ganti key tidak
# akan membantu, malah bisa menutupi bug asli.
_QUOTA_ERROR_STATUS_CODES = {429, 402}


async def _call_firecrawl_with_rotation(path: str, json_body: dict[str, Any], timeout: float) -> httpx.Response:
    """Panggil Firecrawl dgn ROTASI OTOMATIS -- kalau key yg dipakai kena
    quota/rate-limit, tandai exhausted lalu coba key BERIKUTNYA di pool,
    sampai berhasil atau SEMUA key di pool sudah dicoba. Key yg SUDAH
    diketahui exhausted (dari panggilan SEBELUMNYA, masih dlm window TTL)
    dicoba PALING TERAKHIR (jangan buang waktu ke key yg kemungkinan besar
    masih habis), tapi TETAP dicoba kalau semua key "segar" sudah gagal --
    jaga2 kalau ternyata sudah pulih lebih cepat dari TTL asumsi kita."""
    pool = await news_cfg.get_pool()
    keys_to_try = pool if pool else ([settings.firecrawl_api_key] if settings.firecrawl_api_key else [])
    if not keys_to_try:
        raise ExternalAPIError(service="Firecrawl", message="Belum ada Firecrawl API key (pool News kosong & FIRECRAWL_API_KEY .env jg kosong)")

    if pool:
        exhausted_flags = {k: await news_cfg.is_exhausted(k) for k in keys_to_try}
        ordered_keys = sorted(keys_to_try, key=lambda k: exhausted_flags[k])
    else:
        ordered_keys = keys_to_try

    last_resp: httpx.Response | None = None
    for key in ordered_keys:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{_BASE_URL}{path}",
                headers={"Authorization": f"Bearer {key}"},
                json=json_body,
            )
        if resp.status_code in _QUOTA_ERROR_STATUS_CODES:
            logger.warning(
                "[Firecrawl] key %s kena quota/rate-limit (status=%s) di %s -- rotasi ke key berikutnya (pool=%d key)",
                news_cfg.mask_key(key), resp.status_code, path, len(ordered_keys),
            )
            if pool:
                await news_cfg.mark_exhausted(key)
            last_resp = resp
            continue
        return resp

    logger.error(
        "[Firecrawl] SEMUA %d key di pool kena quota/rate-limit utk %s -- request gagal total",
        len(ordered_keys), path,
    )
    last_resp.raise_for_status()
    return last_resp


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


def _parse_published_at(meta: dict[str, Any]) -> datetime | None:
    """
    Coba ekstrak tanggal publish ASLI artikel dari metadata Firecrawl
    (JSON-LD schema.org / OG tags situs sumber) -- key beda-beda per situs,
    dicoba berurutan. Diverifikasi LIVE 2026-07-10 lewat data yang sudah
    tersimpan: `datePublished`/`uploadDate` (JSON-LD) dan
    `article:published_time`/`publishedTime` (OG tag) semuanya PERNAH
    ditemukan valid di artikel nyata.

    CATATAN PENTING: banyak URL hasil search Firecrawl ternyata halaman
    HOMEPAGE/KATEGORI/TAG (bukan artikel tunggal, mis. kompas.com,
    cnnindonesia.com tanpa path) -- situs itu WAJAR tidak punya field ini
    sama sekali (bukan bug, homepage memang tidak punya "tanggal publish").
    Beberapa situs (mis. liputan6.com) bahkan kadang taruh placeholder
    template YANG BELUM DIRENDER, contoh nyata: `"article:published_time":
    "[publishdate]"` -- BUKAN tanggal valid, akan gagal parse & dilewati di
    sini, TIDAK boleh sampai nyangkut sebagai string mentah ke kolom
    datetime (akan error di level DB kalau dipaksa).

    Return None kalau tidak ada kandidat valid -- JANGAN fallback ke waktu
    scrape kita (`collected_at`), itu bukan waktu kejadian asli dan akan
    bikin timeline menyesatkan (numpuk di jam scraping, bukan jam publish).
    """
    candidates = [
        meta.get("datePublished"),
        meta.get("article:published_time"),
        meta.get("publishedTime"),
        meta.get("uploadDate"),
    ]
    for raw in candidates:
        raw = _first(raw)
        if not raw:
            continue
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            logger.debug("_parse_published_at: nilai bukan tanggal valid, dilewati: %r", raw)
            continue
    return None


async def search_news_by_keyword(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """
    Search artikel berita by keyword — return list mentah
    `[{"title", "description", "url"}, ...]` dari Firecrawl. Snippet-nya
    pendek (~150 karakter), untuk isi LENGKAP panggil scrape_article() per
    URL hasil ini.
    """
    resp = await _call_firecrawl_with_rotation("/search", {"query": query, "limit": max_results}, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data") or []


async def scrape_article(url: str) -> dict[str, Any] | None:
    """
    Ambil isi LENGKAP satu artikel (markdown bersih + metadata) via Firecrawl
    `/v1/scrape`. Return None (BUKAN raise) kalau gagal — dipanggil per-artikel
    dalam batch oleh pemanggil, satu URL gagal tidak boleh menggagalkan semua.
    """
    try:
        resp = await _call_firecrawl_with_rotation("/scrape", {"url": url, "formats": ["markdown"]}, timeout=30)
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
    published_at = _parse_published_at(meta)

    return {
        "url": url,
        "title": title,
        "content": markdown,
        "image_url": image,
        "author": author,
        "published_at": published_at,
        "raw_metadata": meta,
    }
