"""
Apify Facebook scraper — sub-actor `apify/facebook-posts-scraper` +
`apify/facebook-comments-scraper`, dipanggil lewat Actor wrapper yang sama
dengan Instagram (`ycQuEFDDZmgX7BAsL`). Lihat docs/apify-instagram-method.md
dan scripts/apify_facebook_test.py untuk gotcha input schema (sama persis
dengan Instagram: `latestComments` harus > 0, field opsional jangan `None`).

Bentuk hasil (per baris = satu pasangan post+comment) IDENTIK dengan
Instagram, cuma `targetPlatform='facebook'`:
    {
      "postUrl": "...", "postDescription": "...", "postTimestamp": "...",
      "postLikesCount": int, "postCommentsCount": int,
      "commentText": "...", "commentAuthor": "...", "commentTimestamp": "...",
      "profileFollowers": int, ...
    }

Diverifikasi live 05 Juli 2026: berhasil scrape page publik
(`pratiwinoviyanthireal`) yang BUKAN dikelola sendiri — beda dengan Meta
Graph API resmi yang diblokir untuk page di luar milik sendiri (lihat
docs/flow scrape/flow-scrap-facebook.md).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from apify_client import ApifyClient

from app.shared.config import settings
from app.shared.exceptions import ExternalAPIError

logger = logging.getLogger(__name__)


def _run_actor_sync(identifier: str, latest_posts: int, latest_comments: int) -> list[dict[str, Any]]:
    if not settings.apify_api_token:
        raise ExternalAPIError(service="Apify", message="APIFY_API_TOKEN belum di-set di .env")

    client = ApifyClient(settings.apify_api_token)
    run_input = {
        "facebookProfileName": identifier,
        "scrapeFacebook": True,
        "scrapeInstagram": False,
        "scrapeTiktok": False,
        "sentimentAnalysis": False,
        "latestPosts": latest_posts,
        "latestComments": max(latest_comments, 1),
    }

    logger.info("[Apify] run actor (facebook) identifier=%s input=%s", identifier, run_input)
    run = client.actor(settings.apify_actor_id).call(run_input=run_input)

    if run.status != "SUCCEEDED":
        raise ExternalAPIError(service="Apify", message=f"Run status={run.status} untuk identifier={identifier}")

    return list(client.dataset(run.default_dataset_id).iterate_items())


async def scrape_facebook_via_apify(
    identifier: str,
    latest_posts: int = 1,
    latest_comments: int = 10,
) -> list[dict[str, Any]]:
    """
    Scrape post + komentar Facebook untuk satu page/profile via Apify.
    Berjalan di thread terpisah karena apify_client bersifat sinkron/blocking.
    """
    return await asyncio.to_thread(_run_actor_sync, identifier, latest_posts, latest_comments)
