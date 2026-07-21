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

import logging
from typing import Any

from app.integrations.apify.rotation import call_apify_actor
from app.shared.config import settings

logger = logging.getLogger(__name__)


async def scrape_facebook_via_apify(
    identifier: str,
    latest_posts: int = 1,
    latest_comments: int = 10,
) -> list[dict[str, Any]]:
    """
    Scrape post + komentar Facebook untuk satu page/profile via Apify.

    2026-07-20: pakai call_apify_actor() (pool token + rotasi otomatis,
    SEKALIGUS fix bug lama `run.status`/`run.default_dataset_id` attribute
    access -- lihat instagram.py utk kronologi penemuan bug ini).
    """
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
    return await call_apify_actor(settings.apify_actor_id, run_input)
