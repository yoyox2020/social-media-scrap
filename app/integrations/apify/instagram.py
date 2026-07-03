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

import asyncio
import logging
from typing import Any

from apify_client import ApifyClient

from app.shared.config import settings
from app.shared.exceptions import ExternalAPIError

logger = logging.getLogger(__name__)


def _run_actor_sync(username: str, latest_posts: int, latest_comments: int) -> list[dict[str, Any]]:
    if not settings.apify_api_token:
        raise ExternalAPIError(service="Apify", message="APIFY_API_TOKEN belum di-set di .env")

    client = ApifyClient(settings.apify_api_token)
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
    run = client.actor(settings.apify_actor_id).call(run_input=run_input)

    if run.status != "SUCCEEDED":
        raise ExternalAPIError(service="Apify", message=f"Run status={run.status} untuk username={username}")

    return list(client.dataset(run.default_dataset_id).iterate_items())


async def scrape_instagram_via_apify(
    username: str,
    latest_posts: int = 1,
    latest_comments: int = 10,
) -> list[dict[str, Any]]:
    """
    Scrape post + komentar Instagram untuk satu username via Apify.
    Berjalan di thread terpisah karena apify_client bersifat sinkron/blocking.
    """
    return await asyncio.to_thread(_run_actor_sync, username, latest_posts, latest_comments)
