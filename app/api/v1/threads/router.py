"""
Threads API endpoints — Fase 1 (scrape dasar via EnsembleData).

TERPISAH TOTAL dari YouTube/TikTok/Facebook/Instagram/Twitter/News — tidak
mengimpor atau mengubah apa pun milik platform lain.

GET  /threads/search              — cari post dari DB berdasarkan keyword, comments NESTED per post
POST /threads/search               — trigger search BARU secara background (Celery), return job_id
GET  /threads/posts/{post_id}      — detail 1 post + SEMUA balasannya yang tersimpan
POST /threads/trend-scrape/run     — trigger manual batch scrape trend_recommendations
GET  /threads/trend-scrape/status  — monitoring pipeline scrape trend_recommendations

Desain response mengikuti pelajaran dari perbaikan smart-search YouTube
(2026-07-19): comments SELALU nested di dalam post yang bersangkutan
(bukan 2 array terpisah yang perlu di-join manual), setiap comment punya
`post_id` eksplisit, dan `limit_comments` TANPA batas atas (kirim angka
besar untuk ambil semua balasan yang tersimpan).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.comments.models import Comment
from app.domain.posts.models import Post
from app.domain.users.models import User
from app.domain.youtube_analysis.models import LexiconAnalysis
from app.infrastructure.database.connection import get_db
from app.services.auth.dependencies import get_current_user
from app.shared.exceptions import NotFoundError
from app.shared.utils import build_success_response

router = APIRouter(prefix="/threads", tags=["threads"])


def _post_to_dict(p: Post) -> dict:
    meta = p.metadata_ or {}
    return {
        "id": str(p.id),
        "external_id": p.external_id,
        "url": p.url,
        "author": p.author,
        "content": p.content,
        "likes": meta.get("likes", 0),
        "replies": meta.get("replies", 0),
        "reposts": meta.get("reposts", 0),
        "quotes": meta.get("quotes", 0),
        "media": p.media or [],
        "tags": p.tags or [],
        "published_at": p.published_at.isoformat() if p.published_at else None,
        "collected_at": p.collected_at.isoformat() if p.collected_at else None,
    }


def _comment_to_dict(comment: Comment, analysis: LexiconAnalysis | None, post: Post) -> dict:
    meta = comment.metadata_ or {}
    return {
        "id": str(comment.id),
        "post_id": str(post.id),
        "content": comment.content,
        "author": comment.author,
        "reply_to": meta.get("reply_to"),
        "like_count": meta.get("like_count", 0),
        # final_label (LLM+tie-breaker) kalau sudah direview, jatuh ke
        # label lexicon asli kalau belum -- pola SAMA dgn YouTube
        # smart-search (2026-07-18/19), TAPI Sentiment Agent (LLM)
        # SEKARANG BARU cover platform='youtube' -- utk Threads,
        # final_label akan SELALU None (murni lexicon) sampai Sentiment
        # Agent diperluas ke platform lain.
        "sentiment": (analysis.final_label or analysis.label) if analysis else None,
        "sentiment_source": ("llm_reviewed" if analysis and analysis.final_label else "lexicon_only") if analysis else None,
        "score": round(analysis.score, 3) if analysis else None,
        "published_at": comment.published_at.isoformat() if comment.published_at else None,
    }


@router.get("/search", response_model=dict)
async def search_threads(
    q: str = Query(..., min_length=1, max_length=200, description="Kata kunci/topik yang dicari"),
    limit_posts: int = Query(default=20, ge=1, le=100, description="Jumlah post yang dikembalikan"),
    limit_comments: int = Query(default=20, ge=1, description="Jumlah balasan per post -- TANPA batas atas, kirim angka besar utk ambil SEMUA balasan yang tersimpan"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Cari post Threads dari data yang SUDAH tersimpan di DB (ILIKE ke
    content/author). Kalau belum ada data, pakai `POST /threads/search`
    dulu utk trigger pencarian baru via EnsembleData.

    Balasan SELALU nested di dalam post-nya masing2 (`posts[i].comments`),
    tidak ada array comments terpisah -- setiap balasan tetap punya
    `post_id` eksplisit utk keperluan audit/join manual kalau diperlukan.
    """
    q_clean = q.strip()

    rows = (await db.scalars(
        select(Post)
        .where(
            Post.platform == "threads",
            (Post.content.ilike(f"%{q_clean}%")) | (Post.author.ilike(f"%{q_clean}%")),
        )
        .order_by(Post.collected_at.desc())
        .limit(limit_posts)
    )).all()

    if not rows:
        return build_success_response({
            "status": "empty",
            "query": q,
            "message": "Belum ada data Threads utk keyword ini. Gunakan POST /threads/search utk cari baru.",
            "posts": [],
        })

    post_ids = [p.id for p in rows]
    comment_rows = (await db.execute(
        select(Comment, LexiconAnalysis, Post)
        .join(Post, Comment.post_id == Post.id)
        .outerjoin(LexiconAnalysis, LexiconAnalysis.comment_id == Comment.id)
        .where(Comment.post_id.in_(post_ids))
        .order_by(Comment.created_at.desc())
        .limit(limit_comments * len(rows))
    )).all()

    comments_by_post: dict[str, list] = {}
    for comment, analysis, post in comment_rows:
        bucket = comments_by_post.setdefault(str(post.id), [])
        if len(bucket) < limit_comments:
            bucket.append(_comment_to_dict(comment, analysis, post))

    posts = []
    for p in rows:
        item = _post_to_dict(p)
        item["comment_count"] = len(comments_by_post.get(str(p.id), []))
        item["comments"] = comments_by_post.get(str(p.id), [])
        posts.append(item)

    return build_success_response({
        "status": "ready",
        "query": q,
        "total_posts": len(posts),
        "posts": posts,
    })


@router.post("/search", response_model=dict, status_code=202)
async def trigger_threads_search(
    q: str = Query(..., min_length=1, max_length=200),
    max_posts: int = Query(default=10, ge=1, le=20),
    comments_top_n: int = Query(default=3, ge=0, le=10, description="Jumlah post teratas yg diambil balasannya (kendali biaya EnsembleData)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger pencarian Threads lewat alur TIER (Fase 1, 2026-07-20):

    1. Data utk keyword ini SUDAH ADA & masih segar (< cache_freshness_hours,
       default 24 jam)? -> balas "ready_from_cache", TIDAK panggil
       EnsembleData sama sekali (hemat kuota).
    2. Belum ada/basi -> slot job paralel Threads (`max_concurrent_jobs`,
       default 2) masih ada? -> dispatch Celery BACKGROUND spt biasa,
       balas job_id.
    3. Slot penuh -> masuk `threads_search_queue`, diproses otomatis
       belakangan oleh task `threads-queue-drain` (tiap 10 menit).

    Lihat docs/threads-redesign-schema.md.
    """
    from app.services.threads import search_tier_service as tier

    fresh_count = await tier.get_fresh_cached_post_count(db, q)
    if fresh_count > 0:
        return build_success_response({
            "status": "ready_from_cache",
            "query": q,
            "fresh_post_count": fresh_count,
            "message": "Data utk keyword ini sudah ada & masih segar. Lihat via GET /threads/search, tidak perlu cari ulang.",
        })

    if await tier.has_available_slot(db):
        from app.workers.threads_trending_worker import threads_search_keyword_task

        task = threads_search_keyword_task.delay(q.strip(), max_posts=max_posts, comments_top_n=comments_top_n)
        return build_success_response({
            "status": "queued",
            "query": q,
            "job_id": task.id,
            "message": "Pencarian berjalan di background. Cek hasil via GET /threads/search setelah beberapa saat.",
        })

    queue_item = await tier.enqueue_search(db, q, source="manual")
    return build_success_response({
        "status": "queued_pending",
        "query": q,
        "queue_id": str(queue_item.id),
        "message": "Slot pencarian Threads sedang penuh. Otomatis diproses saat slot kosong (dicek tiap 10 menit).",
    })


@router.get("/posts/{post_id}", response_model=dict)
async def get_threads_post_detail(
    post_id: str,
    limit_comments: int = Query(default=50, ge=0, description="Jumlah balasan -- TANPA batas atas"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Detail 1 post Threads (by UUID internal ATAU external_id/pk Threads)
    + SEMUA balasannya yang tersimpan di DB. Mirroring GET /youtube/videos/{video_id}.
    """
    try:
        post_uuid = uuid.UUID(post_id)
        post = await db.get(Post, post_uuid)
    except ValueError:
        post = await db.scalar(
            select(Post).where(Post.platform == "threads", Post.external_id == post_id).limit(1)
        )

    if not post:
        raise NotFoundError("Threads post", post_id)

    comments = []
    if limit_comments > 0:
        rows = (await db.execute(
            select(Comment, LexiconAnalysis)
            .outerjoin(LexiconAnalysis, LexiconAnalysis.comment_id == Comment.id)
            .where(Comment.post_id == post.id)
            .order_by(Comment.created_at.desc())
            .limit(limit_comments)
        )).all()
        comments = [_comment_to_dict(c, a, post) for c, a in rows]

    total_comments = (await db.scalar(
        select(func.count(Comment.id)).where(Comment.post_id == post.id)
    )) or 0

    result = _post_to_dict(post)
    result["total_comments_in_db"] = total_comments
    result["comments"] = comments
    return build_success_response(result)


@router.post("/trend-scrape/run", response_model=dict, status_code=202)
async def trigger_threads_trend_scrape(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger manual batch scrape trend_recommendations utk Threads
    (biasanya jalan otomatis via Celery Beat harian, ini utk testing/manual)."""
    from app.services.threads.trend_scrape_service import run_daily_trend_scrape_threads

    result = await run_daily_trend_scrape_threads(db)
    return build_success_response(result)


@router.get("/trend-scrape/status", response_model=dict)
async def get_threads_trend_scrape_status(
    recent_limit: int = Query(default=10, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ringkasan pipeline scrape Threads dari trend_recommendations (cuma
    bagian SCRAPING -- utk gabungan scraping+data+sentiment lihat GET /threads/monitor)."""
    from app.services.threads.trend_scrape_service import get_threads_trend_scrape_summary

    result = await get_threads_trend_scrape_summary(db, recent_limit=recent_limit)
    return build_success_response(result)


@router.get("/monitor", response_model=dict)
async def get_threads_monitor(
    recent_limit: int = Query(default=10, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Monitoring MENYELURUH Threads: status scraping (budget/jadwal/topik/
    riwayat run) SAMPAI sentiment (distribusi label + berapa persen yang
    sudah tervalidasi LLM Sentiment Agent). Satu endpoint utk gambaran
    penuh pipeline dari ujung ke ujung.
    """
    from app.services.threads.trend_scrape_service import get_threads_monitoring_summary

    result = await get_threads_monitoring_summary(db, recent_limit=recent_limit)
    return build_success_response(result)
