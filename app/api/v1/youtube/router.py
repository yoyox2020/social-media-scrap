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
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.comments.models import Comment
from app.domain.keywords.models import Keyword
from app.domain.posts.models import Post
from app.domain.trending.models import TrendingTopic
from app.domain.users.models import User
from app.domain.youtube_analysis.models import LexiconAnalysis
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
    SmartSearchRequest,
    TrendingFetchRequest,
    YouTubeCollectRequest,
)
from app.shared.utils import build_success_response

router = APIRouter(prefix="/youtube", tags=["youtube"])


# ─────────────────────────────────────────────────────────────────────────────
# SEARCH (live dari EnsembleData, tidak disimpan ke DB)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/search", response_model=dict)
async def search_youtube(
    q: str = Query(..., min_length=1, max_length=200, description="Kata kunci pencarian"),
    depth: int = Query(default=1, ge=1, le=5, description="Jumlah halaman hasil (~20 video per halaman)"),
    current_user: User = Depends(get_current_user),
):
    """
    Cari video YouTube berdasarkan kata kunci secara langsung (live search).

    Hasil TIDAK disimpan ke DB — ini hanya proxy ke EnsembleData YouTube search.
    Gunakan POST /youtube/collect jika ingin menyimpan hasil ke DB dan analisis komentar.

    - q     : kata kunci pencarian (wajib)
    - depth : jumlah halaman (1 = ~20 video, max 5 = ~100 video)
    """
    from app.integrations.ensemble_data.client import EnsembleDataClient
    from app.integrations.youtube.connector import YouTubeConnector

    async with EnsembleDataClient() as client:
        connector = YouTubeConnector(client)
        raw = await connector.search_by_keyword(keyword=q, depth=depth)

    videos = connector.extract_posts(raw)

    from app.services.processing.normalizer import _parse_relative_time
    import re as _re

    def _parse_views(raw: str) -> int:
        if not raw:
            return 0
        digits = _re.sub(r"[^\d]", "", raw)
        return int(digits) if digits else 0

    now_utc = datetime.now(timezone.utc)

    items = []
    for v in videos:
        video_id = v.get("videoId", "")
        title_runs = v.get("title", {}).get("runs", [])
        title = title_runs[0].get("text", "") if title_runs else v.get("title", "")

        channel_runs = (
            v.get("longBylineText", {}).get("runs", [])
            or v.get("ownerText", {}).get("runs", [])
        )
        channel = channel_runs[0].get("text", "") if channel_runs else ""

        view_count_raw = (
            v.get("viewCountText", {}).get("simpleText", "")
            or v.get("viewCountText", {}).get("runs", [{}])[0].get("text", "")
        )

        published_text = (
            v.get("publishedTimeText", {}).get("simpleText", "")
            or v.get("publishedTimeText", "")
        )
        published_at = _parse_relative_time(published_text, reference=now_utc)

        duration = v.get("lengthText", {}).get("simpleText", "")

        thumbnail_list = v.get("thumbnail", {}).get("thumbnails", [])
        thumbnail = thumbnail_list[-1].get("url", "") if thumbnail_list else ""

        items.append({
            "video_id": video_id,
            "url": f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
            "title": title,
            "channel": channel,
            "view_count": _parse_views(view_count_raw),
            "published_at": published_at.isoformat() if published_at else None,
            "published_text": published_text,
            "duration": duration,
            "thumbnail_url": thumbnail,
        })

    return build_success_response({
        "query": q,
        "depth": depth,
        "total": len(items),
        "note": "Hasil tidak disimpan ke DB. Gunakan POST /youtube/collect untuk simpan & analisis.",
        "items": items,
    })


# ─────────────────────────────────────────────────────────────────────────────
# SMART SEARCH — cek DB dulu, auto-crawl jika belum ada
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/smart-search", response_model=dict)
async def smart_search_youtube(
    body: SmartSearchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Smart search YouTube berdasarkan kata kunci.

    **Behaviour otomatis:**
    - Jika data sudah ada di DB → langsung kembalikan videos + komentar + sentimen
    - Jika data belum ada → buat keyword, jalankan pipeline crawl otomatis di background,
      kembalikan status `crawling` beserta `poll_url` untuk cek progres
    - `force_refresh=true` → crawl ulang meski data sudah ada (tampilkan data lama sambil refresh)

    **Status response:**
    - `ready`      → data ada, langsung bisa dipakai
    - `crawling`   → pipeline baru dimulai, cek `poll_url` beberapa menit lagi
    - `refreshing` → data lama ditampilkan, pipeline refresh berjalan di background
    - `error`      → tidak ada project aktif di DB
    """
    from app.services.youtube.pipeline_service import smart_search_youtube as _smart_search

    result = await _smart_search(
        db=db,
        q=body.q,
        max_pages=body.max_pages,
        max_comments_per_video=body.max_comments_per_video,
        max_comment_pages=body.max_comment_pages,
        force_refresh=body.force_refresh,
    )
    return build_success_response(result)


# ─────────────────────────────────────────────────────────────────────────────
# GET DATA — ambil hasil pencarian dari DB by kata kunci
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/smart-search", response_model=dict)
async def get_search_result(
    q: str = Query(..., min_length=1, max_length=200, description="Kata kunci yang dicari"),
    limit_videos: int = Query(default=20, ge=1, le=100, description="Jumlah video yang dikembalikan"),
    limit_comments: int = Query(default=20, ge=1, le=200, description="Jumlah sample komentar"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Ambil data hasil pencarian dari database berdasarkan kata kunci.

    - Jika keyword ditemukan di DB → return videos + komentar + sentimen langsung
    - Jika belum ada → return 404 dengan saran gunakan POST /smart-search untuk crawl

    Gunakan POST /smart-search untuk crawl pertama kali.
    Gunakan GET /smart-search untuk ambil data yang sudah ada.
    """
    from collections import Counter as _Counter

    q_clean = q.strip().lower()
    # Cari: exact match dulu, lalu stored keyword yang mengandung query, lalu query yang mengandung stored keyword
    keyword = await db.scalar(
        select(Keyword).where(
            func.lower(Keyword.keyword) == q_clean
        ).limit(1)
    )
    if not keyword:
        keyword = await db.scalar(
            select(Keyword).where(
                func.lower(Keyword.keyword).like(f"%{q_clean}%")
            ).limit(1)
        )
    if not keyword:
        # Cari keyword yang kata-katanya semua ada dalam query
        words = q_clean.split()
        from sqlalchemy import and_
        conditions = [func.lower(Keyword.keyword).contains(w) for w in words]
        keyword = await db.scalar(
            select(Keyword).where(and_(*conditions)).limit(1)
        )

    if not keyword:
        return build_success_response({
            "status": "not_found",
            "query": q,
            "message": "Keyword belum ada di database. Gunakan POST /api/v1/youtube/smart-search untuk crawl data baru.",
            "post_endpoint": "POST /api/v1/youtube/smart-search",
            "body_example": {"q": q},
        })

    # Hitung total
    total_videos = (await db.scalar(
        select(func.count(Post.id)).where(
            Post.keyword_id == keyword.id, Post.platform == "youtube"
        )
    )) or 0

    if total_videos == 0:
        return build_success_response({
            "status": "empty",
            "query": q,
            "keyword_id": str(keyword.id),
            "message": "Keyword ada di DB tapi belum ada data video. Gunakan POST /smart-search untuk crawl.",
        })

    # Videos
    posts = list((await db.scalars(
        select(Post)
        .where(Post.keyword_id == keyword.id, Post.platform == "youtube")
        .order_by(Post.collected_at.desc())
        .limit(limit_videos)
    )).all())

    videos = []
    for p in posts:
        meta = p.metadata_ or {}
        raw_views = meta.get("views", meta.get("view_count", 0))
        try:
            view_count = int(str(raw_views).replace(",", "").split()[0]) if raw_views else 0
        except (ValueError, IndexError):
            view_count = 0
        videos.append({
            "id": str(p.id),
            "video_id": p.external_id,
            "url": p.url or f"https://youtube.com/watch?v={p.external_id}",
            "title": p.content,
            "channel": p.author,
            "view_count": view_count,
            "thumbnail_url": meta.get("thumbnail", meta.get("thumbnail_url", "")),
            "published_at": p.published_at.isoformat() if p.published_at else None,
            "collected_at": p.collected_at.isoformat() if p.collected_at else None,
        })

    # Komentar + sentimen
    rows = (await db.execute(
        select(Comment, LexiconAnalysis, Post)
        .join(Post, Comment.post_id == Post.id)
        .outerjoin(LexiconAnalysis, LexiconAnalysis.comment_id == Comment.id)
        .where(Post.keyword_id == keyword.id)
        .order_by(Comment.created_at.desc())
        .limit(limit_comments)
    )).all()

    comments = [
        {
            "id": str(comment.id),
            "content": comment.content,
            "author": comment.author,
            "sentiment": analysis.label if analysis else None,
            "score": round(analysis.score, 3) if analysis else None,
            "video_url": post.url,
        }
        for comment, analysis, post in rows
    ]

    # Distribusi sentimen
    label_rows = list((await db.scalars(
        select(LexiconAnalysis.label).where(LexiconAnalysis.keyword_id == keyword.id)
    )).all())
    counter = _Counter(label_rows)
    total_analyzed = sum(counter.values())

    total_comments = (await db.scalar(
        select(func.count(Comment.id))
        .join(Post, Comment.post_id == Post.id)
        .where(Post.keyword_id == keyword.id)
    )) or 0

    sentiment = {
        lbl: {
            "count": counter.get(lbl, 0),
            "percentage": round(counter.get(lbl, 0) / total_analyzed * 100, 1) if total_analyzed else 0.0,
        }
        for lbl in ["positif", "negatif", "netral"]
    }

    return build_success_response({
        "status": "ready",
        "query": q,
        "keyword_id": str(keyword.id),
        "stats": {
            "total_videos": total_videos,
            "total_comments": total_comments,
            "total_analyzed": total_analyzed,
            "coverage_pct": round(total_analyzed / total_comments * 100, 1) if total_comments else 0.0,
        },
        "sentiment": {**sentiment, "dominant": counter.most_common(1)[0][0] if counter else "netral"},
        "videos": videos,
        "comments": comments,
    })


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
    date_from: date | None = Query(default=None, description="Filter dari tanggal publish video (YYYY-MM-DD)"),
    date_to: date | None = Query(default=None, description="Filter sampai tanggal publish video"),
    hour: int | None = Query(default=None, ge=0, le=23, description="Filter jam collect (UTC)"),
    sort_by: str = Query(default="views", description="Urutan: views (terviral), newest (terbaru), oldest (terlama)"),
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
        filters.append("p.published_at >= :date_from")
        params["date_from"] = datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc)
    if date_to:
        params["date_to"] = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59, tzinfo=timezone.utc)
        filters.append("p.published_at <= :date_to")
    if hour is not None:
        filters.append("EXTRACT(hour FROM p.collected_at) = :hour")
        params["hour"] = hour

    order_clause = {
        "views":  "(p.metadata->>'views')::bigint DESC NULLS LAST",
        "newest": "p.published_at DESC NULLS LAST",
        "oldest": "p.published_at ASC NULLS LAST",
    }.get(sort_by, "(p.metadata->>'views')::bigint DESC NULLS LAST")

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
        ORDER BY {order_clause}
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
# TOP VIRAL VIDEOS
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/videos/viral", response_model=dict)
async def viral_videos(
    limit: int = Query(default=20, ge=1, le=100, description="Jumlah video teratas"),
    keyword_id: uuid.UUID | None = Query(default=None, description="Filter per keyword (opsional)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Top N video YouTube dengan views terbanyak dari semua data yang tersimpan di DB.
    Default menampilkan 20 video paling viral lintas semua keyword.
    """
    from sqlalchemy import text

    filters = ["p.platform = 'youtube'", "p.metadata->>'views' IS NOT NULL"]
    params: dict = {"limit": limit}

    if keyword_id:
        filters.append("p.keyword_id = :keyword_id")
        params["keyword_id"] = str(keyword_id)

    where_clause = " AND ".join(filters)
    sql = text(f"""
        SELECT
            p.id, p.external_id, p.content, p.author, p.url,
            p.published_at, p.metadata, k.keyword,
            (p.metadata->>'views')::bigint AS view_count
        FROM posts p
        LEFT JOIN keywords k ON p.keyword_id = k.id
        WHERE {where_clause}
        ORDER BY (p.metadata->>'views')::bigint DESC NULLS LAST
        LIMIT :limit
    """)

    rows = (await db.execute(sql, params)).mappings().all()

    items = [
        {
            "rank": i + 1,
            "video_id": r["external_id"],
            "url": r["url"] or f"https://youtube.com/watch?v={r['external_id']}",
            "title": r["content"],
            "channel": r["author"],
            "view_count": r["view_count"] or 0,
            "thumbnail_url": (r["metadata"] or {}).get("thumbnail", ""),
            "duration": (r["metadata"] or {}).get("duration", ""),
            "published_at": r["published_at"].isoformat() if r["published_at"] else None,
            "keyword": r["keyword"],
        }
        for i, r in enumerate(rows)
    ]

    return build_success_response({
        "total": len(items),
        "note": "Diurutkan berdasarkan view count tertinggi dari semua data di DB",
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
