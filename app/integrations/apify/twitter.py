"""
Apify Twitter/X — actor `danek/twitter-scraper` (settings.twitter_actor_id).

SATU actor untuk profil scrape, search by keyword, DAN balasan/reply (mode
"responses", dipicu dengan memberi `post_id`) — beda actor call per mode,
field input WAJIB berbeda tiap mode:
  - Profil  : {"username": "...", "max_posts": N}
  - Search  : {"query": "...", "search_type": "Top", "max_posts": N}
  - Balasan : {"post_id": "<tweet_id>", "max_posts": N}

Bentuk data DIVERIFIKASI LIVE (bukan tebakan) 08 Juli 2026:

Post (top-level, per tweet):
    {
      "tweet_id": "...", "text": "...",
      "created_at": "Tue Jul 07 10:15:23 +0000 2026",   # format custom Twitter
                                                          # (BUKAN ISO), lihat
                                                          # _parse_twitter_date()
                                                          # di pipeline_service.py
      "favorites": int, "retweets": int, "replies": int, "views": "50" (STRING),
      "quotes": int, "conversation_id": "...", "lang": "...",
      "author": {"rest_id": "...", "name": "...", "screen_name": "...",
                 "followers_count": int, "blue_verified": bool},
      "entities": [...],   # struktur hashtag BELUM terkonfirmasi (kosong di
                           # semua sampel) -> hashtag diambil via regex dari
                           # `text`, BUKAN dari entities (lihat pipeline_service.py)
      "media": {...},
    }

Balasan (top-level, per reply, dari mode "responses"):
    {
      "id": "...", "text": "...", "display_text": "...",
      "created_at": "...",  # format sama dengan post
      "likes": int, "retweets": int, "replies": int, "views": "50",
      "conversation_id": "...",
      "author": {"rest_id": "...", "name": "...", "screen_name": "...", ...},
      "entities": {"user_mentions": [...]},
    }
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from apify_client import ApifyClient

from app.shared.config import settings
from app.shared.exceptions import ExternalAPIError

logger = logging.getLogger(__name__)


def _fetch_replies_sync(client: ApifyClient, tweet_id: str | None, max_comments: int) -> list[dict[str, Any]]:
    """Balasan/reply TIDAK inline di item post — perlu actor call TERPISAH per
    tweet (mode "responses", dipicu dengan `post_id`), beda dengan TikTok yang
    cukup 1 dataset URL tambahan. Biaya bertambah per tweet yang diambil
    balasannya — dibatasi lewat max_comments (0 = skip sepenuhnya)."""
    if not tweet_id:
        return []
    try:
        run_input = {"post_id": tweet_id, "max_posts": max_comments}
        run = client.actor(settings.twitter_actor_id).call(run_input=run_input)
        if run.status != "SUCCEEDED":
            return []
        return list(client.dataset(run.default_dataset_id).iterate_items())
    except Exception as exc:
        logger.warning("gagal fetch balasan untuk tweet_id=%s: %s", tweet_id, exc)
        return []


def _run_profile_sync(identifier: str, max_posts: int, max_comments: int) -> list[dict[str, Any]]:
    if not settings.apify_api_token:
        raise ExternalAPIError(service="Apify", message="APIFY_API_TOKEN belum di-set di .env")

    client = ApifyClient(settings.apify_api_token)
    run_input: dict[str, Any] = {"username": identifier, "max_posts": max_posts}

    logger.info("[Apify] twitter-scraper (profile) identifier=%s input=%s", identifier, run_input)
    run = client.actor(settings.twitter_actor_id).call(run_input=run_input)

    if run.status != "SUCCEEDED":
        raise ExternalAPIError(service="Apify", message=f"Run status={run.status} untuk identifier={identifier}")

    posts = list(client.dataset(run.default_dataset_id).iterate_items())

    # CATATAN PENTING (ditemukan live 08 Juli 2026): `max_posts` di actor ini
    # TERBUKTI cuma target lunak, BUKAN batas keras — request max_posts=2
    # tetap mengembalikan 21 tweet (kemungkinan actor scroll per-halaman
    # timeline Twitter, ~20 tweet/halaman, dan tidak memotong within-batch).
    # Dipotong manual di sini SEBELUM loop fetch balasan supaya tidak
    # memicu actor call balasan (biaya tambahan) untuk tweet yang tidak diminta.
    posts = posts[:max_posts]

    for post in posts:
        post["_replies"] = (
            _fetch_replies_sync(client, post.get("tweet_id"), max_comments) if max_comments > 0 else []
        )

    return posts


async def scrape_twitter_via_apify(
    identifier: str,
    max_posts: int = 5,
    max_comments: int = 10,
) -> list[dict[str, Any]]:
    """
    Scrape tweet + balasan Twitter/X untuk satu akun via Apify. Berjalan di
    thread terpisah karena apify_client bersifat sinkron/blocking (pola sama
    dengan app/integrations/apify/facebook.py dan tiktok.py).
    """
    return await asyncio.to_thread(_run_profile_sync, identifier, max_posts, max_comments)


def _run_search_sync(query: str, max_results: int, search_type: str) -> list[dict[str, Any]]:
    if not settings.apify_api_token:
        raise ExternalAPIError(service="Apify", message="APIFY_API_TOKEN belum di-set di .env")

    client = ApifyClient(settings.apify_api_token)
    run_input: dict[str, Any] = {"query": query, "search_type": search_type, "max_posts": max_results}

    logger.info("[Apify] twitter-scraper (search) query=%r input=%s", query, run_input)
    run = client.actor(settings.twitter_actor_id).call(run_input=run_input)

    if run.status != "SUCCEEDED":
        raise ExternalAPIError(service="Apify", message=f"Run status={run.status} untuk query={query!r}")

    return list(client.dataset(run.default_dataset_id).iterate_items())


async def search_twitter_by_keyword(query: str, max_results: int = 10, search_type: str = "Latest") -> list[dict[str, Any]]:
    """
    Search Twitter/X LANGSUNG by keyword (BUKAN scrape profil yang sudah
    diketahui) — actor yang SAMA dengan scrape_twitter_via_apify, input beda
    (`query`+`search_type` alih-alih `username`). Hasilnya tweet yang cocok
    dengan keyword, masing-masing punya `author.screen_name` — akun ASLI

    CATATAN PENTING (diverifikasi live 08 Juli 2026): default `search_type`
    SENGAJA "Latest", BUKAN "Top" — dibandingkan langsung untuk query yang
    SAMA, "Top" mengembalikan tweet berumur sampai 5 hari (algoritma Twitter
    bias ke tweet yang SUDAH sempat mengumpulkan engagement), sedangkan
    "Latest" mengembalikan tweet dari hari yang sama saat query dijalankan.
    Karena tujuan discover di sini adalah topik viral HARI INI, "Latest"
    lebih sesuai — trade-off: tweet yang sangat baru wajar punya engagement
    rendah (belum sempat viral), jadi caller (discover_twitter_topic_by_keyword)
    mengurutkan hasil berdasarkan engagement SETELAH fetch untuk tetap
    memprioritaskan yang relatif lebih ramai di antara tweet hari ini.

    Tidak fetch balasan (discover cuma butuh akun, hemat biaya — sama pola
    dengan TikTok).
    """
    return await asyncio.to_thread(_run_search_sync, query, max_results, search_type)
