"""
Apify TikTok — actor `clockworks/tiktok-scraper` (settings.tiktok_actor_id).

SATU actor untuk semuanya (beda dengan Facebook yang butuh 2 actor terpisah):
scrape profil yang sudah diketahui (`profiles`), search by keyword
(`searchQueries`, Fase 2 — belum dipakai di sini), atau by hashtag
(`hashtags`, Fase 2). File ini baru cover scrape-by-profile (Fase 1).

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

import asyncio
import logging
import re
from typing import Any

from apify_client import ApifyClient

from app.shared.config import settings
from app.shared.exceptions import ExternalAPIError

logger = logging.getLogger(__name__)

_COMMENTS_DATASET_ID_RE = re.compile(r"/datasets/([^/]+)/items")


def _run_actor_sync(identifier: str, max_posts: int, max_comments: int) -> list[dict[str, Any]]:
    if not settings.apify_api_token:
        raise ExternalAPIError(service="Apify", message="APIFY_API_TOKEN belum di-set di .env")

    client = ApifyClient(settings.apify_api_token)
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
    run = client.actor(settings.tiktok_actor_id).call(run_input=run_input)

    if run.status != "SUCCEEDED":
        raise ExternalAPIError(service="Apify", message=f"Run status={run.status} untuk identifier={identifier}")

    posts = list(client.dataset(run.default_dataset_id).iterate_items())

    # ── Fetch komentar per post (dataset terpisah, lihat docstring) ────────────
    for post in posts:
        comments_url = post.get("commentsDatasetUrl")
        post["_comments"] = []
        if comments_url and max_comments > 0:
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
    Scrape post + komentar TikTok untuk satu akun via Apify. Berjalan di
    thread terpisah karena apify_client bersifat sinkron/blocking (pola sama
    dengan app/integrations/apify/facebook.py dan instagram.py).
    """
    return await asyncio.to_thread(_run_actor_sync, identifier, max_posts, max_comments)
