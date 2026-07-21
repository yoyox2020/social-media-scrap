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

import logging
from typing import Any

from app.integrations.apify.rotation import call_apify_actor
from app.shared.config import settings

logger = logging.getLogger(__name__)


async def fetch_twitter_trends(geo: str | None = None, max_results: int | None = None) -> list[dict[str, Any]]:
    """
    Ambil trending topics X/Twitter NATIVE untuk satu lokasi (default
    settings.trends_geo="ID"). Return list mentah — tiap item punya `rank`
    (1=paling trending), `name` (teks trend/hashtag), field lain lihat
    docstring modul.

    2026-07-20: pakai call_apify_actor() (pool token + rotasi otomatis,
    SEKALIGUS fix bug lama `run.status`/`run.default_dataset_id` attribute
    access -- lihat instagram.py utk kronologi penemuan bug ini).
    """
    geo = geo or settings.trends_geo
    max_results = max_results or settings.trends_max_per_source
    run_input: dict[str, Any] = {
        "locations": [geo],
        "maxTrendsPerLocation": max_results,
        "getAvailableLocations": False,
    }
    logger.info("[Apify] twitter-trends-scraper geo=%s input=%s", geo, run_input)
    return await call_apify_actor(settings.twitter_trends_actor_id, run_input)
