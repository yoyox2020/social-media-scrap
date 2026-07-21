"""
Apify Instagram scraper — pengganti EnsembleData untuk scraping Instagram.

Pakai Actor `ycQuEFDDZmgX7BAsL` ("social-media-sentiment-analysis-tool").
Detail metode & gotcha: docs/apify-instagram-method.md

Bentuk hasil (per baris = satu pasangan post+comment):
    {
      "postUrl": "...", "postDescription": "...", "postTimestamp": "...",
      "postLikesCount": int, "postCommentsCount": int,
      "commentText": "...", "commentAuthor": "...", "commentTimestamp": "...",
      "profileFollowers": int, ...
    }

Gotcha penting (lihat docs): `latestComments` harus > 0, kalau tidak Actor
tidak menghasilkan baris output sama sekali walau post berhasil di-fetch.
"""
from __future__ import annotations

import logging
from typing import Any

from app.integrations.apify.rotation import call_apify_actor
from app.shared.config import settings

logger = logging.getLogger(__name__)


async def scrape_instagram_via_apify(
    username: str,
    latest_posts: int = 1,
    latest_comments: int = 10,
) -> list[dict[str, Any]]:
    """
    Scrape post + komentar Instagram untuk satu username via Apify.

    2026-07-20: DIPINDAH ke call_apify_actor() (pool token + rotasi
    otomatis, lihat app/integrations/apify/rotation.py) -- SEKALIGUS
    memperbaiki bug lama `run.status`/`run.default_dataset_id` (attribute
    access) yang crash "'dict' object has no attribute 'status'" krn
    apify_client versi sekarang balikin dict, bukan object (ketahuan dari
    error produksi run search:sby 2026-07-18).
    """
    run_input = {
        "instagramProfileName": username,
        "scrapeFacebook": False,
        "scrapeInstagram": True,
        "scrapeTiktok": False,
        "sentimentAnalysis": False,  # sentimen dianalisis sendiri via lexicon, bukan bawaan Apify
        "latestPosts": latest_posts,
        "latestComments": max(latest_comments, 1),
    }
    logger.info("[Apify] run actor username=%s input=%s", username, run_input)
    return await call_apify_actor(settings.apify_actor_id, run_input)
