"""
Instagram API endpoints. Scraping via Apify (pengganti EnsembleData — lihat
docs/apify-instagram-method.md, docs/trend-recommendations.md).

GET  /instagram/profile      — profil + recent posts dari username (Instagram internal API, bukan Apify)
GET  /instagram/posts        — scrape + ambil post dari username (manual, tanpa budget cap)
GET  /instagram/search       — [nonaktif] Apify tidak punya fitur discovery-by-keyword
GET  /instagram/trending     — topik viral Instagram dari trend_recommendations + hasil scrape
GET  /instagram/analysis/summary — ringkasan MENYELURUH hasil analisis sentimen (semua akun, bukan cuma trend_recommendations)
GET  /instagram/comments     — list komentar Instagram (filter username/post/sentimen/tanggal)
POST /instagram/scrape       — trigger scrape username manual via Celery (tanpa budget cap)
POST /instagram/trend-scrape/run — trigger manual batch harian trend_recommendations (maks N topik/hari)
GET  /instagram/trend-scrape/status — monitoring pending/used + riwayat scrape_runs
"""
from __future__ import annotations

import uuid
from collections import Counter
from datetime import date, datetime, timezone

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
            SELECT c.id, c.external_id, c.content, c.author, c.post_id::text AS post_id,
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
                    "id":         str(cr["id"]),
                    "comment_id": cr["external_id"],
                    "content":    cr["content"],
                    "author":     cr["author"],
                    "sentiment":  cr["sentiment"],
                    "score":      round(float(cr["score"]), 3) if cr["score"] is not None else None,
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
# GET /instagram/search
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/search", response_model=dict, summary="[Nonaktif] Cari akun Instagram by keyword")
async def search_instagram_keyword(
    q: str = Query(..., min_length=1, max_length=200, description="Keyword, nama akun, atau topik"),
    current_user: User = Depends(get_current_user),
):
    """
    **Sudah tidak aktif sejak migrasi EnsembleData → Apify.**

    Apify tidak punya fitur cari-akun-by-keyword/hashtag (hanya bisa scrape
    username yang sudah diketahui) — jadi fitur discovery-by-keyword ini tidak
    bisa direplikasi. Gunakan `GET /instagram/posts?username=...` kalau sudah
    tahu username-nya, atau submit topik+akun via `POST /trend-recommendations`
    supaya diproses otomatis oleh pipeline trend recommendation.

    Detail: docs/apify-instagram-method.md, docs/trend-recommendations.md
    """
    raise HTTPException(
        status_code=501,
        detail=(
            "GET /instagram/search dinonaktifkan — Apify (pengganti EnsembleData) "
            "tidak punya fitur cari-akun-by-keyword/hashtag. Gunakan GET /instagram/posts "
            "dengan username yang sudah diketahui, atau submit ke POST /trend-recommendations."
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /instagram/trending
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/trending", response_model=dict, summary="Topik trending Instagram dari trend_recommendations")
async def get_instagram_trending(
    recommendation_date: date | None = Query(default=None, description="Default: hari ini"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Ambil topik viral Instagram (dari `trend_recommendations`, diisi AI eksternal
    via `POST /trend-recommendations`) beserta post + sentimen komentar hasil scrape.

    Scraping otomatis berjalan tiap hari jam 09:00 WIB (Celery Beat), maksimal
    `settings.instagram_trend_daily_budget` topik/hari (urut score tertinggi,
    lihat docs/trend-recommendations.md). `status='used'` berarti sudah discrape,
    `status='pending'` berarti masih menunggu giliran.
    """
    from app.domain.trend_recommendations.models import TrendRecommendation

    target_date = recommendation_date or date.today()
    topics = (await db.scalars(
        select(TrendRecommendation)
        .where(TrendRecommendation.recommendation_date == target_date)
        .order_by(TrendRecommendation.score.desc())
    )).all()

    # Filter topik yang punya related_account di platform instagram
    ig_topics = []
    for t in topics:
        ig_account = next(
            (a for a in (t.related_accounts or []) if a.get("platform") == "instagram"),
            None,
        )
        if ig_account:
            ig_topics.append((t, ig_account["username"]))

    if not ig_topics:
        return build_success_response({
            "platform":      "instagram",
            "date":          target_date.isoformat(),
            "total_topics":  0,
            "updated_daily": "09:00 WIB",
            "message": "Belum ada topik trending Instagram untuk tanggal ini. Submit via POST /trend-recommendations.",
            "topics": [],
        })

    result_topics = []
    for topic, username in ig_topics:
        rows = (await db.execute(text("""
            SELECT p.id, p.external_id, p.content, p.url, p.published_at, p.metadata
            FROM posts p
            WHERE p.platform = 'instagram' AND p.author = :username
            ORDER BY p.published_at DESC NULLS LAST
            LIMIT 2
        """), {"username": username})).mappings().all()

        post_ids = [str(r["id"]) for r in rows]
        comments_by_post: dict[str, list] = {pid: [] for pid in post_ids}
        all_labels: list[str] = []

        if post_ids:
            ids_sql = ", ".join(f"'{pid}'" for pid in post_ids)
            cmt_rows = (await db.execute(text(f"""
                SELECT c.id, c.external_id, c.content, c.author, c.post_id::text AS post_id,
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
                        "id":         str(cr["id"]),
                        "comment_id": cr["external_id"],
                        "content":    cr["content"],
                        "author":     cr["author"],
                        "sentiment":  cr["sentiment"],
                        "score":      round(float(cr["score"]), 3) if cr["score"] is not None else None,
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
                "hashtags":     meta.get("hashtags", []),
                "published_at": r["published_at"].isoformat() if r["published_at"] else None,
                "comments":     comments_by_post.get(pid, []),
            })

        result_topics.append({
            "topic":          topic.topic,
            "score":          topic.score,
            "status":         topic.status,
            "instagram_username": username,
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
        "date":            target_date.isoformat(),
        "total_topics":    len(result_topics),
        "updated_daily":   "09:00 WIB",
        "daily_budget":    settings.instagram_trend_daily_budget,
        "topics":          result_topics,
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /instagram/analysis/summary — ringkasan MENYELURUH hasil analisis sentimen
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/analysis/summary", response_model=dict,
            summary="Ringkasan menyeluruh hasil analisis sentimen Instagram (semua akun)")
async def get_instagram_analysis_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Ringkasan MENYELURUH hasil scrape + analisis sentimen Instagram, lintas SEMUA
    akun/topik yang pernah discrape — baik dari pipeline `trend_recommendations`
    maupun scrape manual (`POST /instagram/scrape`). Beda dengan `/trending` yang
    cuma menampilkan topik dari `trend_recommendations` per tanggal tertentu.

    - `overall`: total post/komentar/sudah dianalisis + breakdown sentimen keseluruhan
    - `per_account`: breakdown yang sama, dipecah per akun (urut jumlah komentar terbanyak)
    """
    overall_row = (await db.execute(text("""
        SELECT
            count(DISTINCT p.id) AS total_posts,
            count(c.id)          AS total_comments,
            count(la.id)         AS total_analyzed,
            count(*) FILTER (WHERE la.label = 'positif') AS positif,
            count(*) FILTER (WHERE la.label = 'negatif') AS negatif,
            count(*) FILTER (WHERE la.label = 'netral')  AS netral
        FROM posts p
        LEFT JOIN comments c ON c.post_id = p.id
        LEFT JOIN lexicon_analyses la ON la.comment_id = c.id
        WHERE p.platform = 'instagram'
    """))).mappings().first()

    per_account_rows = (await db.execute(text("""
        SELECT
            p.author AS username,
            count(DISTINCT p.id) AS post_count,
            count(c.id)          AS comment_count,
            count(la.id)         AS analyzed_count,
            count(*) FILTER (WHERE la.label = 'positif') AS positif,
            count(*) FILTER (WHERE la.label = 'negatif') AS negatif,
            count(*) FILTER (WHERE la.label = 'netral')  AS netral
        FROM posts p
        LEFT JOIN comments c ON c.post_id = p.id
        LEFT JOIN lexicon_analyses la ON la.comment_id = c.id
        WHERE p.platform = 'instagram'
        GROUP BY p.author
        ORDER BY comment_count DESC
    """))).mappings().all()

    def _pct(count: int, total: int) -> float:
        return round(count / total * 100, 1) if total else 0.0

    total_comments = overall_row["total_comments"] or 0

    return build_success_response({
        "overall": {
            "total_posts":    overall_row["total_posts"],
            "total_comments": total_comments,
            "total_analyzed": overall_row["total_analyzed"],
            "fully_analyzed": overall_row["total_analyzed"] == total_comments,
            "sentiment": {
                "positif": {"count": overall_row["positif"], "percentage": _pct(overall_row["positif"], total_comments)},
                "negatif": {"count": overall_row["negatif"], "percentage": _pct(overall_row["negatif"], total_comments)},
                "netral":  {"count": overall_row["netral"],  "percentage": _pct(overall_row["netral"], total_comments)},
            },
        },
        "per_account": [
            {
                "username":       r["username"],
                "post_count":     r["post_count"],
                "comment_count":  r["comment_count"],
                "analyzed_count": r["analyzed_count"],
                "fully_analyzed": r["analyzed_count"] == r["comment_count"],
                "sentiment": {
                    "positif": r["positif"],
                    "negatif": r["negatif"],
                    "netral":  r["netral"],
                },
            }
            for r in per_account_rows
        ],
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /instagram/comments
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/comments", response_model=dict, summary="List komentar Instagram dengan filter")
async def list_instagram_comments(
    username: str | None = Query(default=None, description="Filter per username pemilik post"),
    post_id: str | None = Query(default=None, description="Instagram post_id (external_id, mis. 3123456789)"),
    post_uuid: uuid.UUID | None = Query(default=None, description="UUID internal post di DB"),
    sentiment: str | None = Query(default=None, description="positif | negatif | netral"),
    date_from: date | None = Query(default=None, description="Filter dari tanggal (created_at)"),
    date_to: date | None = Query(default=None, description="Filter sampai tanggal (created_at)"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    List komentar Instagram yang sudah di-scrape.

    Setiap komentar terikat ke satu post spesifik melalui `post_id` (FK).
    Filter tersedia:
    - `username`  — pemilik post (bukan author komentar)
    - `post_id`   — Instagram post external_id (pk post dari platform)
    - `post_uuid` — UUID internal post di DB
    - `sentiment` — positif | negatif | netral
    - `date_from` / `date_to` — rentang tanggal scrape komentar

    Response setiap item menyertakan info post induknya (`post_id`, `post_url`, `caption`)
    sehingga relasi komentar → post selalu jelas.
    """
    filters = ["p.platform = 'instagram'"]
    params: dict = {"limit": limit, "offset": offset}

    if username:
        filters.append("p.author = :username")
        params["username"] = username.strip().lstrip("@").lower()
    if post_uuid:
        filters.append("c.post_id = :post_uuid")
        params["post_uuid"] = str(post_uuid)
    elif post_id:
        filters.append("p.external_id = :post_ext_id")
        params["post_ext_id"] = post_id.strip()
    if sentiment:
        filters.append("la.label = :sentiment")
        params["sentiment"] = sentiment
    if date_from:
        filters.append("c.created_at >= :date_from")
        params["date_from"] = datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc)
    if date_to:
        filters.append("c.created_at <= :date_to")
        params["date_to"] = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59, tzinfo=timezone.utc)

    where_clause = " AND ".join(filters)
    join_type = "JOIN" if sentiment else "LEFT JOIN"

    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    total: int = (await db.scalar(text(f"""
        SELECT COUNT(*)
        FROM comments c
        JOIN posts p ON c.post_id = p.id
        {join_type} lexicon_analyses la ON la.comment_id = c.id
        WHERE {where_clause}
    """), count_params)) or 0

    rows = (await db.execute(text(f"""
        SELECT
            c.id,
            c.external_id          AS comment_id,
            c.content,
            c.author               AS comment_author,
            c.created_at           AS scraped_at,
            c.metadata,
            p.id                   AS post_uuid,
            p.external_id          AS post_id,
            p.author               AS post_owner,
            p.content              AS caption,
            p.url                  AS post_url,
            p.published_at         AS post_published_at,
            la.label               AS sentiment,
            la.score               AS sentiment_score
        FROM comments c
        JOIN posts p ON c.post_id = p.id
        {join_type} lexicon_analyses la ON la.comment_id = c.id
        WHERE {where_clause}
        ORDER BY c.created_at DESC
        OFFSET :offset LIMIT :limit
    """), params)).mappings().all()

    items = []
    for r in rows:
        meta = r["metadata"] or {}
        items.append({
            "id":             str(r["id"]),
            "comment_id":     r["comment_id"],
            "content":        r["content"],
            "author":         r["comment_author"],
            "sentiment":      r["sentiment"],
            "sentiment_score": round(float(r["sentiment_score"]), 3) if r["sentiment_score"] is not None else None,
            "like_count":     meta.get("like_count", 0),
            "child_comment_count": meta.get("child_comment_count", 0),
            "author_user_id": meta.get("author_user_id"),
            "scraped_at":     r["scraped_at"].isoformat() if r["scraped_at"] else None,
            "post": {
                "post_uuid":    str(r["post_uuid"]),
                "post_id":      r["post_id"],
                "post_owner":   r["post_owner"],
                "caption":      (r["caption"] or "")[:200],
                "post_url":     r["post_url"] or f"https://www.instagram.com/p/{r['post_id']}/",
                "published_at": r["post_published_at"].isoformat() if r["post_published_at"] else None,
            },
        })

    return build_success_response({
        "platform": "instagram",
        "filter": {
            "username":  username,
            "post_id":   post_id,
            "post_uuid": str(post_uuid) if post_uuid else None,
            "sentiment": sentiment,
            "date_from": str(date_from) if date_from else None,
            "date_to":   str(date_to) if date_to else None,
        },
        "total":  total,
        "offset": offset,
        "limit":  limit,
        "items":  items,
    })


# ─────────────────────────────────────────────────────────────────────────────
# POST /instagram/scrape  — trigger scraping username via Celery (background)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/scrape", response_model=dict, status_code=202,
             summary="Trigger scraping Instagram username secara background (Celery)")
async def trigger_instagram_scrape(
    username: str = Query(..., min_length=1, max_length=100, description="Username Instagram (tanpa @)"),
    max_posts: int = Query(default=5, ge=1, le=5, description="Maks post per username (1-5)"),
    max_comments: int = Query(default=5, ge=0, le=5, description="Maks komentar per post (0-5)"),
    current_user: User = Depends(get_current_user),
):
    """
    Trigger scraping Instagram secara async via Celery worker.

    Berbeda dengan `GET /instagram/posts` yang scrape inline (request harus tunggu selesai),
    endpoint ini langsung return **202 Accepted** dan scraping berjalan di background.

    **Gunakan ini untuk:**
    - Scrape username baru tanpa memblok response
    - Trigger ulang scrape username yang sudah ada
    - Integrasi dengan scheduler / cron eksternal

    **Flow background:**
    1. Celery worker terima task
    2. Panggil EnsembleData: user/info → user/posts → post/comments
    3. Simpan ke DB: posts + comments + lexicon_analyses
    4. Bisa dipantau via `GET /youtube/monitor-public` (worker status)

    **Cek hasil setelah selesai:**
    `GET /instagram/posts?username={username}` — ambil dari DB
    `GET /instagram/comments?username={username}` — list komentar
    """
    from app.workers.instagram_trending_worker import instagram_scrape_username_task

    clean_username = username.strip().lstrip("@").lower()
    task = instagram_scrape_username_task.delay(
        username=clean_username,
        max_posts=max_posts,
        max_comments=max_comments,
    )

    return build_success_response({
        "status":       "queued",
        "task_id":      task.id,
        "username":     clean_username,
        "max_posts":    max_posts,
        "max_comments": max_comments,
        "message":      f"Scraping @{clean_username} dijadwalkan. Cek hasilnya di GET /instagram/posts?username={clean_username}",
    })


# ─────────────────────────────────────────────────────────────────────────────
# POST /instagram/trend-scrape/run — trigger manual batch harian (trend_recommendations)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/trend-scrape/run", response_model=dict, status_code=202,
             summary="Trigger manual batch scrape trend_recommendations (tanpa nunggu jadwal 09:00)")
async def trigger_trend_scrape_run(
    current_user: User = Depends(get_current_user),
):
    """
    Trigger manual proses scraping batch harian Instagram dari `trend_recommendations`
    (sama seperti yang jalan otomatis jam 09:00 WIB via Celery Beat).

    Tetap mengikuti budget `settings.instagram_trend_daily_budget` — topik yang
    sudah `status='used'` hari ini tidak di-scrape ulang. Gunakan ini untuk
    testing atau kalau tidak mau menunggu jadwal harian.
    """
    from app.workers.instagram_trending_worker import instagram_trend_recommendation_daily_task

    task = instagram_trend_recommendation_daily_task.delay()

    return build_success_response({
        "status":  "queued",
        "task_id": task.id,
        "message": "Batch scrape trend_recommendations dijadwalkan di background.",
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /instagram/trend-scrape/status — monitoring pipeline trend_recommendations
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/trend-scrape/status", response_model=dict,
            summary="Monitoring pipeline scrape trend_recommendations (pending/used, riwayat run)")
async def get_trend_scrape_status(
    recent_limit: int = Query(default=10, ge=1, le=50, description="Jumlah riwayat scrape_runs terakhir"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Snapshot status pipeline Instagram trend-recommendations, tanpa perlu psql manual:

    - Ringkasan berapa topik `pending` vs `used` yang punya akun Instagram
      (lintas tanggal, bukan cuma hari ini — supaya kelihatan backlog-nya)
    - Daftar topik `pending` yang akan diambil giliran berikutnya (urut score)
    - Riwayat run terakhir dari `scrape_runs` (sukses/gagal, durasi, error)

    Scraping otomatis jalan tiap hari jam 09:00 WIB (Celery Beat), maksimal
    `settings.instagram_trend_daily_budget` topik/hari. Kalau sebuah topik gagal
    di-scrape (0 post), statusnya TETAP `pending` dan otomatis dicoba lagi di run
    berikutnya — tidak perlu campur tangan manual.

    `summary.ai_keyword_search_pending` = berapa dari topik pending itu yang berasal
    dari trigger `GET /instagram/posts/search` (keyword miss), bukan submission AI
    eksternal manual — dan tiap `pending_topics` item punya flag `is_ai_keyword_search`.
    """
    from app.services.instagram_trending.trend_scrape_service import get_trend_scrape_summary

    return build_success_response(await get_trend_scrape_summary(db, recent_limit=recent_limit))
