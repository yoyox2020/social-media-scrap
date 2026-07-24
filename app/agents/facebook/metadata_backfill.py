"""Backfill follower akun + hitung skor (trend/engagement/freshness/
authority) utk post Facebook (2026-07-24) -- pola SAMA PERSIS dgn
app/agents/instagram/metadata_backfill.py, melengkapi audit "apakah
tiap platform sudah py agent update sendiri" (YouTube=agent_youtube01,
TikTok=agent_tiktok03, Instagram=agent_instagram02, Facebook FILE INI
= agent_facebook05).

Gap NYATA (dicek ke DB sebelum dibangun): 50 post Facebook, 15 author
unik. Sebagian SUDAH py `metadata_.followers` (dari scrape awal
Apify/apify_post_scraper era lama), TAPI TIDAK SEMUA -- file ini
melengkapi yg kosong + hitung ulang skor kalau belum ada.

Follower via SocialCrawl `/v1/facebook/profile?url=...` (BEDA param dari
TikTok/Instagram yg pakai `handle` -- Facebook wajib URL profil penuh,
dikonfirmasi live 2026-07-24: data nyata Mark Zuckerberg 121.000.000
followers). URL dibangun dari `https://www.facebook.com/{author}` (author
sudah berupa username/slug halaman, terverifikasi dari data existing).

KREDIT DIBAGI dgn TikTok(agent_tiktok03)+Instagram(agent_instagram02) --
SocialCrawl 1 akun, 1 pool kredit bersama, BUKAN kuota terpisah per
platform. Formula skor SAMA PERSIS dgn Instagram (log-interaksi, tanpa
views krn Facebook jg tidak expose views publik)."""
from __future__ import annotations

import math
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.posts.models import Post
from app.services.agent_registry.service import get_key_for_agent

SOCIALCRAWL_BASE_URL = "https://www.socialcrawl.dev/v1"
MIN_CREDIT_BUFFER = 5
DEFAULT_AUTHOR_LIMIT = 15


def _compute_scores(likes: int, comments: int, shares: int, followers: int | None, published_at) -> dict:
    now = datetime.now(timezone.utc)
    hours_since = max((now - (published_at or now)).total_seconds() / 3600, 0)
    freshness_score = max(0.0, 100.0 - (hours_since * 2))

    interactions = likes + comments * 2 + shares * 3
    engagement_score = min(100.0, math.log10(interactions + 1) * 30)

    authority_score = min(100.0, math.log10(followers + 1) * 12) if followers else 40.0

    trend_score = round((freshness_score * 0.4) + (engagement_score * 0.35) + (authority_score * 0.25), 2)
    return {
        "trend_score": trend_score,
        "engagement_score": round(engagement_score, 2),
        "freshness_score": round(freshness_score, 2),
        "authority_score": round(authority_score, 2),
    }


async def _get_authors_missing_followers(db: AsyncSession, limit: int) -> list[str]:
    result = await db.execute(
        select(Post.author)
        .where(
            Post.platform == "facebook",
            Post.author.is_not(None),
            Post.author != "",
            Post.metadata_["followers"].astext.is_(None),
        )
        .distinct()
        .limit(limit)
    )
    return [row[0] for row in result.all() if row[0]]


async def backfill_facebook_metadata(db: AsyncSession, api_key: str | None = None, author_limit: int = DEFAULT_AUTHOR_LIMIT) -> dict:
    if not api_key:
        key_info = await get_key_for_agent(db, "agent_facebook05")
        if not key_info or not key_info.get("api_key"):
            return {"error": "agent_facebook05 belum punya key SocialCrawl", "authors_checked": 0}
        api_key = key_info["api_key"]

    authors_to_fetch = await _get_authors_missing_followers(db, author_limit)
    followers_by_author: dict[str, int] = {}
    credits_remaining = None
    stopped_low_credit = False

    async with httpx.AsyncClient(timeout=20.0) as client:
        for author in authors_to_fetch:
            if credits_remaining is not None and credits_remaining < MIN_CREDIT_BUFFER:
                stopped_low_credit = True
                break
            profile_url = f"https://www.facebook.com/{author}"
            try:
                resp = await client.get(
                    f"{SOCIALCRAWL_BASE_URL}/facebook/profile", params={"url": profile_url},
                    headers={"x-api-key": api_key},
                )
            except Exception:
                continue

            remaining_header = resp.headers.get("x-credits-remaining")
            if remaining_header is not None:
                try:
                    credits_remaining = int(remaining_header)
                except ValueError:
                    pass

            if resp.status_code != 200:
                continue
            data = resp.json().get("data", {}).get("author", {})
            followers = data.get("followers")
            if isinstance(followers, int) and followers >= 0:
                followers_by_author[author] = followers

    result = await db.execute(
        select(Post).where(Post.platform == "facebook", Post.metadata_["trend_score"].astext.is_(None))
    )
    posts = result.scalars().all()

    scored = 0
    followers_applied = 0
    for post in posts:
        meta = dict(post.metadata_ or {})
        followers = meta.get("followers")
        if followers is None and post.author in followers_by_author:
            followers = followers_by_author[post.author]
            meta["followers"] = followers
            meta["audience_size"] = followers
            followers_applied += 1

        metrics = post.metrics or {}
        scores = _compute_scores(
            metrics.get("likes", 0), metrics.get("comments", 0), metrics.get("shares", 0),
            followers, post.published_at,
        )
        meta.update(scores)
        post.metadata_ = meta
        scored += 1

    await db.commit()

    return {
        "authors_checked": len(authors_to_fetch),
        "authors_followers_fetched": len(followers_by_author),
        "posts_scored": scored,
        "posts_followers_applied": followers_applied,
        "credits_remaining": credits_remaining,
        "stopped_low_credit": stopped_low_credit,
    }
