"""
Facebook API endpoints.

GET  /facebook/posts?username=X    — scrape + ambil post dari page/profil (maks 10)
GET  /facebook/search?q=keyword    — cari page Facebook berdasarkan keyword
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.services.auth.dependencies import get_current_user
from app.shared.config import settings
from app.shared.utils import build_success_response

router = APIRouter(prefix="/facebook", tags=["facebook"])


# ─────────────────────────────────────────────────────────────────────────────
# GET /facebook/posts
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/posts", response_model=dict, summary="Scrape + ambil post Facebook dari page/username")
async def get_facebook_posts(
    username: str = Query(..., min_length=1, max_length=200, description="Username / Page ID Facebook"),
    max_posts: int = Query(default=10, ge=1, le=10, description="Jumlah post (maks 10)"),
    max_comments: int = Query(default=20, ge=0, le=50, description="Jumlah komentar per post (maks 50)"),
    force_refresh: bool = Query(default=False, description="Paksa scrape ulang"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Scrape post dari Facebook page atau profil, simpan ke DB, analisis sentimen komentar.

    - Jika data sudah ada dan `force_refresh=false` → return dari DB
    - Jika belum ada atau `force_refresh=true` → scrape via Facebook Graph API

    **Yang di-scrape per post:**
    - Pesan/caption, likes, shares, komentar (dengan lexicon sentiment)

    **Response:**
    - `page_info` : info page (nama, followers, kategori, dll)
    - `items`     : list post dengan nested `comments` + `sentiment_summary`
    - `sentiment` : distribusi global sentimen komentar
    """
    identifier = username.strip().lstrip("@")

    existing_count: int = await db.scalar(
        text("SELECT COUNT(*) FROM posts WHERE platform = 'facebook' AND author = :author"),
        {"author": identifier},
    ) or 0

    scrape_result: dict | None = None
    if existing_count == 0 or force_refresh:
        from app.services.facebook.pipeline_service import scrape_facebook_posts
        scrape_result = await scrape_facebook_posts(
            db=db,
            identifier=identifier,
            max_posts=max_posts,
            max_comments=max_comments,
            keyword_id=None,
            access_token=settings.facebook_access_token or None,
        )

    # ── Ambil posts dari DB ───────────────────────────────────────────────────
    rows = (await db.execute(text("""
        SELECT id, external_id, content, author, url, published_at, collected_at, metadata
        FROM posts
        WHERE platform = 'facebook' AND author = :author
        ORDER BY published_at DESC NULLS LAST
        LIMIT :limit
    """), {"author": identifier, "limit": max_posts})).mappings().all()

    page_info: dict = scrape_result.get("page_info", {}) if scrape_result else {"username": identifier}

    # ── Batch-fetch komentar ──────────────────────────────────────────────────
    post_ids = [str(r["id"]) for r in rows]
    comments_by_post: dict[str, list] = {pid: [] for pid in post_ids}
    all_labels: list[str] = []
    total_per_post: dict[str, int] = {}

    if post_ids and max_comments > 0:
        ids_sql = ", ".join(f"'{pid}'" for pid in post_ids)
        cmt_rows = (await db.execute(text(f"""
            SELECT c.id, c.content, c.author, c.post_id::text AS post_id,
                   la.label AS sentiment, la.score
            FROM comments c
            LEFT JOIN lexicon_analyses la ON la.comment_id = c.id
            WHERE c.post_id::text IN ({ids_sql})
            ORDER BY c.created_at DESC
        """))).mappings().all()

        for cr in cmt_rows:
            pid = cr["post_id"]
            total_per_post[pid] = total_per_post.get(pid, 0) + 1
            if cr["sentiment"]:
                all_labels.append(cr["sentiment"])
            bucket = comments_by_post.setdefault(pid, [])
            if len(bucket) < max_comments:
                bucket.append({
                    "id":        str(cr["id"]),
                    "content":   cr["content"],
                    "author":    cr["author"],
                    "sentiment": cr["sentiment"],
                    "score":     round(float(cr["score"]), 3) if cr["score"] is not None else None,
                })

    # ── Build items ───────────────────────────────────────────────────────────
    items = []
    for i, r in enumerate(rows):
        pid = str(r["id"])
        meta = r["metadata"] or {}
        vid_cmts = comments_by_post.get(pid, [])
        vid_lbls = [c["sentiment"] for c in vid_cmts if c["sentiment"]]
        sc = Counter(vid_lbls)
        total_sc = sum(sc.values())

        items.append({
            "rank":          i + 1,
            "post_id":       r["external_id"],
            "url":           r["url"] or f"https://www.facebook.com/{r['external_id']}",
            "message":       r["content"] or "",
            "author":        r["author"],
            "likes":         meta.get("likes", 0),
            "shares":        meta.get("shares", 0),
            "comment_count": total_per_post.get(pid, meta.get("comments", 0)),
            "thumbnail":     meta.get("thumbnail", ""),
            "published_at":  r["published_at"].isoformat() if r["published_at"] else None,
            "collected_at":  r["collected_at"].isoformat() if r["collected_at"] else None,
            "sentiment_summary": {
                lbl: {
                    "count":      sc.get(lbl, 0),
                    "percentage": round(sc.get(lbl, 0) / total_sc * 100, 1) if total_sc else 0.0,
                }
                for lbl in ["positif", "negatif", "netral"]
            },
            "comments": vid_cmts,
        })

    # ── Sentimen global ───────────────────────────────────────────────────────
    counter = Counter(all_labels)
    total_analyzed = sum(counter.values())
    total_cmts = sum(total_per_post.values())
    sentiment_dist = {
        lbl: {
            "count":      counter.get(lbl, 0),
            "percentage": round(counter.get(lbl, 0) / total_analyzed * 100, 1) if total_analyzed else 0.0,
        }
        for lbl in ["positif", "negatif", "netral"]
    }

    return build_success_response({
        "platform":  "facebook",
        "username":  identifier,
        "scrape":    {
            "posts_scraped": scrape_result.get("posts_scraped", 0) if scrape_result else 0,
            "posts_new":     scrape_result.get("posts_saved", 0) if scrape_result else 0,
            "errors":        scrape_result.get("errors", []) if scrape_result else [],
        } if scrape_result else None,
        "page_info": page_info,
        "stats": {
            "total_posts":    len(items),
            "total_comments": total_cmts,
            "total_analyzed": total_analyzed,
            "coverage_pct":   round(total_analyzed / total_cmts * 100, 1) if total_cmts else 0.0,
        },
        "sentiment": {
            **sentiment_dist,
            "dominant":       counter.most_common(1)[0][0] if counter else "netral",
            "total_analyzed": total_analyzed,
        },
        "items": items,
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /facebook/search
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/search", response_model=dict, summary="Cari page Facebook berdasarkan keyword")
async def search_facebook_pages(
    q: str = Query(..., min_length=1, max_length=200, description="Keyword pencarian"),
    limit: int = Query(default=10, ge=1, le=25, description="Jumlah hasil (maks 25)"),
    current_user: User = Depends(get_current_user),
):
    """
    Cari Facebook Pages berdasarkan keyword menggunakan Graph API search.

    Mengembalikan daftar page dengan: id, nama, kategori, jumlah fans, dan link.
    """
    from app.integrations.facebook.connector import FacebookConnector
    connector = FacebookConnector(settings.facebook_access_token)

    try:
        raw = await connector.search_pages(q.strip(), limit=limit)
        pages = raw.get("data", [])
    except Exception as exc:
        return build_success_response({
            "query":   q,
            "results": [],
            "error":   str(exc),
        })

    results = []
    for p in pages:
        pic = (p.get("picture") or {}).get("data", {})
        results.append({
            "page_id":  p.get("id", ""),
            "name":     p.get("name", ""),
            "category": p.get("category", ""),
            "fans":     p.get("fan_count", 0),
            "link":     p.get("link", ""),
            "picture":  pic.get("url", ""),
        })

    return build_success_response({
        "query":   q,
        "count":   len(results),
        "results": results,
    })
