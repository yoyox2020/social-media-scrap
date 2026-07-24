"""Child scraper Threads (2026-07-24) -- EnsembleData `/threads/keyword/search`
(dikonfirmasi live via OpenAPI spec resmi mereka, BUKAN tebakan --
`https://ensembledata.com/apis/openapi.json`, param `name`=keyword,
`sorting`=0 top/1 recent). BEDA dari Instagram (username-only): endpoint
ini genuinely keyword-search, jadi coordinator TikTok/Facebook-style
(distribusi keyword ke child) yg dipakai, BUKAN pola related_accounts
Instagram.

FIELD MAPPING TERVERIFIKASI LIVE (2026-07-24, keyword "bola", token
EnsembleData Pool 2) -- respons SANGAT nested (GraphQL-style Threads
asli): `data[].node.thread.thread_items[].post.{pk, code, caption.text,
user.username, like_count, taken_at (unix), image_versions2.candidates[0].url,
video_versions[0].url, text_post_app_info.{direct_reply_count,
repost_count, quote_count}}` -- field replies/reposts/quotes ini PERSIS
cocok dgn struktur data lama yg SUDAH ada di 60 post Threads di DB
(metadata.replies/reposts/quotes), jadi normalisasi baru ini KOMPATIBEL
dgn data historis, bukan bikin bentuk baru yg beda sendiri.

Follower/audience_size: BELUM ada mekanisme (SocialCrawl tidak py
endpoint Threads, EnsembleData py `/threads/user/info` terpisah TAPI
belum diintegrasikan -- dicatat sbg keterbatasan, authority_score
fallback default 40.0 spt platform lain yg followernya belum diketahui)."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.third_party_apis.service import get_next_available_key, mark_api_error

ENSEMBLEDATA_BASE_URL = "https://ensembledata.com/apis"
ROTATION_FAILURE_STATUS_CODES = {401, 402, 403, 429, 495}  # 495 = "Maximum requests limit reached for today" (verified live)
MAX_ROTATION_ATTEMPTS = 6


def _safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _extract_media(post: dict) -> tuple[list[dict], str | None]:
    media: list[dict] = []
    thumb = None
    candidates = ((post.get("image_versions2") or {}).get("candidates")) or []
    if candidates:
        thumb = candidates[0].get("url")
        media.append({"type": "image", "url": thumb})
    for v in post.get("video_versions") or []:
        if v.get("url"):
            media.append({"type": "video", "url": v["url"]})
            break
    return media, thumb


def _normalize_post(post: dict) -> dict | None:
    pk = post.get("pk")
    code = post.get("code")
    if not pk or not code:
        return None
    user = post.get("user") or {}
    username = user.get("username") or ""
    caption = (post.get("caption") or {}).get("text") or ""
    if not caption and not username:
        return None

    app_info = post.get("text_post_app_info") or {}
    media, thumb = _extract_media(post)

    return {
        "external_id": str(pk),
        "content": caption,
        "author": username,
        "url": f"https://www.threads.net/@{username}/post/{code}",
        "media": media,
        "thumbnail": thumb,
        "metrics": {
            "views": 0,  # Threads tidak expose view count publik
            "likes": _safe_int(post.get("like_count")),
            "comments": _safe_int(app_info.get("direct_reply_count")),
            "shares": _safe_int(app_info.get("repost_count")),
        },
        "quotes": _safe_int(app_info.get("quote_count")),
        "code": code,
        "published_at": _parse_dt(post.get("taken_at")),
        "raw_data": post,
    }


def _extract_items(response_json) -> list[dict]:
    if not isinstance(response_json, dict):
        return []
    data = response_json.get("data")
    if not isinstance(data, list):
        return []

    posts: list[dict] = []
    for node_wrap in data:
        if not isinstance(node_wrap, dict):
            continue
        thread = ((node_wrap.get("node") or {}).get("thread")) or {}
        for item in thread.get("thread_items") or []:
            post = (item or {}).get("post")
            if isinstance(post, dict):
                normalized = _normalize_post(post)
                if normalized:
                    posts.append(normalized)
    return posts


async def _fetch_keyword(db: AsyncSession, keyword: str) -> tuple[list[dict], str | None]:
    tried_key_ids: set = set()
    last_error: str | None = None

    for _attempt in range(MAX_ROTATION_ATTEMPTS):
        key_entry = await get_next_available_key(db, "EnsembleData", platform_group="threads")
        if not key_entry or key_entry.id in tried_key_ids:
            last_error = "Semua token EnsembleData sudah dicoba & gagal -- menunggu jadwal berikutnya"
            break
        tried_key_ids.add(key_entry.id)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"{ENSEMBLEDATA_BASE_URL}/threads/keyword/search",
                    params={"name": keyword, "sorting": 1, "token": key_entry.api_key},
                )
        except Exception as exc:
            await mark_api_error(db, key_entry.id, str(exc)[:500])
            last_error = str(exc)
            continue

        if resp.status_code in ROTATION_FAILURE_STATUS_CODES:
            await mark_api_error(db, key_entry.id, f"HTTP {resp.status_code}: {resp.text[:500]}")
            last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
            continue

        if resp.status_code != 200:
            last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
            break

        try:
            data = resp.json()
        except ValueError:
            last_error = "response bukan JSON valid"
            break

        return _extract_items(data), None

    return [], last_error or "unknown"


async def fetch_via_keywords(db: AsyncSession, keywords: list[str]) -> dict:
    all_posts: list[dict] = []
    errors: list[dict] = []
    for kw in keywords:
        posts, error = await _fetch_keyword(db, kw)
        if error:
            errors.append({"keyword": kw, "error": error})
        else:
            all_posts.extend(posts)
    return {"success": True, "posts": all_posts, "errors": errors}
