"""
Instagram API endpoints.

GET  /instagram/profile — profil + recent posts dari username (Instagram internal API)
GET  /instagram/posts   — scrape + ambil post dari username (maks 10)
"""
from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.services.auth.dependencies import get_current_user
from app.shared.utils import build_success_response

router = APIRouter(prefix="/instagram", tags=["instagram"])

_IG_HEADERS = {
    "User-Agent": "Instagram 219.0.0.12.117 Android (26/8.0.0; 480dpi; 1080x1920; OnePlus; ONEPLUS A3010; OnePlus3T; qcom; id_ID; 314665256)",
    "x-ig-app-id": "936619743392459",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
    "Accept": "*/*",
}


# ─────────────────────────────────────────────────────────────────────────────
# GET /instagram/profile  (public, no EnsembleData)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/profile", response_model=dict, summary="Profil Instagram dari username (tanpa EnsembleData)")
async def get_instagram_profile(
    username: str = Query(..., min_length=1, max_length=100, description="Username Instagram (tanpa @)"),
    current_user: User = Depends(get_current_user),
):
    """
    Ambil profil Instagram berdasarkan username menggunakan Instagram internal API.

    Tidak memerlukan EnsembleData — langsung ke Instagram.

    **Response:**
    - `profile` : info lengkap (followers, bio, verified, post_count, dll)
    - `recent_posts` : list post terbaru (thumbnail, likes, comments)
    """
    username = username.strip().lstrip("@").lower()

    try:
        async with httpx.AsyncClient(headers=_IG_HEADERS, timeout=20, follow_redirects=True) as client:
            r = await client.get(
                "https://i.instagram.com/api/v1/users/web_profile_info/",
                params={"username": username},
            )
            if r.status_code == 404:
                raise HTTPException(status_code=404, detail=f"Username @{username} tidak ditemukan")
            if r.status_code == 429:
                raise HTTPException(status_code=429, detail="Instagram rate limit — coba lagi sebentar")
            if r.status_code != 200:
                raise HTTPException(status_code=502, detail=f"Instagram error: HTTP {r.status_code}")
            data = r.json()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gagal menghubungi Instagram: {exc}")

    user = data.get("data", {}).get("user") or {}
    if not user:
        raise HTTPException(status_code=404, detail=f"Username @{username} tidak ditemukan atau akun private")

    # ── Profile info ──────────────────────────────────────────────────────────
    edge_media = user.get("edge_owner_to_timeline_media") or {}
    timeline_edges = edge_media.get("edges", [])

    profile = {
        "user_id":        user.get("id", ""),
        "username":       user.get("username", username),
        "full_name":      user.get("full_name", ""),
        "biography":      user.get("biography", ""),
        "followers":      (user.get("edge_followed_by") or {}).get("count", 0),
        "following":      (user.get("edge_follow") or {}).get("count", 0),
        "post_count":     edge_media.get("count", 0),
        "profile_pic_url": user.get("profile_pic_url_hd") or user.get("profile_pic_url", ""),
        "is_verified":    user.get("is_verified", False),
        "is_private":     user.get("is_private", False),
        "is_business":    user.get("is_business_account", False),
        "business_category": user.get("business_category_name", ""),
        "external_url":   user.get("external_url", ""),
        "instagram_url":  f"https://www.instagram.com/{username}/",
    }

    # ── Recent posts ──────────────────────────────────────────────────────────
    recent_posts = []
    for edge in timeline_edges[:12]:
        node = edge.get("node", {})
        shortcode = node.get("shortcode", "")
        thumb = node.get("thumbnail_src") or node.get("display_url", "")
        cap_edges = (node.get("edge_media_to_caption") or {}).get("edges", [])
        caption = cap_edges[0]["node"]["text"] if cap_edges else ""
        likes = (node.get("edge_liked_by") or node.get("edge_media_preview_like") or {}).get("count", 0)
        cmts = (node.get("edge_media_to_comment") or {}).get("count", 0)

        recent_posts.append({
            "shortcode":     shortcode,
            "url":           f"https://www.instagram.com/p/{shortcode}/" if shortcode else "",
            "thumbnail":     thumb,
            "caption":       caption[:200],
            "likes":         likes,
            "comment_count": cmts,
            "media_type":    node.get("__typename", ""),
            "is_video":      node.get("is_video", False),
            "views":         node.get("video_view_count") if node.get("is_video") else None,
            "taken_at":      node.get("taken_at_timestamp"),
        })

    return build_success_response({
        "platform": "instagram",
        "username":  username,
        "profile":   profile,
        "recent_posts": recent_posts,
        "total_shown": len(recent_posts),
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /instagram/posts
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/posts", response_model=dict, summary="Scrape + ambil post Instagram dari username")
async def get_instagram_posts(
    username: str = Query(..., min_length=1, max_length=100, description="Username Instagram (tanpa @)"),
    max_posts: int = Query(default=10, ge=1, le=10, description="Jumlah post yang di-scrape (maks 10)"),
    max_comments: int = Query(default=20, ge=0, le=50, description="Jumlah komentar per post (maks 50)"),
    force_refresh: bool = Query(default=False, description="Paksa scrape ulang meski data sudah ada di DB"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Scrape post Instagram dari username, simpan ke DB, analisis sentimen komentar.

    **Behaviour:**
    - Jika data sudah ada di DB dan `force_refresh=false` → langsung return dari DB
    - Jika belum ada atau `force_refresh=true` → scrape dari EnsembleData Instagram API

    **Yang di-scrape per post:**
    - Info post: caption, likes, comments_count, media_type, thumbnail, shortcode
    - Komentar: maks `max_comments` per post, dianalisis dengan lexicon sentiment

    **Response:**
    - `user_info` : profil Instagram (followers, bio, dll)
    - `items`     : list post, masing-masing dengan `comments` nested + `sentiment_summary`
    - `stats`     : total post, komentar, coverage sentimen
    - `sentiment` : distribusi global positif/negatif/netral
    """
    username = username.strip().lstrip("@")

    # ── Cek apakah data sudah ada di DB ──────────────────────────────────────
    existing_count: int = await db.scalar(
        text("""
            SELECT COUNT(*) FROM posts
            WHERE platform = 'instagram' AND author = :username
        """),
        {"username": username},
    ) or 0

    scrape_result: dict | None = None

    if existing_count == 0 or force_refresh:
        from app.services.instagram.pipeline_service import scrape_instagram_posts
        scrape_result = await scrape_instagram_posts(
            db=db,
            username=username,
            max_posts=max_posts,
            max_comments=max_comments,
            keyword_id=None,
        )

    # ── Ambil posts dari DB ───────────────────────────────────────────────────
    rows = (await db.execute(text("""
        SELECT
            p.id, p.external_id, p.content, p.author, p.url,
            p.published_at, p.collected_at, p.metadata
        FROM posts p
        WHERE p.platform = 'instagram'
          AND p.author = :username
        ORDER BY p.published_at DESC NULLS LAST
        LIMIT :limit
    """), {"username": username, "limit": max_posts})).mappings().all()

    # ── Build user_info (dari scrape atau minimal dari DB) ────────────────────
    user_info: dict = {}
    if scrape_result:
        user_info = scrape_result.get("user_info") or {}
    if not user_info:
        # Fallback: ambil dari metadata post pertama jika ada
        if rows:
            meta = rows[0]["metadata"] or {}
            user_info = {"username": username}

    # ── Auto-scrape komentar untuk post yang belum punya (max 3 per request) ──
    if rows and max_comments > 0 and not scrape_result:
        from app.services.instagram.pipeline_service import scrape_instagram_posts
        ids_check = ", ".join(f"'{r['id']}'" for r in rows)
        existing_counts = dict((await db.execute(text(f"""
            SELECT post_id::text, COUNT(*) FROM comments
            WHERE post_id::text IN ({ids_check}) GROUP BY post_id::text
        """))).all())
        to_scrape = [r for r in rows if existing_counts.get(str(r["id"]), 0) == 0][:3]

        if to_scrape:
            try:
                await scrape_instagram_posts(
                    db=db,
                    username=username,
                    max_posts=max_posts,
                    max_comments=max_comments,
                    keyword_id=None,
                )
            except Exception:
                pass

    # ── Batch-fetch komentar dari DB ─────────────────────────────────────────
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

        shortcode = meta.get("shortcode", r["external_id"])

        items.append({
            "rank":          i + 1,
            "post_id":       r["external_id"],
            "shortcode":     shortcode,
            "url":           r["url"] or f"https://www.instagram.com/p/{shortcode}/",
            "caption":       r["content"] or "",
            "author":        r["author"],
            "likes":         meta.get("likes", 0),
            "comment_count": total_per_post.get(pid, meta.get("comments", 0)),
            "media_type":    meta.get("media_type", ""),
            "is_video":      meta.get("is_video", False),
            "thumbnail":     meta.get("thumbnail", ""),
            "views":         meta.get("views", 0),
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

    scrape_info = None
    if scrape_result:
        scrape_info = {
            "posts_scraped": scrape_result.get("posts_scraped", 0),
            "posts_new":     scrape_result.get("posts_saved", 0),
            "errors":        scrape_result.get("errors", []),
        }

    return build_success_response({
        "platform": "instagram",
        "username": username,
        "scrape":   scrape_info,
        "user_info": user_info,
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
