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
from app.shared.config import settings
from app.shared.utils import build_success_response

router = APIRouter(prefix="/instagram", tags=["instagram"])

_IG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "x-ig-app-id": "936619743392459",
    "X-Requested-With": "XMLHttpRequest",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
    "Accept": "*/*",
    "Referer": "https://www.instagram.com/",
    "Origin": "https://www.instagram.com",
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

    # Bangun cookies dari settings (sessionid wajib agar tidak di-block Instagram)
    cookies: dict = {}
    if settings.instagram_session_id:
        cookies["sessionid"] = settings.instagram_session_id
    if settings.instagram_csrf_token:
        cookies["csrftoken"] = settings.instagram_csrf_token

    if not cookies.get("sessionid"):
        raise HTTPException(
            status_code=503,
            detail="Instagram session belum dikonfigurasi. Set INSTAGRAM_SESSION_ID di .env server.",
        )

    try:
        async with httpx.AsyncClient(
            headers=_IG_HEADERS, cookies=cookies, timeout=20, follow_redirects=True
        ) as client:
            r = await client.get(
                "https://www.instagram.com/api/v1/users/web_profile_info/",
                params={"username": username},
            )
            if r.status_code == 404:
                raise HTTPException(status_code=404, detail=f"Username @{username} tidak ditemukan")
            if r.status_code == 429:
                raise HTTPException(status_code=429, detail="Instagram rate limit — coba lagi sebentar")
            if r.status_code == 401:
                raise HTTPException(status_code=503, detail="Instagram session expired — perbarui INSTAGRAM_SESSION_ID")
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
    max_comments: int = Query(default=5, ge=0, le=5, description="Jumlah komentar terpopuler per post (maks 5)"),
    force_refresh: bool = Query(default=False, description="Paksa scrape ulang (tetap dibatasi 1x per hari)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Scrape post Instagram dari username via EnsembleData, simpan ke DB, analisis sentimen komentar.

    **Behaviour:**
    - Maksimal **5 post per username per hari** untuk hemat EnsembleData token
    - Jika sudah di-scrape hari ini → langsung return dari DB (tidak hit EnsembleData lagi)
    - `force_refresh=true` tetap dibatasi 1x per hari per username

    **Yang di-scrape per post:**
    - Info post: caption, likes, comments_count, media_type, thumbnail, shortcode
    - Komentar: maks `max_comments` per post, dianalisis dengan lexicon sentiment

    **Response:**
    - `user_info` : profil Instagram (followers, bio, dll)
    - `items`     : list post dengan nested `comments` + `sentiment_summary`
    - `stats`     : total post, komentar, coverage sentimen
    - `sentiment` : distribusi global positif/negatif/netral
    """
    MAX_POSTS_PER_DAY = 5
    username = username.strip().lstrip("@").lower()

    # ── Cek apakah sudah di-scrape hari ini ──────────────────────────────────
    scraped_today: bool = await db.scalar(
        text("""
            SELECT EXISTS(
                SELECT 1 FROM posts
                WHERE platform = 'instagram'
                  AND author = :username
                  AND collected_at::date = CURRENT_DATE
            )
        """),
        {"username": username},
    ) or False

    existing_count: int = await db.scalar(
        text("SELECT COUNT(*) FROM posts WHERE platform = 'instagram' AND author = :username"),
        {"username": username},
    ) or 0

    scrape_result: dict | None = None

    # Scrape hanya jika: belum ada data ATAU (force_refresh DAN belum scrape hari ini)
    should_scrape = (existing_count == 0) or (force_refresh and not scraped_today)

    if should_scrape:
        from app.services.instagram.pipeline_service import scrape_instagram_posts
        scrape_result = await scrape_instagram_posts(
            db=db,
            username=username,
            max_posts=MAX_POSTS_PER_DAY,
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
    """), {"username": username, "limit": MAX_POSTS_PER_DAY})).mappings().all()

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
                    max_posts=MAX_POSTS_PER_DAY,
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

    scrape_info = {
        "executed":     should_scrape,
        "skipped_reason": "sudah di-scrape hari ini" if (not should_scrape and scraped_today) else None,
        "posts_scraped": scrape_result.get("posts_scraped", 0) if scrape_result else 0,
        "posts_new":     scrape_result.get("posts_saved", 0) if scrape_result else 0,
        "daily_limit":   MAX_POSTS_PER_DAY,
        "errors":        scrape_result.get("errors", []) if scrape_result else [],
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


# ─────────────────────────────────────────────────────────────────────────────
# GET /instagram/trending
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/trending", response_model=dict, summary="Top 5 akun Instagram trending hari ini")
async def get_instagram_trending(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Ambil top 5 akun Instagram trending beserta post + sentimen komentar.

    Data di-update otomatis setiap hari jam 09:00 WIB via Celery Beat.
    Jika belum ada data hari ini, jalankan discovery + scoring on-demand.
    """
    from sqlalchemy import select as sa_select
    from app.domain.instagram_trending.models import InstagramTrendingAccount

    accounts = (await db.scalars(
        sa_select(InstagramTrendingAccount)
        .where(InstagramTrendingAccount.status == "active")
        .order_by(InstagramTrendingAccount.rank.asc().nulls_last())
        .limit(5)
    )).all()

    if not accounts:
        return build_success_response({
            "platform":       "instagram",
            "total_accounts": 0,
            "updated_daily":  "09:00 WIB",
            "message": "Belum ada data trending. Task harian berjalan jam 09:00 WIB.",
            "accounts": [],
        })

    # Ambil posts + sentimen per akun dari DB
    result_accounts = []
    for account in accounts:
        rows = (await db.execute(text("""
            SELECT p.id, p.external_id, p.content, p.url, p.published_at,
                   p.metadata, p.collected_at
            FROM posts p
            WHERE p.platform = 'instagram' AND p.author = :username
            ORDER BY p.published_at DESC NULLS LAST
            LIMIT 2
        """), {"username": account.username})).mappings().all()

        post_ids = [str(r["id"]) for r in rows]
        comments_by_post: dict[str, list] = {pid: [] for pid in post_ids}
        all_labels: list[str] = []

        if post_ids:
            ids_sql = ", ".join(f"'{pid}'" for pid in post_ids)
            cmt_rows = (await db.execute(text(f"""
                SELECT c.content, c.author, c.post_id::text AS post_id,
                       la.label AS sentiment, la.score
                FROM comments c
                LEFT JOIN lexicon_analyses la ON la.comment_id = c.id
                WHERE c.post_id::text IN ({ids_sql})
                ORDER BY la.score DESC NULLS LAST
                LIMIT 25
            """))).mappings().all()

            for cr in cmt_rows:
                pid = cr["post_id"]
                if cr["sentiment"]:
                    all_labels.append(cr["sentiment"])
                bucket = comments_by_post.setdefault(pid, [])
                if len(bucket) < 5:
                    bucket.append({
                        "content":   cr["content"],
                        "author":    cr["author"],
                        "sentiment": cr["sentiment"],
                        "score":     round(float(cr["score"]), 3) if cr["score"] is not None else None,
                    })

        counter = Counter(all_labels)
        total_analyzed = sum(counter.values())

        posts_out = []
        for r in rows:
            pid = str(r["id"])
            meta = r["metadata"] or {}
            posts_out.append({
                "post_id":      r["external_id"],
                "url":          r["url"] or "",
                "caption":      (r["content"] or "")[:200],
                "likes":        meta.get("likes", 0),
                "comment_count": meta.get("comments", 0),
                "thumbnail":    meta.get("thumbnail", ""),
                "published_at": r["published_at"].isoformat() if r["published_at"] else None,
                "comments":     comments_by_post.get(pid, []),
            })

        result_accounts.append({
            "rank":            account.rank,
            "username":        account.username,
            "display_name":    account.display_name,
            "followers":       account.followers,
            "trending_score":  account.trending_score,
            "engagement_rate": account.engagement_rate,
            "virality_score":  account.virality_score,
            "source":          account.source,
            "discovered_via":  account.discovered_via,
            "last_scraped":    account.last_scraped_date.isoformat() if account.last_scraped_date else None,
            "sentiment": {
                lbl: {
                    "count":      counter.get(lbl, 0),
                    "percentage": round(counter.get(lbl, 0) / total_analyzed * 100, 1) if total_analyzed else 0.0,
                }
                for lbl in ["positif", "negatif", "netral"]
            },
            "posts": posts_out,
        })

    return build_success_response({
        "platform":        "instagram",
        "total_accounts":  len(result_accounts),
        "updated_daily":   "09:00 WIB",
        "accounts":        result_accounts,
    })
