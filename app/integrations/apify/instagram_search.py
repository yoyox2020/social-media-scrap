"""
Apify Instagram SEARCH by keyword — actor `apify/instagram-hashtag-scraper`
(settings.instagram_search_actor_id), BEDA dari app/integrations/apify/instagram.py
yang cuma bisa scrape profil yang SUDAH diketahui usernamenya. Actor ini
genuinely search Instagram by kata kunci (mode `keywordSearch=True`),
hasilnya POST NYATA langsung — BEDA arsitektur dari Facebook
(app/integrations/apify/facebook_search.py, cari akun dulu baru scrape
akunnya): actor Instagram ini tidak butuh langkah "temukan akun" sama
sekali, konten sudah lengkap dari satu panggilan.

Dipanggil oleh app/api/v1/instagram/router.py (GET /instagram/posts/search
tingkat 3) via app/services/instagram/pipeline_service.py:
save_instagram_keyword_search_results().

Diverifikasi LIVE 2026-07-09 (2x panggilan nyata via server produksi, lihat
docs/analisa-gap-instagram.md bagian C) — bentuk input/output DI BAWAH ini
hasil observasi nyata, bukan cuma baca dokumentasi Apify:

Input: {"hashtags": [keyword], "resultsType": "posts", "resultsLimit": N,
"keywordSearch": True}

Output per item (field yang dipakai kode ini):
  id, shortCode, caption, url, ownerUsername, likesCount, commentsCount,
  timestamp (ISO8601 diakhiri "Z"), hashtags (list of str), displayUrl,
  firstComment (string tunggal, SELALU ada meski string kosong),
  latestComments (list — BELUM terverifikasi shape isinya: 2x test live
  selalu kosong "[]", kemungkinan karena akun Apify di server ini FREE
  plan, "dibatasi first page of results". extract_comments() di bawah
  DEFENSIF terhadap ini — coba beberapa nama field umum, fallback ke
  firstComment, TIDAK PERNAH crash kalau shape beda dari dugaan).

Harga: pay-per-event ~$2.60/1000 hasil (Juli 2026, cek pricing terbaru di
apify.com/apify/instagram-hashtag-scraper).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from apify_client import ApifyClient

from app.shared.config import settings
from app.shared.exceptions import ExternalAPIError

logger = logging.getLogger(__name__)


def _run_search_sync(keyword: str, max_results: int) -> list[dict[str, Any]]:
    if not settings.apify_api_token:
        raise ExternalAPIError(service="Apify", message="APIFY_API_TOKEN belum di-set di .env")

    client = ApifyClient(settings.apify_api_token)
    run_input: dict[str, Any] = {
        "hashtags": [keyword],
        "resultsType": "posts",
        "resultsLimit": max_results,
        "keywordSearch": True,
    }

    logger.info("[Apify] instagram-hashtag-scraper keyword=%r input=%s", keyword, run_input)
    run = client.actor(settings.instagram_search_actor_id).call(run_input=run_input)

    if run.status != "SUCCEEDED":
        raise ExternalAPIError(service="Apify", message=f"Run status={run.status} untuk keyword={keyword!r}")

    return list(client.dataset(run.default_dataset_id).iterate_items())


async def search_instagram_posts_by_keyword(keyword: str, max_results: int = 5) -> list[dict[str, Any]]:
    """
    Search Instagram LANGSUNG by keyword (bukan hashtag yang harus persis
    ada, `keywordSearch=True` di actor menangani ini) — return list post
    mentah dari Apify, sudah termasuk caption/author/likes/comments
    count/timestamp/hashtag terstruktur.
    """
    return await asyncio.to_thread(_run_search_sync, keyword, max_results)


def extract_comments(item: dict[str, Any]) -> list[dict[str, str]]:
    """
    Ambil komentar dari satu item post — BEST-EFFORT, defensif terhadap
    shape `latestComments` yang belum terverifikasi (lihat docstring modul).
    Coba beberapa nama field umum per entri; entri yang tidak dikenali
    dilewati (bukan bikin exception). Fallback ke `firstComment` (string
    tunggal, SUDAH terverifikasi live) kalau `latestComments` kosong/semua
    entrinya tidak bisa diparse.
    """
    comments: list[dict[str, str]] = []
    for raw in item.get("latestComments") or []:
        if not isinstance(raw, dict):
            continue
        text = raw.get("text") or raw.get("comment") or raw.get("content") or ""
        author = raw.get("ownerUsername") or raw.get("username") or raw.get("owner") or ""
        if text:
            comments.append({"text": text, "author": author})

    if not comments and item.get("firstComment"):
        comments.append({"text": item["firstComment"], "author": ""})

    return comments
