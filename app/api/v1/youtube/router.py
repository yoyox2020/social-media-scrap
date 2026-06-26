"""
YouTube Intelligence API.

Semua endpoint READ menggunakan filter date_from/date_to/hour.
Data TIDAK PERNAH dihapus — semua tersimpan historis di PostgreSQL.

Video yang disimpan ke DB adalah URL YouTube (link), BUKAN file video.
  contoh: https://youtube.com/watch?v=xxxx
  plus metadata: judul, channel, views, thumbnail_url, collected_at

Flow otomatis (Celery Beat setiap 1 jam):
  trending/fetch → keywords → collect videos (URL+metadata) → comments → sentiment
"""
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.comments.models import Comment
from app.domain.keywords.models import Keyword
from app.domain.posts.models import Post
from app.domain.trending.models import TrendingTopic
from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.services.auth.dependencies import get_current_user
from app.services.youtube.pipeline_service import (
    fetch_and_store_trending,
    get_dashboard_summary,
    get_keyword_pipeline_status,
    get_sentiment_distribution,
    get_sentiment_table,
    get_wordcloud_data,
)
from app.services.youtube.schemas import (
    TrendingFetchRequest,
    YouTubeCollectRequest,
)
from app.shared.utils import build_success_response

router = APIRouter(prefix="/youtube", tags=["youtube"])


# ─────────────────────────────────────────────────────────────────────────────
# TRIGGER MANUAL
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/collect", response_model=dict, status_code=202)
async def collect_youtube(
    body: YouTubeCollectRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Trigger pipeline YouTube untuk satu keyword (async via Celery):
      → scrape video YouTube (simpan URL + metadata ke DB)
      → scrape komentar → lexicon sentiment
    """
    from app.workers.youtube_worker import collect_youtube_pipeline_task

    task = collect_youtube_pipeline_task.delay(
        str(body.keyword_id),
        max_pages=body.max_pages,
        max_comments_per_video=body.max_comments_per_video,
        max_comment_pages=body.max_comment_pages,
    )
    return build_success_response({
        "job_id": task.id,
        "keyword_id": str(body.keyword_id),
        "status": "queued",
        "message": "Pipeline sedang berjalan di background. Video URL + metadata akan tersimpan ke DB.",
    })


# ─────────────────────────────────────────────────────────────────────────────
# GOOGLE TRENDS
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/trending/fetch", response_model=dict, status_code=202)
async def fetch_trending_endpoint(
    body: TrendingFetchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Ambil Google Trends → simpan trending_topics ke DB (histori tersimpan semua)
    → buat Keyword records → queue pipeline per keyword (jika auto_collect=true).
    """
    result = await fetch_and_store_trending(db, body)
    return build_success_response(result.model_dump())


@router.get("/trending", response_model=dict)
async def list_trending(
    geo: str = Query(default="ID", max_length=10),
    period: str = Query(default="24h"),
    date_from: date | None = Query(default=None, description="Filter dari tanggal (YYYY-MM-DD)"),
    date_to: date | None = Query(default=None, description="Filter sampai tanggal (YYYY-MM-DD), inklusif"),
    hour: int | None = Query(default=None, ge=0, le=23, description="Filter jam tertentu (0-23 UTC)"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    List trending topics dari PostgreSQL.

    Filter opsional:
    - date_from / date_to  → filter rentang tanggal
    - hour                 → filter jam tertentu (UTC)
    - Tanpa filter         → tampilkan semua, terbaru di atas

    Data tidak pernah dihapus — semua histori tersimpan.
    """
    q = select(TrendingTopic).where(
        TrendingTopic.geo == geo,
        TrendingTopic.period == period,
    )

    if date_from:
        q = q.where(TrendingTopic.fetched_at >= datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc))
    if date_to:
        end = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59, tzinfo=timezone.utc)
        q = q.where(TrendingTopic.fetched_at <= end)
    if hour is not None:
        # Filter jam — ambil record yang di-fetch pada jam tertentu (UTC)
        from sqlalchemy import extract
        q = q.where(extract("hour", TrendingTopic.fetched_at) == hour)

    q = q.order_by(desc(TrendingTopic.fetched_at)).offset(offset).limit(limit)
    rows = await db.scalars(q)

    items = [
        {
            "id": str(t.id),
            "rank": t.rank,
            "title": t.title,
            "traffic": t.traffic,
            "description": t.description,
            "geo": t.geo,
            "period": t.period,
            "published_at": t.published_at.isoformat() if t.published_at else None,
            "fetched_at": t.fetched_at.isoformat(),
        }
        for t in rows.all()
    ]
    return build_success_response({
        "geo": geo,
        "period": period,
        "filter": {
            "date_from": str(date_from) if date_from else None,
            "date_to": str(date_to) if date_to else None,
            "hour": hour,
        },
        "total": len(items),
        "offset": offset,
        "items": items,
    })


# ─────────────────────────────────────────────────────────────────────────────
# VIDEOS (URL YouTube + metadata, bukan file video)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/videos", response_model=dict)
async def list_videos(
    keyword_id: uuid.UUID | None = Query(default=None, description="Filter per keyword"),
    date_from: date | None = Query(default=None, description="Filter dari tanggal collect (YYYY-MM-DD)"),
    date_to: date | None = Query(default=None, description="Filter sampai tanggal collect"),
    hour: int | None = Query(default=None, ge=0, le=23, description="Filter jam collect (UTC)"),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    List video YouTube yang sudah di-scrape dan tersimpan di DB.

    Yang disimpan adalah URL video (link YouTube) + metadata:
      - url       : https://youtube.com/watch?v=VIDEO_ID
      - title     : judul video
      - author    : nama channel
      - view_count: jumlah views
      - thumbnail : URL thumbnail
      - keyword   : keyword yang dipakai untuk scraping
      - collected_at: kapan di-scrape

    File video TIDAK disimpan — hanya link & metadata.
    Filter opsional per tanggal/jam kapan video di-collect.
    """
    from sqlalchemy import text

    # Query raw SQL untuk bypass SQLAlchemy label/key mapping issue pada kolom JSONB
    filters = ["p.platform = 'youtube'"]
    params: dict = {"limit": limit, "offset": offset}

    if keyword_id:
        filters.append("p.keyword_id = :keyword_id")
        params["keyword_id"] = str(keyword_id)
    if date_from:
        filters.append("p.collected_at >= :date_from")
        params["date_from"] = datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc)
    if date_to:
        params["date_to"] = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59, tzinfo=timezone.utc)
        filters.append("p.collected_at <= :date_to")
    if hour is not None:
        filters.append("EXTRACT(hour FROM p.collected_at) = :hour")
        params["hour"] = hour

    where_clause = " AND ".join(filters)
    sql = text(f"""
        SELECT
            p.id,
            p.external_id,
            p.content,
            p.author,
            p.url,
            p.keyword_id,
            p.collected_at,
            p.published_at,
            p.metadata,
            k.keyword
        FROM posts p
        LEFT JOIN keywords k ON p.keyword_id = k.id
        WHERE {where_clause}
        ORDER BY p.collected_at DESC
        OFFSET :offset LIMIT :limit
    """)

    rows = await db.execute(sql, params)

    items = []
    for row in rows.mappings().all():
        meta = row["metadata"] or {}
        items.append({
            "id": str(row["id"]),
            "video_id": row["external_id"],
            "url": row["url"] or f"https://youtube.com/watch?v={row['external_id']}",
            "title": row["content"],
            "channel": row["author"],
            "thumbnail_url": meta.get("thumbnail", meta.get("thumbnail_url", "")),
            "view_count": meta.get("views", meta.get("view_count", 0)),
            "description": meta.get("description", ""),
            "duration": meta.get("duration", ""),
            "keyword": row["keyword"],
            "keyword_id": str(row["keyword_id"]) if row["keyword_id"] else None,
            "collected_at": row["collected_at"].isoformat() if row["collected_at"] else None,
            "published_at": row["published_at"].isoformat() if row["published_at"] else None,
        })

    return build_success_response({
        "filter": {
            "keyword_id": str(keyword_id) if keyword_id else None,
            "date_from": str(date_from) if date_from else None,
            "date_to": str(date_to) if date_to else None,
            "hour": hour,
        },
        "total": len(items),
        "offset": offset,
        "note": "url berisi link YouTube. File video tidak disimpan di server.",
        "items": items,
    })


# ─────────────────────────────────────────────────────────────────────────────
# KOMENTAR
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/comments", response_model=dict)
async def list_comments(
    keyword_id: uuid.UUID | None = Query(default=None),
    video_id: uuid.UUID | None = Query(default=None, description="UUID Post (bukan video_id YouTube)"),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    hour: int | None = Query(default=None, ge=0, le=23),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    List komentar yang sudah di-scrape dari YouTube.
    Filter per keyword, per video, atau per rentang tanggal/jam.
    """
    q = select(Comment, Post).join(Post, Comment.post_id == Post.id)

    if video_id:
        q = q.where(Comment.post_id == video_id)
    if keyword_id:
        q = q.where(Post.keyword_id == keyword_id)
    if date_from:
        q = q.where(Comment.created_at >= datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc))
    if date_to:
        end = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59, tzinfo=timezone.utc)
        q = q.where(Comment.created_at <= end)
    if hour is not None:
        from sqlalchemy import extract
        q = q.where(extract("hour", Comment.created_at) == hour)

    q = q.order_by(desc(Comment.created_at)).offset(offset).limit(limit)
    rows = await db.execute(q)

    items = []
    for comment, post in rows.all():
        meta = comment.metadata_ or {}
        items.append({
            "id": str(comment.id),
            "comment_id": comment.external_id,
            "content": comment.content,
            "author": comment.author,
            "like_count": meta.get("like_count", 0),
            "reply_count": meta.get("reply_count", 0),
            "published_time": meta.get("published_time", ""),
            "video_url": post.url or f"https://youtube.com/watch?v={post.external_id}",
            "video_title": post.content,
            "scraped_at": comment.created_at.isoformat(),
        })

    return build_success_response({
        "filter": {
            "keyword_id": str(keyword_id) if keyword_id else None,
            "video_id": str(video_id) if video_id else None,
            "date_from": str(date_from) if date_from else None,
            "date_to": str(date_to) if date_to else None,
            "hour": hour,
        },
        "total": len(items),
        "offset": offset,
        "items": items,
    })


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/dashboard", response_model=dict)
async def dashboard(
    project_id: uuid.UUID | None = Query(default=None),
    date_from: date | None = Query(default=None, description="Hitung stats dari tanggal ini"),
    date_to: date | None = Query(default=None, description="Hitung stats sampai tanggal ini"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Ringkasan dashboard.
    Filter opsional per rentang tanggal — tanpa filter = semua data historis.
    """
    result = await get_dashboard_summary(db, project_id, date_from=date_from, date_to=date_to)
    return build_success_response(result.model_dump())


# ─────────────────────────────────────────────────────────────────────────────
# PIPELINE STATUS
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/status", response_model=dict)
async def pipeline_status(
    keyword_id: uuid.UUID = Query(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Progress pipeline untuk satu keyword: videos → comments → analyzed (coverage %)."""
    result = await get_keyword_pipeline_status(db, keyword_id)
    return build_success_response(result.model_dump())


# ─────────────────────────────────────────────────────────────────────────────
# SENTIMENT ANALYTICS
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/sentiment/distribution", response_model=dict)
async def sentiment_distribution(
    keyword_id: uuid.UUID = Query(...),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Distribusi sentimen positif/negatif/netral per keyword. Filter opsional per tanggal."""
    result = await get_sentiment_distribution(db, keyword_id, date_from=date_from, date_to=date_to)
    return build_success_response(result.model_dump())


@router.get("/sentiment/table", response_model=dict)
async def sentiment_table(
    keyword_id: uuid.UUID = Query(...),
    label: str | None = Query(default=None, description="positif | negatif | netral"),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    hour: int | None = Query(default=None, ge=0, le=23),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Tabel detail sentimen per komentar.
    Filter per label, tanggal, dan jam.
    """
    result = await get_sentiment_table(
        db, keyword_id, label,
        limit=limit, offset=offset,
        date_from=date_from, date_to=date_to, hour=hour,
    )
    return build_success_response(result.model_dump())


# ─────────────────────────────────────────────────────────────────────────────
# WORD CLOUD
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/wordcloud", response_model=dict)
async def wordcloud(
    keyword_id: uuid.UUID = Query(...),
    sentiment: str | None = Query(default=None, description="positif | negatif | netral"),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    top_n: int = Query(default=100, ge=10, le=500),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Frekuensi kata untuk word cloud. Filter per sentimen dan tanggal."""
    result = await get_wordcloud_data(
        db, keyword_id, sentiment, top_n,
        date_from=date_from, date_to=date_to,
    )
    return build_success_response(result.model_dump())
