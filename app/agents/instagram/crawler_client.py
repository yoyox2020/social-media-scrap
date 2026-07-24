"""Child scraper Instagram (2026-07-24) -- BEDA STRUKTUR dari
Facebook/TikTok/YouTube: actor `apify/instagram-post-scraper` (41,6 JUTA
run, dikonfirmasi live 2026-07-24) SCRAPE PER-USERNAME/PROFIL, BUKAN
keyword/hashtag search (`{"username": [...], "resultsLimit": N}`,
dikonfirmasi lewat Apify build API, `username` WAJIB array) -- jadi
TIDAK dipakai lewat sistem curl-target generik {{KEYWORD}} spt platform
lain (topik "jokowi terbaru"/"jokowi trending" bukan username valid).

Sumber username: `trend_recommendations.related_accounts`
(`[{"platform":"instagram","username":"..."}]`, SUDAH ADA di skema
lama, dikonfirmasi ada isinya nyata di DB) -- topik yg py akun Instagram
terdaftar di situ dipakai LANGSUNG; topik yg TIDAK py entry sama sekali
fallback coba topik itu sendiri sbg username (best-effort, kadang
cocok kadang tidak, SAMA PERSIS perilaku sistem lama sblm restructure).

FIELD MAPPING TERVERIFIKASI LIVE (bukan tebakan -- diambil dari riwayat
project yg PERNAH benar2 dites end-to-end thd actor yg SAMA, akun
`coldplay`, lihat [[reference_instagram_post_scraper_actor]]):
level post: `shortCode, url, caption, timestamp, likesCount,
commentsCount, displayUrl, ownerUsername, ownerFullName`. Komentar
nested `latestComments[]`: `{id, text, ownerUsername, timestamp,
likesCount, repliesCount}` -- KETERBATASAN TERUKUR (dicatat di memori
lama): cuma ~14-15 komentar/post BERAPAPUN resultsLimit (parameter itu
ngatur JUMLAH POST, bukan komentar per post)."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trend_recommendations.models import TrendRecommendation
from app.services.third_party_apis.service import get_next_available_key, mark_api_error

ACTOR_URL_TEMPLATE = "https://api.apify.com/v2/acts/apify~instagram-post-scraper/run-sync-get-dataset-items?token={token}"
DEFAULT_RESULTS_PER_PROFILE = 10
ROTATION_FAILURE_STATUS_CODES = {401, 402, 403, 429}
MAX_ROTATION_ATTEMPTS = 4


def _safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_dt(value) -> datetime | None:
    """Actor ini balikin `timestamp` ISO string (bukan unix epoch) --
    dikonfirmasi dari field mapping terverifikasi di docstring modul."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


async def _get_related_instagram_usernames(db: AsyncSession, topic: str) -> list[str]:
    row = await db.scalar(
        select(TrendRecommendation)
        .where(TrendRecommendation.topic == topic)
        .order_by(TrendRecommendation.recommendation_date.desc())
        .limit(1)
    )
    if not row or not row.related_accounts:
        return []
    usernames = [
        acc.get("username") for acc in row.related_accounts
        if isinstance(acc, dict) and acc.get("platform") == "instagram" and acc.get("username")
    ]
    return usernames


def _normalize_item(item: dict) -> dict | None:
    short_code = item.get("shortCode")
    owner = item.get("ownerUsername") or ""
    if not short_code or not (item.get("caption") or owner):
        return None

    comments_raw = []
    for c in item.get("latestComments") or []:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        comments_raw.append(c)

    return {
        "external_id": short_code,
        "content": item.get("caption") or "",
        "author": owner,
        "author_full_name": item.get("ownerFullName") or "",
        "url": item.get("url") or f"https://www.instagram.com/p/{short_code}/",
        "thumbnail": item.get("displayUrl"),
        "metrics": {
            "views": 0,  # actor ini tidak expose view count publik utk post foto
            "likes": _safe_int(item.get("likesCount")),
            "comments": _safe_int(item.get("commentsCount")),
            "shares": 0,
        },
        "published_at": _parse_dt(item.get("timestamp")),
        "comments_raw": comments_raw,
        "raw_data": item,
    }


async def fetch_posts_for_topic(db: AsyncSession, topic: str, results_per_profile: int = DEFAULT_RESULTS_PER_PROFILE) -> dict:
    usernames = await _get_related_instagram_usernames(db, topic)
    used_fallback = False
    if not usernames:
        usernames = [topic]
        used_fallback = True

    body = {"username": usernames, "resultsLimit": results_per_profile}
    tried_key_ids: set = set()
    last_error: str | None = None

    for _attempt in range(MAX_ROTATION_ATTEMPTS):
        key_entry = await get_next_available_key(db, "Apify")
        if not key_entry or key_entry.id in tried_key_ids:
            last_error = "Semua token Apify sudah dicoba & gagal -- menunggu jadwal berikutnya"
            break
        tried_key_ids.add(key_entry.id)

        url = ACTOR_URL_TEMPLATE.format(token=key_entry.api_key)
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(url, json=body)
        except Exception as exc:
            await mark_api_error(db, key_entry.id, str(exc)[:500])
            last_error = str(exc)
            continue

        if resp.status_code in ROTATION_FAILURE_STATUS_CODES:
            await mark_api_error(db, key_entry.id, f"HTTP {resp.status_code}: {resp.text[:500]}")
            last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
            continue

        if resp.status_code not in (200, 201):
            last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
            break

        try:
            data = resp.json()
        except ValueError:
            last_error = "response bukan JSON valid"
            break

        items = data if isinstance(data, list) else []
        normalized = [_normalize_item(it) for it in items if isinstance(it, dict)]
        posts = [n for n in normalized if n is not None]
        return {"success": True, "posts": posts, "usernames_used": usernames, "used_fallback_topic_as_username": used_fallback, "error": None}

    return {"success": False, "posts": [], "usernames_used": usernames, "used_fallback_topic_as_username": used_fallback, "error": last_error or "unknown"}
