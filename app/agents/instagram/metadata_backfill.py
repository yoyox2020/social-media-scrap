"""Backfill follower akun + hitung skor (trend/engagement/freshness/
authority) utk post Instagram (2026-07-24, lanjutan permintaan user
"buatkan api instagram... lalu update data metadatanya").

Gap NYATA (dicek ke DB sebelum dibangun): 106 post Instagram, 65 author
unik, 0 py follower TERSIMPAN dan 0 py trend_score (belum pernah
dihitung sama sekali -- beda dari YouTube/TikTok yg emang py agent
struktur-data, Instagram di branch ini belum py pipeline apapun).
`should_have_comments_but_none` = 0 (dicek: kapanpun metrics.comments>0,
komentar SUDAH tersimpan) -- TIDAK ada gap komentar utk dibackfill
sekarang, jadi file ini CUMA follower+skor (bukan komentar).

Follower via SocialCrawl (`/v1/instagram/profile`, verified live
2026-07-24: data nyata @cristiano 677.873.612 followers). Agent
penanggung jawab: `agent_instagram02` (ditugaskan 2026-07-24, permintaan
user "satu agen lagi utk update metadata instagram") -- key-nya SALINAN
persis dari agent_youtube05/TikTok follower backfill (SocialCrawl cuma
1 akun asli, third_party_apis SENGAJA 1 API = 1 agent eksklusif jadi
tidak bisa di-link ke 2 agent sekaligus -- disimpan sbg custom_api_key
langsung di agent_instagram02 sbg gantinya). KREDIT TETAP DIBAGI dgn
TikTok (budget SocialCrawl sama, bukan 2 kuota terpisah) -- limit
per-run KECIL+jadwal jarang, sama pola dgn
app/agents/tiktok/socialcrawl_follower_backfill.py.

Skor dihitung TANPA views (Instagram scraper ini tidak expose views
publik utk post foto) -- formula log-interaksi SAMA PERSIS dgn
app/agents/facebook/struktur_data.py (bukan dobel-tulis, disalin krn
Python tidak punya cara import lintas-platform yg elegan di sini tanpa
bikin modul shared baru -- dicatat sbg technical debt kecil kalau nanti
ada platform ke-3 yg butuh formula sama)."""
from __future__ import annotations

import math
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.posts.models import Post
from app.services.agent_registry.service import get_key_for_agent
from app.services.third_party_apis.service import get_next_available_key

SOCIALCRAWL_BASE_URL = "https://www.socialcrawl.dev/v1"
MIN_CREDIT_BUFFER = 5
DEFAULT_AUTHOR_LIMIT = 15  # kecil -- kredit dibagi dgn TikTok backfill


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
            Post.platform == "instagram",
            Post.author.is_not(None),
            Post.author != "",
            Post.metadata_["followers"].astext.is_(None),
        )
        .distinct()
        .limit(limit)
    )
    return [row[0] for row in result.all() if row[0]]


async def backfill_instagram_metadata(db: AsyncSession, api_key: str | None = None, author_limit: int = DEFAULT_AUTHOR_LIMIT) -> dict:
    if not api_key:
        # Rotasi grup platform DULU (2026-07-24, "setiap platform py 1
        # group... rotasi otomatis") -- kalau user nambah akun SocialCrawl
        # BARU yg di-tag platform_group="instagram", otomatis kepakai di
        # sini tanpa kode baru. Fallback ke key lama di agent_instagram02
        # (custom_api_key, BUKAN di katalog rotasi) kalau grup kosong --
        # jaga kompatibilitas dgn setup SEBELUM fitur grup ini ada.
        key_entry = await get_next_available_key(db, "SocialCrawl", platform_group="instagram")
        if key_entry and key_entry.api_key:
            api_key = key_entry.api_key
        else:
            key_info = await get_key_for_agent(db, "agent_instagram02")
            if not key_info or not key_info.get("api_key"):
                return {"error": "Tidak ada key SocialCrawl tersedia (grup 'instagram' kosong & agent_instagram02 jg belum punya)", "authors_checked": 0}
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
            try:
                resp = await client.get(
                    f"{SOCIALCRAWL_BASE_URL}/instagram/profile", params={"handle": author},
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

    # Skor dihitung utk SEMUA post yg belum py trend_score (bukan cuma
    # yg baru dibackfill follower-nya di run ini) -- post yg author-nya
    # sudah py followers dari run SEBELUMNYA tetap ikut dihitung skornya
    # sekarang kalau belum pernah, tanpa perlu panggil SocialCrawl lagi.
    result = await db.execute(
        select(Post).where(Post.platform == "instagram", Post.metadata_["trend_score"].astext.is_(None))
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
