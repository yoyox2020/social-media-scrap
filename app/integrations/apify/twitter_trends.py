"""
Apify Twitter/X Trends — actor `automation-lab/twitter-trends-scraper`
(settings.twitter_trends_actor_id), BEDA dari app/integrations/apify/twitter.py
(scrape profil/search post) — ini scrape fitur **Trends bawaan X sendiri**,
sinyal trending PALING objektif yang tersedia (bukan tebakan AI, bukan
turunan data kita sendiri) — langsung dari apa yang X sendiri tampilkan
sebagai trending di suatu lokasi.

Diverifikasi LIVE 2026-07-10: input `{"locations": ["ID"], "maxTrendsPerLocation": N,
"getAvailableLocations": false}`, output per trend `{rank, name, tweetVolume,
tweetVolumeAvailable, isHashtag, isPromoted, locationName, countryCode, asOf,
scrapedAt}`. CATATAN: `tweetVolume` HAMPIR SELALU `null` (X sudah membatasi
data volume di API publik sejak beberapa tahun terakhir) — jangan
mengandalkan itu, pakai `rank` sebagai sinyal urutan (rank 1 = paling
trending).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from apify_client import ApifyClient

from app.shared.config import settings
from app.shared.exceptions import ExternalAPIError

logger = logging.getLogger(__name__)


def _run_trends_sync(geo: str, max_results: int) -> list[dict[str, Any]]:
    if not settings.apify_api_token:
        raise ExternalAPIError(service="Apify", message="APIFY_API_TOKEN belum di-set di .env")

    client = ApifyClient(settings.apify_api_token)
    run_input: dict[str, Any] = {
        "locations": [geo],
        "maxTrendsPerLocation": max_results,
        "getAvailableLocations": False,
    }

    logger.info("[Apify] twitter-trends-scraper geo=%s input=%s", geo, run_input)
    run = client.actor(settings.twitter_trends_actor_id).call(run_input=run_input)

    if run.status != "SUCCEEDED":
        raise ExternalAPIError(service="Apify", message=f"Run status={run.status} untuk geo={geo!r}")

    return list(client.dataset(run.default_dataset_id).iterate_items())


async def fetch_twitter_trends(geo: str | None = None, max_results: int | None = None) -> list[dict[str, Any]]:
    """
    Ambil trending topics X/Twitter NATIVE untuk satu lokasi (default
    settings.trends_geo="ID"). Return list mentah — tiap item punya `rank`
    (1=paling trending), `name` (teks trend/hashtag), field lain lihat
    docstring modul.
    """
    geo = geo or settings.trends_geo
    max_results = max_results or settings.trends_max_per_source
    return await asyncio.to_thread(_run_trends_sync, geo, max_results)
