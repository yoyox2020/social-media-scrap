"""Child scraper Twitter/X (2026-07-24) -- pola SAMA dgn Threads
(genuinely keyword-search, direct httpx call + rotasi
get_next_available_key, BUKAN curl-target generik krn actor ini butuh
JSON body POST spesifik).

Aktor: Apify `danek/twitter-scraper` -- SEBELUM restructure branch ini
(kode di `main`, commit 15da503/5f33c2b) actor yg SAMA PERNAH live-tested
sukses, field mapping di bawah DIAMBIL LANGSUNG dari situ (bukan tebakan
baru): input search `{"query": "...", "search_type": "Latest", "max_posts": N}`
-- `search_type="Latest"` (BUKAN "Top") krn dibandingkan langsung dulu,
"Top" balikin tweet berumur sampai 5 hari (Twitter bias ke tweet yg
sudah kumpulkan engagement), "Latest" balikin tweet hari yg sama saat
query jalan -- lebih cocok utk discover topik viral HARI INI.

Field per tweet (level top): `tweet_id, text, created_at` (format Twitter
klasik "Tue Jul 07 10:15:23 +0000 2026", BUKAN ISO -- lihat
_parse_twitter_date()), `favorites, retweets, replies, views` (STRING),
`quotes`, `author.{rest_id,name,screen_name,followers_count,blue_verified}`.
Cocok PERSIS dgn 124 post Twitter lama yg sudah ada di DB
(metadata.likes/retweets/quotes/views/comments/followers)."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.third_party_apis.service import get_next_available_key, mark_api_error

ACTOR_URL_TEMPLATE = "https://api.apify.com/v2/acts/danek~twitter-scraper/run-sync-get-dataset-items?token={token}"
DEFAULT_MAX_POSTS = 15
ROTATION_FAILURE_STATUS_CODES = {401, 402, 403, 429}
MAX_ROTATION_ATTEMPTS = 4


def _safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_twitter_date(ts) -> datetime | None:
    """Format asli Twitter: 'Tue Jul 07 10:15:23 +0000 2026' -- format
    klasik Twitter API v1.1, BUKAN ISO."""
    if not ts:
        return None
    try:
        return datetime.strptime(str(ts), "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return None


def _normalize_item(item: dict) -> dict | None:
    tweet_id = item.get("tweet_id")
    if not tweet_id:
        return None
    text = item.get("text") or ""
    author = item.get("author") or {}
    if not isinstance(author, dict):
        author = {}
    screen_name = author.get("screen_name") or item.get("screen_name") or ""
    if not text and not screen_name:
        return None

    return {
        "external_id": str(tweet_id),
        "content": text,
        "author": screen_name,
        "author_followers": _safe_int(author.get("followers_count")),
        "url": f"https://twitter.com/{screen_name}/status/{tweet_id}" if screen_name else "",
        "metrics": {
            "views": _safe_int(item.get("views")),
            "likes": _safe_int(item.get("favorites")),
            "comments": _safe_int(item.get("replies")),
            "shares": _safe_int(item.get("retweets")),
        },
        "quotes": _safe_int(item.get("quotes")),
        "published_at": _parse_twitter_date(item.get("created_at")),
        "raw_data": item,
    }


def _extract_items(response_json) -> list[dict]:
    items = response_json if isinstance(response_json, list) else []
    normalized = [_normalize_item(it) for it in items if isinstance(it, dict)]
    return [n for n in normalized if n is not None]


async def _fetch_keyword(db: AsyncSession, keyword: str, max_posts: int) -> tuple[list[dict], str | None]:
    tried_key_ids: set = set()
    last_error: str | None = None
    body = {"query": keyword, "search_type": "Latest", "max_posts": max_posts}

    for _attempt in range(MAX_ROTATION_ATTEMPTS):
        key_entry = await get_next_available_key(db, "Apify", platform_group="twitter")
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

        return _extract_items(data), None

    return [], last_error or "unknown"


async def fetch_via_keywords(db: AsyncSession, keywords: list[str], max_posts: int = DEFAULT_MAX_POSTS) -> dict:
    all_posts: list[dict] = []
    errors: list[dict] = []
    for kw in keywords:
        posts, error = await _fetch_keyword(db, kw, max_posts)
        if error:
            errors.append({"keyword": kw, "error": error})
        else:
            all_posts.extend(posts)
    return {"success": True, "posts": all_posts, "errors": errors}
