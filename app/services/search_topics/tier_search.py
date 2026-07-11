"""
Tier-1 search utk Smart Search (app/api/v1/topic_search.py +
app/services/search_topics/rescan_service.py) -- ILIKE ke `posts.content`
DAN `comments.content` (join `comments.post_id = posts.id`), BUKAN
`Post.keyword_id`.

KENAPA BUKAN keyword_id: cuma pipeline YouTube yang pernah mengisi
`Post.keyword_id` -- semua alur trend_recommendations-driven (Facebook/
Instagram/TikTok/Twitter, baik interaktif maupun daily scrape) menyimpan
post dengan `keyword_id=None`, cuma terhubung lewat `Post.author`/
`trend_recommendations.topic` text. Kalau tier-1 tetap pakai keyword_id,
hasil pencarian akan diam-diam kosong utk hampir semua platform selain
YouTube walau datanya sebenarnya ADA di `posts`. Pola ILIKE ini sama
persis dengan `get_trend_feed()` di app/api/v1/trend_discovery/router.py
yang sudah live-verified sesi ini.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _extract_view_count(meta: dict) -> int:
    raw_views = meta.get("views", meta.get("view_count", 0))
    try:
        return int(str(raw_views).replace(",", "").split()[0]) if raw_views else 0
    except (ValueError, IndexError):
        return 0


async def find_posts_by_keyword(
    db: AsyncSession,
    keyword_text: str,
    platforms: list[str] | None,
    limit: int = 10,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    """Post yang `content`-nya mengandung `keyword_text` (ILIKE substring),
    opsional difilter platform & rentang waktu, terbaru dulu."""
    platform_clause = "AND platform = ANY(:platforms)" if platforms else ""
    since_clause = "AND published_at >= :since" if since else ""
    params: dict = {"pattern": f"%{keyword_text}%", "limit": limit}
    if platforms:
        params["platforms"] = platforms
    if since:
        params["since"] = since

    rows = (await db.execute(text(f"""
        SELECT id, platform, content, author, url, published_at, collected_at, metadata
        FROM posts
        WHERE content ILIKE :pattern
          {platform_clause}
          {since_clause}
        ORDER BY collected_at DESC
        LIMIT :limit
    """), params)).mappings().all()

    results = []
    for p in rows:
        meta = p["metadata"] or {}
        results.append({
            "id": str(p["id"]),
            "platform": p["platform"],
            "title": p["content"],
            "author": p["author"],
            "url": p["url"],
            "view_count": _extract_view_count(meta),
            "likes": meta.get("likes", 0),
            "published_at": p["published_at"].isoformat() if p["published_at"] else None,
            "collected_at": p["collected_at"].isoformat() if p["collected_at"] else None,
            "thumbnail_url": meta.get("thumbnail") or meta.get("photo_url") or meta.get("image_url") or "",
        })
    return results


async def find_comments_by_keyword(
    db: AsyncSession,
    keyword_text: str,
    platforms: list[str] | None,
    limit: int = 10,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    """Comment yang `content`-nya mengandung `keyword_text`, lewat join ke
    `posts` (utk platform + url balik ke post induk, comment sendiri tidak
    punya permalink)."""
    platform_clause = "AND p.platform = ANY(:platforms)" if platforms else ""
    since_clause = "AND c.published_at >= :since" if since else ""
    params: dict = {"pattern": f"%{keyword_text}%", "limit": limit}
    if platforms:
        params["platforms"] = platforms
    if since:
        params["since"] = since

    rows = (await db.execute(text(f"""
        SELECT c.id, p.platform, c.content, c.author, p.url, c.published_at
        FROM comments c
        JOIN posts p ON p.id = c.post_id
        WHERE c.content ILIKE :pattern
          {platform_clause}
          {since_clause}
        ORDER BY c.published_at DESC NULLS LAST
        LIMIT :limit
    """), params)).mappings().all()

    return [
        {
            "id": str(r["id"]),
            "platform": r["platform"],
            "content": r["content"],
            "author": r["author"],
            "url": r["url"],
            "published_at": r["published_at"].isoformat() if r["published_at"] else None,
        }
        for r in rows
    ]


async def get_sentiment_summary_by_keyword(
    db: AsyncSession,
    keyword_text: str,
    platforms: list[str] | None,
) -> dict[str, Any]:
    """Ringkasan sentimen KOMENTAR (`lexicon_analyses`, label Indonesia)
    utk semua post yang cocok `keyword_text` -- "bagaimana reaksi warganet
    ke post-post soal topik ini", bukan cuma komentar yang literally
    mengandung kata itu. Pengganti versi `Post.keyword_id`-based lama di
    topic_search.py (cuma menangkap data YouTube)."""
    platform_clause = "AND p.platform = ANY(:platforms)" if platforms else ""
    params: dict = {"pattern": f"%{keyword_text}%"}
    if platforms:
        params["platforms"] = platforms

    rows = (await db.execute(text(f"""
        SELECT la.label, count(la.id) AS cnt
        FROM lexicon_analyses la
        JOIN comments c ON c.id = la.comment_id
        JOIN posts p ON p.id = c.post_id
        WHERE p.content ILIKE :pattern
          {platform_clause}
        GROUP BY la.label
    """), params)).mappings().all()

    summary = {"positif": 0, "negatif": 0, "netral": 0}
    total = 0
    for r in rows:
        label = r["label"]
        if label in summary:
            summary[label] = r["cnt"]
            total += r["cnt"]

    if total > 0:
        dominant = max(summary, key=summary.get)
        return {
            "total_analyzed": total,
            "positif": {"count": summary["positif"], "pct": round(summary["positif"] * 100 / total, 1)},
            "negatif": {"count": summary["negatif"], "pct": round(summary["negatif"] * 100 / total, 1)},
            "netral":  {"count": summary["netral"],  "pct": round(summary["netral"]  * 100 / total, 1)},
            "dominant": dominant,
        }
    return {"total_analyzed": 0}


async def count_posts_and_comments_by_keyword(
    db: AsyncSession,
    keyword_text: str,
    platforms: list[str] | None,
) -> tuple[int, int]:
    """Hitung total post + komentar yang cocok `keyword_text` (utk dashboard
    list/detail topik) -- lebih murah drpd narik semua baris cuma utk
    dihitung panjangnya."""
    platform_clause = "AND platform = ANY(:platforms)" if platforms else ""
    params: dict = {"pattern": f"%{keyword_text}%"}
    if platforms:
        params["platforms"] = platforms

    total_posts = (await db.execute(text(f"""
        SELECT count(*) FROM posts WHERE content ILIKE :pattern {platform_clause}
    """), params)).scalar() or 0

    comment_platform_clause = "AND p.platform = ANY(:platforms)" if platforms else ""
    total_comments = (await db.execute(text(f"""
        SELECT count(*)
        FROM comments c
        JOIN posts p ON p.id = c.post_id
        WHERE p.content ILIKE :pattern
          {comment_platform_clause}
    """), params)).scalar() or 0

    return total_posts, total_comments
