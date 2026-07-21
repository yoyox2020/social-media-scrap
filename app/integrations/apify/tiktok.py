"""
Apify TikTok — actor `clockworks/tiktok-scraper` (settings.tiktok_actor_id).

SATU actor untuk semuanya (beda dengan Facebook yang butuh 2 actor terpisah):
scrape profil yang sudah diketahui (`profiles`) ATAU search by keyword
(`searchQueries`). Search mode TERBUKTI LIVE mengembalikan bentuk data yang
IDENTIK dengan mode profil (item video + `authorMeta` lengkap) — beda dengan
Facebook yang harus extract akun dari URL post secara manual, di sini tinggal
baca `authorMeta.name` langsung, jauh lebih simpel & akurat.

Bentuk data DIVERIFIKASI LIVE (bukan tebakan) 07 Juli 2026 — lihat
docs/update-fix-tiktok.md untuk detail lengkap:

Post (top-level, per video):
    {
      "id": "...", "text": "caption...", "createTimeISO": "...",
      "authorMeta": {"name": "...", "fans": int, ...},
      "webVideoUrl": "...", "diggCount": int, "shareCount": int,
      "playCount": int, "commentCount": int, "collectCount": int,
      "hashtags": [{"name": "..."}, ...],   # SUDAH terstruktur, tidak perlu regex
      "commentsDatasetUrl": "https://api.apify.com/v2/datasets/<id>/items?...",
    }

Komentar TIDAK inline di item post — ada di dataset TERPISAH per post,
linknya di field `commentsDatasetUrl`. Fungsi di sini otomatis fetch dataset
itu dan taruh hasilnya di key tambahan `_comments` per item post:
    {"videoWebUrl": "...", "cid": "...", "text": "...", "createTimeISO": "...",
     "diggCount": int, "uniqueId": "...", "uid": "..."}
    (CATATAN: tidak ada nama tampilan komentator, cuma uniqueId/uid numerik —
    keterbatasan data dari actor ini, bukan bug di kode kita)
"""
from __future__ import annotations

import logging
import re
from typing import Any

from apify_client import ApifyClient

from app.integrations.apify.rotation import call_apify_actor
from app.shared.config import settings

logger = logging.getLogger(__name__)

_COMMENTS_DATASET_ID_RE = re.compile(r"/datasets/([^/]+)/items")


def _enrich_with_comments(client: ApifyClient, posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fetch komentar per post (dataset terpisah, lihat docstring modul) --
    dipanggil SELAGI client/token yg SAMA masih di scope (lihat
    app/integrations/apify/rotation.py::call_apify_actor `enrich_fn`)."""
    for post in posts:
        comments_url = post.get("commentsDatasetUrl")
        post["_comments"] = []
        if comments_url:
            match = _COMMENTS_DATASET_ID_RE.search(comments_url)
            if match:
                try:
                    post["_comments"] = list(client.dataset(match.group(1)).iterate_items())
                except Exception as exc:
                    logger.warning("gagal fetch comments dataset untuk post=%s: %s", post.get("id"), exc)
    return posts


async def scrape_tiktok_via_apify(
    identifier: str,
    max_posts: int = 5,
    max_comments: int = 10,
) -> list[dict[str, Any]]:
    """
    Scrape post + komentar TikTok untuk satu akun via Apify.

    2026-07-20: pakai call_apify_actor() (pool token + rotasi otomatis,
    SEKALIGUS fix bug lama `run.status`/`run.default_dataset_id` attribute
    access -- lihat instagram.py utk kronologi penemuan bug ini).
    """
    run_input: dict[str, Any] = {
        "profiles": [identifier],
        "resultsPerPage": max_posts,
        "commentsPerPost": max_comments,
        # Semua opsi download media dimatikan — kita cuma butuh teks+metadata,
        # download media cuma nambah biaya tanpa dipakai.
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
        "shouldDownloadAvatars": False,
        "shouldDownloadSlideshowImages": False,
        "shouldDownloadMusicCovers": False,
    }
    logger.info("[Apify] tiktok-scraper (profile) identifier=%s input=%s", identifier, run_input)
    enrich = _enrich_with_comments if max_comments > 0 else None
    return await call_apify_actor(settings.tiktok_actor_id, run_input, enrich_fn=enrich)


async def search_tiktok_by_keyword(query: str, max_results: int = 10) -> list[dict[str, Any]]:
    """
    Search TikTok LANGSUNG by keyword (BUKAN scrape profil yang sudah
    diketahui) — actor yang SAMA dengan scrape_tiktok_via_apify, cuma input
    beda (`searchQueries` alih-alih `profiles`). Hasilnya video yang cocok
    dengan keyword, masing-masing punya `authorMeta.name` — akun ASLI yang
    genuinely bikin konten soal topik itu, bukan tebakan AI.

    2026-07-20: pakai call_apify_actor() (pool token + rotasi otomatis).
    """
    run_input: dict[str, Any] = {
        "searchQueries": [query],
        "resultsPerPage": max_results,
        "commentsPerPost": 0,  # discover cuma butuh akun, tidak perlu komentar (hemat biaya)
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
        "shouldDownloadAvatars": False,
        "shouldDownloadSlideshowImages": False,
        "shouldDownloadMusicCovers": False,
    }
    logger.info("[Apify] tiktok-scraper (search) query=%r input=%s", query, run_input)
    return await call_apify_actor(settings.tiktok_actor_id, run_input)
