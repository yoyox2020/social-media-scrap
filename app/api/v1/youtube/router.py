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
from sqlalchemy import and_, desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.comments.models import Comment
from app.domain.keywords.models import Keyword
from app.domain.posts.models import Post
from app.domain.trending.models import TrendingTopic
from app.domain.users.models import User
from app.domain.viral_tracking.models import FlaggedAccount, ViralChannelTracker
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
    DateSearchRequest,
    SmartSearchRequest,
    TrendingFetchRequest,
    ViralSearchRequest,
    YouTubeCollectRequest,
    YouTubePopularRequest,
)
from app.services.processing.normalizer import _utc_from_iso
from app.shared.utils import build_success_response

router = APIRouter(prefix="/youtube", tags=["youtube"])


# ─────────────────────────────────────────────────────────────────────────────
# KEYWORDS
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/keywords", response_model=dict)
async def list_keywords(
    q: str | None = Query(default=None, max_length=200, description="Filter nama keyword (ILIKE)"),
    is_active: bool | None = Query(default=None, description="Filter aktif/tidak aktif"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Daftar semua keyword beserta jumlah video dan komentar yang sudah di-scrape."""
    filters = ["k.id IS NOT NULL"]
    params: dict = {"limit": limit, "offset": offset}

    if q:
        filters.append("k.keyword ILIKE :q_like")
        params["q_like"] = f"%{q.strip()}%"
    if is_active is not None:
        filters.append("k.is_active = :is_active")
        params["is_active"] = is_active

    where_clause = " AND ".join(filters)

    total: int = (await db.scalar(
        text(f"SELECT COUNT(*) FROM keywords k WHERE {where_clause}"),
        {k: v for k, v in params.items() if k not in ("limit", "offset")},
    )) or 0

    rows = (await db.execute(text(f"""
        SELECT
            k.id,
            k.keyword,
            k.is_active,
            k.created_at,
            COUNT(DISTINCT p.id)  AS video_count,
            COUNT(DISTINCT c.id)  AS comment_count
        FROM keywords k
        LEFT JOIN posts p    ON p.keyword_id = k.id AND p.platform = 'youtube'
        LEFT JOIN comments c ON c.post_id = p.id
        WHERE {where_clause}
        GROUP BY k.id, k.keyword, k.is_active, k.created_at
        ORDER BY video_count DESC, k.created_at DESC
        OFFSET :offset LIMIT :limit
    """), params)).mappings().all()

    items = [
        {
            "id": str(r["id"]),
            "keyword": r["keyword"],
            "is_active": r["is_active"],
            "video_count": r["video_count"],
            "comment_count": r["comment_count"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]
    return build_success_response({"total": total, "offset": offset, "limit": limit, "items": items})


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
    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    total: int = (await db.scalar(
        text(f"SELECT COUNT(*) FROM posts p LEFT JOIN keywords k ON p.keyword_id = k.id WHERE {where_clause}"),
        count_params,
    )) or 0

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
        "total": total,
        "offset": offset,
        "limit": limit,
        "note": "url berisi link YouTube. File video tidak disimpan di server.",
        "items": items,
    })


# ─────────────────────────────────────────────────────────────────────────────
# TOP VIRAL VIDEOS
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/videos/viral", response_model=dict)
async def viral_videos(
    limit: int = Query(default=20, ge=1, le=100, description="Jumlah video teratas"),
    keyword_id: uuid.UUID | None = Query(default=None, description="Filter per keyword UUID (opsional)"),
    q: str | None = Query(default=None, max_length=200, description="Filter nama keyword (ILIKE, opsional — alternatif keyword_id)"),
    limit_comments: int = Query(default=10, ge=0, le=200, description="Jumlah sample komentar (0 = tidak ambil)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Top N video YouTube dengan views terbanyak dari semua data yang tersimpan di DB.
    Default menampilkan 20 video paling viral lintas semua keyword.

    Filter keyword: gunakan `keyword_id` (UUID) ATAU `q` (nama keyword, ILIKE).
    """


    filters = ["p.platform = 'youtube'", "p.metadata->>'views' IS NOT NULL"]
    params: dict = {"limit": limit}

    if keyword_id:
        filters.append("p.keyword_id = :keyword_id")
        params["keyword_id"] = str(keyword_id)
    elif q:
        filters.append("k.keyword ILIKE :q_like")
        params["q_like"] = f"%{q.strip()}%"

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

    comments = []
    if limit_comments > 0:
        c_filters = ["p.platform = 'youtube'"]
        c_params: dict = {"lc": limit_comments}
        if keyword_id:
            c_filters.append("p.keyword_id = :keyword_id_c")
            c_params["keyword_id_c"] = str(keyword_id)
        elif q:
            c_filters.append("k.keyword ILIKE :q_like_c")
            c_params["q_like_c"] = f"%{q.strip()}%"
        c_where = " AND ".join(c_filters)
        comment_rows = (await db.execute(text(f"""
            SELECT c.id, c.content, c.author,
                   la.label AS sentiment, la.score,
                   p.url AS video_url, p.external_id
            FROM comments c
            JOIN posts p ON c.post_id = p.id
            LEFT JOIN lexicon_analyses la ON la.comment_id = c.id
            LEFT JOIN keywords k ON p.keyword_id = k.id
            WHERE {c_where}
            ORDER BY c.created_at DESC
            LIMIT :lc
        """), c_params)).mappings().all()
        comments = [
            {
                "id": str(r["id"]),
                "content": r["content"],
                "author": r["author"],
                "sentiment": r["sentiment"],
                "score": round(float(r["score"]), 3) if r["score"] is not None else None,
                "video_url": r["video_url"] or f"https://www.youtube.com/watch?v={r['external_id']}",
            }
            for r in comment_rows
        ]

    return build_success_response({
        "total": len(items),
        "note": "Diurutkan berdasarkan view count tertinggi dari semua data di DB",
        "filter": {"keyword_id": str(keyword_id) if keyword_id else None, "q": q},
        "comments": comments,
        "items": items,
    })


@router.post("/videos/viral", response_model=dict)
async def viral_videos_post(
    body: ViralSearchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Cari video viral/trending dari **database** dengan filter lengkap (POST version).

    Filter tersedia:
    - `keyword_id` : filter per keyword ID
    - `q`          : filter nama keyword (ILIKE)
    - `date_from`  : filter dari tanggal publish video
    - `date_to`    : filter sampai tanggal publish video
    - `sort_by`    : `views` (terviral) | `newest` | `oldest`
    - `limit`      : jumlah hasil (maks 200)
    - `offset`     : pagination
    """


    filters = ["p.platform = 'youtube'"]
    params: dict = {"limit": body.limit, "offset": body.offset}

    if body.keyword_id:
        filters.append("p.keyword_id = :keyword_id")
        params["keyword_id"] = str(body.keyword_id)
    elif body.q:
        filters.append("k.keyword ILIKE :q_like")
        params["q_like"] = f"%{body.q.strip()}%"

    if body.date_from:
        filters.append("p.published_at >= :date_from")
        params["date_from"] = datetime(body.date_from.year, body.date_from.month, body.date_from.day, tzinfo=timezone.utc)
    if body.date_to:
        filters.append("p.published_at <= :date_to")
        params["date_to"] = datetime(body.date_to.year, body.date_to.month, body.date_to.day, 23, 59, 59, tzinfo=timezone.utc)

    if body.sort_by == "views":
        filters.append("p.metadata->>'views' IS NOT NULL")

    order = {
        "views":  "(p.metadata->>'views')::bigint DESC NULLS LAST",
        "newest": "p.published_at DESC NULLS LAST",
        "oldest": "p.published_at ASC NULLS LAST",
    }.get(body.sort_by, "(p.metadata->>'views')::bigint DESC NULLS LAST")

    where_clause = " AND ".join(filters)

    count_sql = text(f"""
        SELECT COUNT(*) FROM posts p
        LEFT JOIN keywords k ON p.keyword_id = k.id
        WHERE {where_clause}
    """)
    total = (await db.execute(count_sql, {k: v for k, v in params.items() if k not in ("limit", "offset")})).scalar() or 0

    sql = text(f"""
        SELECT
            p.id, p.external_id, p.content, p.author, p.url,
            p.published_at, p.collected_at, p.metadata,
            k.keyword, k.id AS keyword_id,
            (p.metadata->>'views')::bigint AS view_count
        FROM posts p
        LEFT JOIN keywords k ON p.keyword_id = k.id
        WHERE {where_clause}
        ORDER BY {order}
        OFFSET :offset LIMIT :limit
    """)
    rows = (await db.execute(sql, params)).mappings().all()

    items = [
        {
            "rank":          i + 1 + body.offset,
            "video_id":      r["external_id"],
            "url":           r["url"] or f"https://youtube.com/watch?v={r['external_id']}",
            "title":         r["content"],
            "channel":       r["author"],
            "view_count":    r["view_count"] or 0,
            "thumbnail_url": (r["metadata"] or {}).get("thumbnail", (r["metadata"] or {}).get("thumbnail_url", "")),
            "duration":      (r["metadata"] or {}).get("duration", ""),
            "published_at":  r["published_at"].isoformat() if r["published_at"] else None,
            "collected_at":  r["collected_at"].isoformat() if r["collected_at"] else None,
            "keyword":       r["keyword"],
            "keyword_id":    str(r["keyword_id"]) if r["keyword_id"] else None,
        }
        for i, r in enumerate(rows)
    ]

    # ── Komentar ──────────────────────────────────────────────────────────────
    comments = []
    if body.limit_comments > 0 and total > 0:
        c_filters = ["p.platform = 'youtube'"]
        c_params: dict = {"lc": body.limit_comments}
        if body.keyword_id:
            c_filters.append("p.keyword_id = :keyword_id_c")
            c_params["keyword_id_c"] = str(body.keyword_id)
        elif body.q:
            c_filters.append("k.keyword ILIKE :q_like_c")
            c_params["q_like_c"] = f"%{body.q.strip()}%"
        if body.date_from:
            c_filters.append("p.published_at >= :date_from_c")
            c_params["date_from_c"] = datetime(body.date_from.year, body.date_from.month, body.date_from.day, tzinfo=timezone.utc)
        if body.date_to:
            c_filters.append("p.published_at <= :date_to_c")
            c_params["date_to_c"] = datetime(body.date_to.year, body.date_to.month, body.date_to.day, 23, 59, 59, tzinfo=timezone.utc)
        c_where = " AND ".join(c_filters)
        comment_rows = (await db.execute(text(f"""
            SELECT c.id, c.content, c.author,
                   la.label AS sentiment, la.score,
                   p.url AS video_url, p.external_id
            FROM comments c
            JOIN posts p ON c.post_id = p.id
            LEFT JOIN lexicon_analyses la ON la.comment_id = c.id
            LEFT JOIN keywords k ON p.keyword_id = k.id
            WHERE {c_where}
            ORDER BY c.created_at DESC
            LIMIT :lc
        """), c_params)).mappings().all()
        comments = [
            {
                "id": str(r["id"]),
                "content": r["content"],
                "author": r["author"],
                "sentiment": r["sentiment"],
                "score": round(float(r["score"]), 3) if r["score"] is not None else None,
                "video_url": r["video_url"] or f"https://www.youtube.com/watch?v={r['external_id']}",
            }
            for r in comment_rows
        ]

    # ── Fallback ke YouTube Data API v3 jika DB kosong ────────────────────────
    if total == 0 and body.auto_search:
        from app.shared.config import settings
        from app.integrations.youtube_data_api.client import YouTubeDataAPIClient

        if settings.youtube_data_api_key:
            yt = YouTubeDataAPIClient(api_key=settings.youtube_data_api_key)

            if body.q:
                # Cari berdasarkan keyword, diurutkan view terbanyak
                raw = await yt.search_videos(body.q, max_results=body.limit, order="viewCount")
                yt_items_raw = raw.get("data", {}).get("items") or []
                yt_items = []
                for i, it in enumerate(yt_items_raw):
                    vid_id = (it.get("id") or {}).get("videoId") if isinstance(it.get("id"), dict) else None
                    if not vid_id:
                        continue
                    snip = it.get("snippet") or {}
                    thumbs = snip.get("thumbnails") or {}
                    thumb_url = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
                    yt_items.append({
                        "rank":          i + 1,
                        "video_id":      vid_id,
                        "url":           f"https://www.youtube.com/watch?v={vid_id}",
                        "title":         snip.get("title", ""),
                        "channel":       snip.get("channelTitle", ""),
                        "view_count":    0,
                        "thumbnail_url": thumb_url,
                        "duration":      "",
                        "published_at":  snip.get("publishedAt"),
                        "collected_at":  None,
                        "keyword":       body.q,
                        "keyword_id":    None,
                    })
                return build_success_response({
                    "source":  "youtube_data_api_v3",
                    "note":    f"Data tidak ditemukan di DB — hasil langsung dari YouTube search (order=viewCount) untuk '{body.q}'",
                    "sort_by": "viewCount",
                    "filter": {
                        "keyword_id": str(body.keyword_id) if body.keyword_id else None,
                        "q":          body.q,
                        "date_from":  str(body.date_from) if body.date_from else None,
                        "date_to":    str(body.date_to) if body.date_to else None,
                    },
                    "total":  len(yt_items),
                    "offset": body.offset,
                    "limit":  body.limit,
                    "items":  yt_items,
                })
            else:
                # Tanpa keyword → ambil mostPopular chart
                raw = await yt.fetch_popular(region_code="ID", max_results=body.limit)
                yt_items_raw = raw.get("items") or []
                yt_items = [_format_popular_item(it, i + 1) for i, it in enumerate(yt_items_raw)]
                return build_success_response({
                    "source":  "youtube_data_api_v3",
                    "note":    "Data tidak ditemukan di DB — hasil langsung dari YouTube mostPopular chart (ID)",
                    "sort_by": "mostPopular",
                    "filter": {
                        "keyword_id": None,
                        "q":          None,
                        "date_from":  str(body.date_from) if body.date_from else None,
                        "date_to":    str(body.date_to) if body.date_to else None,
                    },
                    "total":  len(yt_items),
                    "offset": body.offset,
                    "limit":  body.limit,
                    "items":  yt_items,
                })

    return build_success_response({
        "sort_by": body.sort_by,
        "filter": {
            "keyword_id": str(body.keyword_id) if body.keyword_id else None,
            "q":          body.q,
            "date_from":  str(body.date_from) if body.date_from else None,
            "date_to":    str(body.date_to) if body.date_to else None,
        },
        "total":  total,
        "offset": body.offset,
        "limit":  body.limit,
        "comments": comments,
        "items":  items,
    })


# ─────────────────────────────────────────────────────────────────────────────
# YOUTUBE POPULAR + DATE SEARCH — harus sebelum /{video_id}
# ─────────────────────────────────────────────────────────────────────────────

def _format_popular_item(item: dict, rank: int) -> dict:
    """Normalisasi satu item dari YouTube Data API v3 videos.list response."""
    snippet = item.get("snippet") or {}
    stats   = item.get("statistics") or {}
    content = item.get("contentDetails") or {}
    video_id = item.get("id", "")
    thumbs   = snippet.get("thumbnails") or {}
    thumb_url = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
    return {
        "rank":         rank,
        "video_id":     video_id,
        "url":          f"https://www.youtube.com/watch?v={video_id}",
        "title":        snippet.get("title", ""),
        "channel":      snippet.get("channelTitle", ""),
        "channel_id":   snippet.get("channelId", ""),
        "description":  snippet.get("description", ""),
        "thumbnail_url": thumb_url,
        "published_at": snippet.get("publishedAt"),
        "duration":     content.get("duration", ""),
        "view_count":   int(stats.get("viewCount", 0) or 0),
        "like_count":   int(stats.get("likeCount", 0) or 0),
        "comment_count": int(stats.get("commentCount", 0) or 0),
    }


@router.get("/videos/popular", response_model=dict)
async def get_popular_videos(
    region_code: str = Query(default="ID", max_length=10, description="Kode negara (ISO 3166-1 alpha-2), misal: ID, US, JP"),
    limit: int = Query(default=20, ge=1, le=50, description="Jumlah video (maks 50)"),
    category_id: str | None = Query(default=None, description="ID kategori YouTube (opsional, misal: '10' untuk musik)"),
    current_user: User = Depends(get_current_user),
):
    """
    Ambil video paling populer di YouTube secara **live** dari YouTube Data API v3.

    Sumber: `GET https://www.googleapis.com/youtube/v3/videos?chart=mostPopular`

    Data **tidak** disimpan ke DB — gunakan POST `/videos/popular` untuk simpan ke DB.
    """
    from app.shared.config import settings
    from app.integrations.youtube_data_api.client import YouTubeDataAPIClient

    if not settings.youtube_data_api_key:
        from app.shared.exceptions import AppException
        raise AppException(code="CONFIG_ERROR", message="YOUTUBE_DATA_API_KEY belum dikonfigurasi", status_code=503)

    client = YouTubeDataAPIClient(api_key=settings.youtube_data_api_key)
    raw = await client.fetch_popular(region_code=region_code, max_results=limit, category_id=category_id)

    items_raw = raw.get("items") or []
    items = [_format_popular_item(item, i + 1) for i, item in enumerate(items_raw)]

    return build_success_response({
        "source":      "youtube_data_api_v3",
        "region_code": region_code,
        "total":       len(items),
        "items":       items,
    })


@router.get("/videos/date-search", response_model=dict)
async def date_range_search(
    date_from: date = Query(..., description="Tanggal mulai (YYYY-MM-DD), inklusif"),
    date_to: date = Query(..., description="Tanggal akhir (YYYY-MM-DD), inklusif"),
    q: str | None = Query(default=None, max_length=200, description="Filter kata kunci (opsional, tanpa filter = semua keyword)"),
    keyword_id: uuid.UUID | None = Query(default=None, description="Filter per keyword ID (opsional)"),
    sort_by: str = Query(default="newest", description="Urutan: newest (terbaru), oldest (terlama), views (terviral)"),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    limit_comments: int = Query(default=50, ge=0, le=500, description="Jumlah sample komentar (0 = tidak ambil)"),
    include_sentiment: bool = Query(default=True, description="Sertakan distribusi sentimen komentar dalam rentang tanggal ini"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Cari video YouTube dari DB berdasarkan **rentang tanggal publish** video.

    Parameter wajib:
    - `date_from` : tanggal mulai (YYYY-MM-DD)
    - `date_to`   : tanggal akhir (YYYY-MM-DD), inklusif

    Parameter opsional:
    - `q`          : filter kata kunci (cari di nama keyword, LIKE match)
    - `keyword_id` : filter per keyword ID (lebih presisi dari `q`)
    - `sort_by`    : newest / oldest / views
    - `include_sentiment` : sertakan distribusi sentimen & breakdown per hari

    **Catatan:** filter tanggal berlaku pada `published_at` video (bukan `collected_at`).
    Video yang di-scrape hari ini tapi di-publish 3 bulan lalu akan muncul jika
    rentang tanggal mencakup tanggal publishnya.
    """
    if date_from > date_to:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="date_from tidak boleh lebih besar dari date_to")

    dt_from = datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc)
    dt_to   = datetime(date_to.year,   date_to.month,   date_to.day,   23, 59, 59, tzinfo=timezone.utc)

    filters = [
        "p.platform = 'youtube'",
        "p.published_at >= :dt_from",
        "p.published_at <= :dt_to",
    ]
    params: dict = {
        "dt_from": dt_from,
        "dt_to":   dt_to,
        "limit":   limit,
        "offset":  offset,
    }

    if keyword_id:
        filters.append("p.keyword_id = :keyword_id")
        params["keyword_id"] = str(keyword_id)
    elif q:
        filters.append("k.keyword ILIKE :q_like")
        params["q_like"] = f"%{q.strip()}%"

    order_clause = {
        "newest": "p.published_at DESC NULLS LAST",
        "oldest": "p.published_at ASC NULLS LAST",
        "views":  "(p.metadata->>'views')::bigint DESC NULLS LAST",
    }.get(sort_by, "p.published_at DESC NULLS LAST")

    where_clause = " AND ".join(filters)

    sql_videos = text(f"""
        SELECT
            p.id, p.external_id, p.content, p.author, p.url,
            p.keyword_id, p.collected_at, p.published_at, p.metadata,
            k.keyword, k.id AS kw_id
        FROM posts p
        LEFT JOIN keywords k ON p.keyword_id = k.id
        WHERE {where_clause}
        ORDER BY {order_clause}
        OFFSET :offset LIMIT :limit
    """)
    sql_count = text(f"""
        SELECT COUNT(*) FROM posts p
        LEFT JOIN keywords k ON p.keyword_id = k.id
        WHERE {where_clause}
    """)

    rows        = (await db.execute(sql_videos, params)).mappings().all()
    total_count = (await db.execute(sql_count, {k: v for k, v in params.items() if k not in ("limit", "offset")})).scalar() or 0

    items = []
    for row in rows:
        meta = row["metadata"] or {}
        raw_views = meta.get("views", meta.get("view_count", 0))
        try:
            view_count = int(str(raw_views).replace(",", "").split()[0]) if raw_views else 0
        except (ValueError, IndexError):
            view_count = 0
        items.append({
            "id":            str(row["id"]),
            "video_id":      row["external_id"],
            "url":           row["url"] or f"https://youtube.com/watch?v={row['external_id']}",
            "title":         row["content"],
            "channel":       row["author"],
            "view_count":    view_count,
            "thumbnail_url": meta.get("thumbnail", meta.get("thumbnail_url", "")),
            "duration":      meta.get("duration", ""),
            "keyword":       row["keyword"],
            "keyword_id":    str(row["kw_id"]) if row["kw_id"] else None,
            "published_at":  row["published_at"].isoformat() if row["published_at"] else None,
            "collected_at":  row["collected_at"].isoformat() if row["collected_at"] else None,
        })

    comments = []
    if limit_comments > 0:
        kw_filter_c = ""
        params_c: dict = {"dt_from": dt_from, "dt_to": dt_to, "lc": limit_comments}
        if keyword_id:
            kw_filter_c = "AND p.keyword_id = :kw_id_c"
            params_c["kw_id_c"] = str(keyword_id)
        elif q:
            kw_filter_c = "AND k.keyword ILIKE :q_like_c"
            params_c["q_like_c"] = f"%{q.strip()}%"
        comment_rows = (await db.execute(text(f"""
            SELECT c.id, c.content, c.author,
                   la.label AS sentiment, la.score,
                   p.url AS video_url, p.external_id
            FROM comments c
            JOIN posts p ON c.post_id = p.id
            LEFT JOIN lexicon_analyses la ON la.comment_id = c.id
            LEFT JOIN keywords k ON p.keyword_id = k.id
            WHERE p.platform = 'youtube'
              AND p.published_at >= :dt_from AND p.published_at <= :dt_to
              {kw_filter_c}
            ORDER BY c.created_at DESC
            LIMIT :lc
        """), params_c)).mappings().all()
        comments = [
            {
                "id":        str(r["id"]),
                "content":   r["content"],
                "author":    r["author"],
                "sentiment": r["sentiment"],
                "score":     round(float(r["score"]), 3) if r["score"] is not None else None,
                "video_url": r["video_url"] or f"https://www.youtube.com/watch?v={r['external_id']}",
            }
            for r in comment_rows
        ]

    result: dict = {
        "filter": {
            "date_from":  str(date_from),
            "date_to":    str(date_to),
            "q":          q,
            "keyword_id": str(keyword_id) if keyword_id else None,
            "sort_by":    sort_by,
        },
        "total":  total_count,
        "offset": offset,
        "limit":  limit,
        "items":  items,
        "comments": comments,
    }

    if include_sentiment:
        kw_filter_sent = ""
        params_sent: dict = {"dt_from": dt_from, "dt_to": dt_to}
        if keyword_id:
            kw_filter_sent = "AND p.keyword_id = :keyword_id"
            params_sent["keyword_id"] = str(keyword_id)
        elif q:
            kw_filter_sent = "AND k.keyword ILIKE :q_like"
            params_sent["q_like"] = f"%{q.strip()}%"

        sent_rows = (await db.execute(text(f"""
            SELECT la.label, COUNT(*) AS cnt
            FROM lexicon_analyses la
            JOIN comments c ON la.comment_id = c.id
            JOIN posts p    ON c.post_id = p.id
            LEFT JOIN keywords k ON p.keyword_id = k.id
            WHERE p.platform = 'youtube'
              AND p.published_at >= :dt_from AND p.published_at <= :dt_to
              {kw_filter_sent}
            GROUP BY la.label
        """), params_sent)).mappings().all()
        dist: dict[str, int] = {r["label"]: r["cnt"] for r in sent_rows}
        total_analyzed = sum(dist.values())

        sentiment_summary = {
            lbl: {
                "count":      dist.get(lbl, 0),
                "percentage": round(dist.get(lbl, 0) / total_analyzed * 100, 1) if total_analyzed else 0.0,
            }
            for lbl in ["positif", "negatif", "netral"]
        }
        dominant = max(dist, key=dist.get) if dist else "netral"

        daily_rows = (await db.execute(text(f"""
            SELECT DATE(p.published_at AT TIME ZONE 'UTC') AS day, COUNT(*) AS video_count
            FROM posts p
            LEFT JOIN keywords k ON p.keyword_id = k.id
            WHERE p.platform = 'youtube'
              AND p.published_at >= :dt_from AND p.published_at <= :dt_to
              {kw_filter_sent}
            GROUP BY day ORDER BY day ASC
        """), params_sent)).mappings().all()

        result["sentiment"] = {
            **sentiment_summary,
            "dominant":       dominant,
            "total_analyzed": total_analyzed,
        }
        result["daily_breakdown"] = [
            {"date": str(r["day"]), "video_count": r["video_count"]} for r in daily_rows
        ]

    return build_success_response(result)


# ─────────────────────────────────────────────────────────────────────────────
# VIDEO DETAIL — harus setelah /videos/viral, /videos/popular, /videos/date-search
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/videos/{video_id}", response_model=dict)
async def get_video_detail(
    video_id: str,
    limit_comments: int = Query(default=20, ge=0, le=200, description="Jumlah komentar (0 = tidak ambil)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Detail satu video YouTube — bisa pakai UUID post DB atau YouTube video_id (mis. dQw4w9WgXcQ).
    Menyertakan komentar beserta sentimen.
    """
    try:
        post_uuid = uuid.UUID(video_id)
        post = await db.get(Post, post_uuid)
    except ValueError:
        post = await db.scalar(
            select(Post).where(Post.external_id == video_id, Post.platform == "youtube").limit(1)
        )

    if not post:
        from app.shared.exceptions import NotFoundError
        raise NotFoundError("Video", video_id)

    meta = post.metadata_ or {}

    comments = []
    if limit_comments > 0:
        rows = (await db.execute(text("""
            SELECT c.id, c.external_id, c.content, c.author, c.created_at, c.metadata,
                   la.label AS sentiment, la.score AS sentiment_score
            FROM comments c
            LEFT JOIN lexicon_analyses la ON la.comment_id = c.id
            WHERE c.post_id = :post_id
            ORDER BY c.created_at DESC
            LIMIT :lc
        """), {"post_id": str(post.id), "lc": limit_comments})).mappings().all()

        for r in rows:
            cm = r["metadata"] or {}
            comments.append({
                "id": str(r["id"]),
                "comment_id": r["external_id"],
                "content": r["content"],
                "author": r["author"],
                "sentiment": r["sentiment"],
                "sentiment_score": round(float(r["sentiment_score"]), 3) if r["sentiment_score"] is not None else None,
                "like_count": cm.get("like_count", 0),
                "reply_count": cm.get("reply_count", 0),
                "author_channel_id": cm.get("author_channel_id"),
                "published_time": cm.get("published_time", ""),
                "scraped_at": r["created_at"].isoformat() if r["created_at"] else None,
            })

    total_comments: int = (await db.scalar(
        text("SELECT COUNT(*) FROM comments WHERE post_id = :pid"), {"pid": str(post.id)}
    )) or 0

    return build_success_response({
        "id": str(post.id),
        "video_id": post.external_id,
        "url": post.url or f"https://youtube.com/watch?v={post.external_id}",
        "title": post.content,
        "channel": post.author,
        "view_count": meta.get("views", 0),
        "like_count": meta.get("likes", 0),
        "description": meta.get("description", ""),
        "thumbnail_url": meta.get("thumbnail", ""),
        "duration": meta.get("duration", ""),
        "source": meta.get("source", ""),
        "keyword_id": str(post.keyword_id) if post.keyword_id else None,
        "published_at": post.published_at.isoformat() if post.published_at else None,
        "collected_at": post.collected_at.isoformat() if post.collected_at else None,
        "total_comments_in_db": total_comments,
        "comments": comments,
    })


@router.post("/videos/popular", response_model=dict)
async def crawl_popular_videos(
    body: YouTubePopularRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Ambil video populer YouTube via YouTube Data API v3 dan **simpan ke DB**.

    - Membuat keyword `_popular_{region_code}` otomatis jika belum ada
    - Deduplication: video yang sudah ada di DB dilewati
    - Kembalikan jumlah video baru yang berhasil disimpan
    """
    from datetime import datetime as _dt
    from app.shared.config import settings
    from app.integrations.youtube_data_api.client import YouTubeDataAPIClient
    from app.domain.projects.models import Project
    from app.repositories.post_repository import PostRepository

    if not settings.youtube_data_api_key:
        from app.shared.exceptions import AppException
        raise AppException(code="CONFIG_ERROR", message="YOUTUBE_DATA_API_KEY belum dikonfigurasi", status_code=503)

    # Ambil data dari YouTube API
    client = YouTubeDataAPIClient(api_key=settings.youtube_data_api_key)
    raw = await client.fetch_popular(
        region_code=body.region_code,
        max_results=body.limit,
        category_id=body.category_id,
    )
    items_raw = raw.get("items") or []
    items = [_format_popular_item(item, i + 1) for i, item in enumerate(items_raw)]

    saved_count = 0
    keyword_id_used = None

    if body.save_to_db and items:
        # Cari / buat keyword khusus trending populer per region
        kw_text = f"_popular_{body.region_code.upper()}"
        keyword = await db.scalar(
            select(Keyword).where(func.lower(Keyword.keyword) == kw_text.lower()).limit(1)
        )
        if not keyword:
            project_id = await db.scalar(
                select(Project.id).where(Project.is_active == True).limit(1)  # noqa: E712
            )
            if not project_id:
                from app.domain.users.models import User as UserModel
                first_user = await db.scalar(select(UserModel.id).limit(1))
                proj = Project(user_id=first_user, name=f"YouTube Popular {body.region_code}", is_active=True)
                db.add(proj)
                await db.flush()
                project_id = proj.id

            keyword = Keyword(project_id=project_id, keyword=kw_text, is_active=True)
            db.add(keyword)
            await db.flush()
            await db.commit()

        keyword_id_used = keyword.id

        # Deduplication + simpan ke DB
        post_repo = PostRepository(db)
        ext_ids = [it["video_id"] for it in items if it["video_id"]]
        existing = await post_repo.get_existing_external_ids(ext_ids, "youtube")

        new_posts = []
        for it in items:
            if it["video_id"] in existing:
                continue
            new_posts.append(Post(
                id=uuid.uuid4(),
                keyword_id=keyword.id,
                external_id=it["video_id"],
                platform="youtube",
                content=it["title"],
                author=it["channel"],
                url=it["url"],
                metadata_={
                    "views":       it["view_count"],
                    "likes":       it["like_count"],
                    "comments":    it["comment_count"],
                    "description": it["description"],
                    "thumbnail":   it["thumbnail_url"],
                    "duration":    it["duration"],
                    "source":      "youtube_data_api_popular",
                    "region_code": body.region_code,
                },
                raw_data={"_popular": True, "region_code": body.region_code},
                published_at=_utc_from_iso(it["published_at"]),
                collected_at=_dt.now(timezone.utc),
            ))

        if new_posts:
            saved_count = await post_repo.bulk_create(new_posts)
            await db.commit()

    return build_success_response({
        "source":       "youtube_data_api_v3",
        "region_code":  body.region_code,
        "total_fetched": len(items),
        "saved_to_db":  saved_count,
        "keyword_id":   str(keyword_id_used) if keyword_id_used else None,
        "items":        items,
    })


# ─────────────────────────────────────────────────────────────────────────────
# DATE RANGE SEARCH — cari video dari DB berdasarkan kata kunci + rentang tanggal
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/videos/date-search", response_model=dict)
async def date_range_search_post(
    body: DateSearchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Cari video YouTube berdasarkan **rentang tanggal publish** + keyword (versi POST).

    **Behaviour otomatis (`auto_crawl: true`, default):**
    - Data sudah ada di DB → langsung kembalikan hasil (`status: ready`)
    - Data belum ada + `q` diisi → crawl YouTube dulu, lalu filter by tanggal (`status: crawled`)
    - Data belum ada + tanpa `q` → kembalikan kosong (`status: empty`)

    **Catatan penting:**
    YouTube API tidak bisa filter by tanggal — crawl mengambil video terbaru/relevan,
    lalu hasilnya difilter dari DB berdasarkan `published_at` video.

    ```json
    {
      "date_from": "2025-01-01",
      "date_to":   "2025-12-31",
      "q":         "banjir jakarta",
      "sort_by":   "views",
      "auto_crawl": true
    }
    ```
    """

    from app.services.youtube.pipeline_service import collect_comments_for_video

    dt_from = datetime(body.date_from.year, body.date_from.month, body.date_from.day, tzinfo=timezone.utc)
    dt_to   = datetime(body.date_to.year,   body.date_to.month,   body.date_to.day,   23, 59, 59, tzinfo=timezone.utc)

    # ── Helper: query videos dari DB dengan filter tanggal ────────────────────
    async def _query_db(kw_id: uuid.UUID | None = None, q_like: str | None = None) -> tuple[list, int]:
        filters = ["p.platform = 'youtube'", "p.published_at >= :dt_from", "p.published_at <= :dt_to"]
        p: dict = {"dt_from": dt_from, "dt_to": dt_to, "limit": body.limit, "offset": body.offset}

        if kw_id:
            filters.append("p.keyword_id = :kw_id")
            p["kw_id"] = str(kw_id)
        elif q_like:
            filters.append("k.keyword ILIKE :q_like")
            p["q_like"] = q_like

        order = {
            "newest": "p.published_at DESC NULLS LAST",
            "oldest": "p.published_at ASC NULLS LAST",
            "views":  "(p.metadata->>'views')::bigint DESC NULLS LAST",
        }.get(body.sort_by, "p.published_at DESC NULLS LAST")

        where = " AND ".join(filters)
        rows = (await db.execute(text(f"""
            SELECT p.id, p.external_id, p.content, p.author, p.url,
                   p.keyword_id, p.collected_at, p.published_at, p.metadata,
                   k.keyword, k.id AS kw_id
            FROM posts p LEFT JOIN keywords k ON p.keyword_id = k.id
            WHERE {where} ORDER BY {order} OFFSET :offset LIMIT :limit
        """), p)).mappings().all()

        total = (await db.execute(text(f"""
            SELECT COUNT(*) FROM posts p LEFT JOIN keywords k ON p.keyword_id = k.id WHERE {where}
        """), {k: v for k, v in p.items() if k not in ("limit", "offset")})).scalar() or 0

        return list(rows), total

    # ── q adalah pencarian utama; keyword_id hanya filter DB tambahan ────────
    q_like = f"%{body.q.strip()}%" if body.q else None

    # Keyword yang cocok dengan q di DB (dipakai untuk crawl)
    kw_from_q = None
    if body.q:
        kw_from_q = await db.scalar(
            select(Keyword).where(func.lower(Keyword.keyword) == body.q.strip().lower()).limit(1)
        )

    # filter_kw_id: hanya untuk mempersempit hasil DB
    # Pakai keyword_id dari body jika valid, lalu fallback ke keyword dari q
    filter_kw_id: uuid.UUID | None = None
    if body.keyword_id:
        kw_id_valid = await db.scalar(
            select(Keyword.id).where(Keyword.id == body.keyword_id).limit(1)
        )
        if kw_id_valid:
            filter_kw_id = body.keyword_id
    if not filter_kw_id and kw_from_q:
        filter_kw_id = kw_from_q.id

    # ── Cek data di DB ────────────────────────────────────────────────────────
    rows, total_count = await _query_db(filter_kw_id, q_like if not filter_kw_id else None)
    crawl_status = "ready"
    crawl_message = None
    crawled_new = 0

    # ── Auto-crawl jika kosong + ada q (crawl selalu berdasarkan q) ──────────
    if total_count == 0 and body.auto_crawl and body.q:
        from app.domain.projects.models import Project
        from app.repositories.keyword_repository import KeywordRepository
        from app.services.collector.service import CollectorService

        # crawl_kw_id selalu dari q, bukan dari keyword_id body
        crawl_kw = kw_from_q
        crawl_kw_id = crawl_kw.id if crawl_kw else None

        # Buat keyword baru dari q jika belum ada
        if not crawl_kw:
            project_id = await db.scalar(
                select(Project.id).where(Project.is_active == True).limit(1)  # noqa: E712
            )
            if not project_id:
                from app.domain.users.models import User as UserModel
                first_user = await db.scalar(select(UserModel.id).limit(1))
                default_project = Project(
                    user_id=first_user,
                    name="Default Project",
                    description="Auto-created",
                    is_active=True,
                )
                db.add(default_project)
                await db.flush()
                project_id = default_project.id
                await db.commit()

            new_kw = Keyword(project_id=project_id, keyword=body.q.strip(), is_active=True)
            db.add(new_kw)
            await db.flush()
            crawl_kw_id = new_kw.id
            await db.commit()

        # Crawl video berdasarkan q
        kw_repo = KeywordRepository(db)
        svc = CollectorService(kw_repo)
        collect_result = await svc.collect_for_platform(
            keyword_id=crawl_kw_id,
            platform="youtube",
            max_pages=1,
            max_results=5,
        )
        crawled_new = collect_result.new_posts

        if collect_result.errors:
            import logging as _logging
            _logging.getLogger(__name__).warning("date_search crawl errors: %s", collect_result.errors)

        # Collect komentar untuk 3 video teratas
        db.expire_all()
        fresh_posts = list((await db.scalars(
            select(Post)
            .where(Post.keyword_id == crawl_kw_id, Post.platform == "youtube")
            .order_by(Post.collected_at.desc())
            .limit(3)
        )).all())

        for post in fresh_posts:
            try:
                await collect_comments_for_video(
                    db=db, post_id=post.id, keyword_id=crawl_kw_id,
                    max_comments=20, max_pages=1,
                )
            except Exception:
                pass

        db.expire_all()

        # Query ulang setelah crawl (pakai crawl_kw_id agar hasil konsisten)
        rows, total_count = await _query_db(crawl_kw_id, None)
        crawl_status  = "crawled"
        crawl_message = (
            f"Data belum ada — crawl {crawled_new} video baru dari YouTube. "
            f"Ditemukan {total_count} video dalam rentang {body.date_from} s/d {body.date_to}."
        )

    elif total_count == 0 and not body.q:
        crawl_status = "empty"

    # ── Build items ───────────────────────────────────────────────────────────
    items = []
    for row in rows:
        meta = row["metadata"] or {}
        raw_views = meta.get("views", meta.get("view_count", 0))
        try:
            view_count = int(str(raw_views).replace(",", "").split()[0]) if raw_views else 0
        except (ValueError, IndexError):
            view_count = 0
        items.append({
            "id":            str(row["id"]),
            "video_id":      row["external_id"],
            "url":           row["url"] or f"https://youtube.com/watch?v={row['external_id']}",
            "title":         row["content"],
            "channel":       row["author"],
            "view_count":    view_count,
            "thumbnail_url": meta.get("thumbnail", meta.get("thumbnail_url", "")),
            "duration":      meta.get("duration", ""),
            "keyword":       row["keyword"],
            "keyword_id":    str(row["kw_id"]) if row["kw_id"] else None,
            "published_at":  row["published_at"].isoformat() if row["published_at"] else None,
            "collected_at":  row["collected_at"].isoformat() if row["collected_at"] else None,
        })

    # ── Komentar ──────────────────────────────────────────────────────────────
    comments = []
    if body.limit_comments > 0:
        kw_filter_c = ""
        params_c: dict = {"dt_from": dt_from, "dt_to": dt_to, "lc": body.limit_comments}
        if filter_kw_id:
            kw_filter_c = "AND p.keyword_id = :kw_id_c"
            params_c["kw_id_c"] = str(filter_kw_id)
        elif q_like:
            kw_filter_c = "AND k.keyword ILIKE :q_like_c"
            params_c["q_like_c"] = q_like
        comment_rows = (await db.execute(text(f"""
            SELECT c.id, c.content, c.author,
                   la.label AS sentiment, la.score,
                   p.url AS video_url, p.external_id
            FROM comments c
            JOIN posts p ON c.post_id = p.id
            LEFT JOIN lexicon_analyses la ON la.comment_id = c.id
            LEFT JOIN keywords k ON p.keyword_id = k.id
            WHERE p.platform = 'youtube'
              AND p.published_at >= :dt_from AND p.published_at <= :dt_to
              {kw_filter_c}
            ORDER BY c.created_at DESC
            LIMIT :lc
        """), params_c)).mappings().all()
        comments = [
            {
                "id":        str(r["id"]),
                "content":   r["content"],
                "author":    r["author"],
                "sentiment": r["sentiment"],
                "score":     round(float(r["score"]), 3) if r["score"] is not None else None,
                "video_url": r["video_url"] or f"https://www.youtube.com/watch?v={r['external_id']}",
            }
            for r in comment_rows
        ]

    result: dict = {
        "status":  crawl_status,
        "message": crawl_message,
        "filter": {
            "date_from":  str(body.date_from),
            "date_to":    str(body.date_to),
            "q":          body.q,
            "keyword_id": str(filter_kw_id) if filter_kw_id else None,
            "sort_by":    body.sort_by,
        },
        "total":  total_count,
        "offset": body.offset,
        "limit":  body.limit,
        "items":  items,
        "comments": comments,
    }

    # ── Sentiment + daily breakdown ───────────────────────────────────────────
    if body.include_sentiment:
        kw_filter_sent = ""
        params_sent: dict = {"dt_from": dt_from, "dt_to": dt_to}

        if filter_kw_id:
            kw_filter_sent = "AND p.keyword_id = :kw_id"
            params_sent["kw_id"] = str(filter_kw_id)
        elif q_like:
            kw_filter_sent = "AND k.keyword ILIKE :q_like"
            params_sent["q_like"] = q_like

        sent_rows = (await db.execute(text(f"""
            SELECT la.label, COUNT(*) AS cnt
            FROM lexicon_analyses la
            JOIN comments c ON la.comment_id = c.id
            JOIN posts p    ON c.post_id = p.id
            LEFT JOIN keywords k ON p.keyword_id = k.id
            WHERE p.platform = 'youtube'
              AND p.published_at >= :dt_from AND p.published_at <= :dt_to
              {kw_filter_sent}
            GROUP BY la.label
        """), params_sent)).mappings().all()

        dist: dict[str, int] = {r["label"]: r["cnt"] for r in sent_rows}
        total_analyzed = sum(dist.values())

        daily_rows = (await db.execute(text(f"""
            SELECT DATE(p.published_at AT TIME ZONE 'UTC') AS day, COUNT(*) AS video_count
            FROM posts p LEFT JOIN keywords k ON p.keyword_id = k.id
            WHERE p.platform = 'youtube'
              AND p.published_at >= :dt_from AND p.published_at <= :dt_to
              {kw_filter_sent}
            GROUP BY day ORDER BY day ASC
        """), params_sent)).mappings().all()

        result["sentiment"] = {
            lbl: {
                "count":      dist.get(lbl, 0),
                "percentage": round(dist.get(lbl, 0) / total_analyzed * 100, 1) if total_analyzed else 0.0,
            }
            for lbl in ["positif", "negatif", "netral"]
        }
        result["sentiment"]["dominant"] = max(dist, key=dist.get) if dist else "netral"
        result["sentiment"]["total_analyzed"] = total_analyzed
        result["daily_breakdown"] = [
            {"date": str(r["day"]), "video_count": r["video_count"]}
            for r in daily_rows
        ]

    return build_success_response(result)



# ─────────────────────────────────────────────────────────────────────────────
# KOMENTAR
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/comments", response_model=dict)
async def list_comments(
    keyword_id: uuid.UUID | None = Query(default=None, description="Filter per keyword UUID"),
    q: str | None = Query(default=None, max_length=200, description="Filter nama keyword (ILIKE)"),
    video_id: uuid.UUID | None = Query(default=None, description="UUID Post di DB (bukan YouTube video_id)"),
    youtube_video_id: str | None = Query(default=None, description="YouTube video_id (mis. dQw4w9WgXcQ)"),
    sentiment: str | None = Query(default=None, description="positif | negatif | netral"),
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
    Filter per keyword (UUID atau nama), per video (UUID atau YouTube video_id), sentimen, atau rentang tanggal/jam.
    """


    filters = ["p.platform = 'youtube'"]
    params: dict = {"limit": limit, "offset": offset}

    if video_id:
        filters.append("c.post_id = :video_id")
        params["video_id"] = str(video_id)
    if youtube_video_id:
        filters.append("p.external_id = :yt_vid_id")
        params["yt_vid_id"] = youtube_video_id.strip()
    if keyword_id:
        filters.append("p.keyword_id = :keyword_id")
        params["keyword_id"] = str(keyword_id)
    elif q:
        filters.append("k.keyword ILIKE :q_like")
        params["q_like"] = f"%{q.strip()}%"
    if sentiment:
        filters.append("la.label = :sentiment")
        params["sentiment"] = sentiment
    if date_from:
        filters.append("c.created_at >= :date_from")
        params["date_from"] = datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc)
    if date_to:
        filters.append("c.created_at <= :date_to")
        params["date_to"] = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59, tzinfo=timezone.utc)
    if hour is not None:
        filters.append("EXTRACT(hour FROM c.created_at) = :hour")
        params["hour"] = hour

    where_clause = " AND ".join(filters)
    join_type = "JOIN" if sentiment else "LEFT JOIN"

    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    total: int = (await db.scalar(text(f"""
        SELECT COUNT(*) FROM comments c
        JOIN posts p ON c.post_id = p.id
        LEFT JOIN keywords k ON p.keyword_id = k.id
        {join_type} lexicon_analyses la ON la.comment_id = c.id
        WHERE {where_clause}
    """), count_params)) or 0

    rows = (await db.execute(text(f"""
        SELECT
            c.id, c.external_id, c.content, c.author, c.created_at, c.metadata,
            p.id AS post_id, p.external_id AS post_ext_id, p.content AS post_title, p.url AS post_url,
            la.label AS sentiment, la.score AS sentiment_score,
            k.keyword
        FROM comments c
        JOIN posts p ON c.post_id = p.id
        LEFT JOIN keywords k ON p.keyword_id = k.id
        {join_type} lexicon_analyses la ON la.comment_id = c.id
        WHERE {where_clause}
        ORDER BY c.created_at DESC
        OFFSET :offset LIMIT :limit
    """), params)).mappings().all()

    items = []
    for r in rows:
        meta = r["metadata"] or {}
        items.append({
            "id": str(r["id"]),
            "comment_id": r["external_id"],
            "content": r["content"],
            "author": r["author"],
            "sentiment": r["sentiment"],
            "sentiment_score": round(float(r["sentiment_score"]), 3) if r["sentiment_score"] is not None else None,
            "like_count": meta.get("like_count", 0),
            "reply_count": meta.get("reply_count", 0),
            "author_channel_id": meta.get("author_channel_id"),
            "published_time": meta.get("published_time", ""),
            "video_id": r["post_ext_id"],
            "video_url": r["post_url"] or f"https://youtube.com/watch?v={r['post_ext_id']}",
            "video_title": r["post_title"],
            "keyword": r["keyword"],
            "scraped_at": r["created_at"].isoformat() if r["created_at"] else None,
        })

    return build_success_response({
        "filter": {
            "keyword_id": str(keyword_id) if keyword_id else None,
            "q": q,
            "video_id": str(video_id) if video_id else None,
            "youtube_video_id": youtube_video_id,
            "sentiment": sentiment,
            "date_from": str(date_from) if date_from else None,
            "date_to": str(date_to) if date_to else None,
            "hour": hour,
        },
        "total": total,
        "offset": offset,
        "limit": limit,
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


@router.get("/monitor-public", response_model=dict, tags=["monitor"])
async def scrape_monitor_public(
    db: AsyncSession = Depends(get_db),
):
    """Status scraping tanpa auth — untuk dashboard publik."""
    running_count: int = await db.scalar(
        text("SELECT COUNT(*) FROM scrape_runs WHERE status = 'running'")
    ) or 0

    stale_count: int = await db.scalar(
        text("""
            SELECT COUNT(*) FROM scrape_runs
            WHERE status = 'running'
              AND started_at < NOW() - INTERVAL '30 minutes'
        """)
    ) or 0

    stats = (await db.execute(text("""
        SELECT status, COUNT(*) AS total,
               SUM(videos_fetched) AS videos_fetched,
               SUM(videos_new) AS videos_new,
               SUM(comments_new) AS comments_new,
               AVG(duration_seconds) AS avg_duration_sec
        FROM scrape_runs
        WHERE started_at >= NOW() - INTERVAL '24 hours'
        GROUP BY status
    """))).mappings().all()

    stats_by_status = {
        r["status"]: {
            "total": r["total"],
            "videos_fetched": int(r["videos_fetched"] or 0),
            "videos_new": int(r["videos_new"] or 0),
            "comments_new": int(r["comments_new"] or 0),
            "avg_duration_sec": round(float(r["avg_duration_sec"] or 0), 1),
        }
        for r in stats
    }

    rows = (await db.execute(text("""
        SELECT sr.id, sr.keyword_text, sr.api_source, sr.status, sr.triggered_by,
               sr.videos_fetched, sr.videos_new, sr.videos_duplicate,
               sr.comments_fetched, sr.comments_new,
               sr.duration_seconds, sr.error_message,
               sr.started_at, sr.finished_at,
               k.keyword AS kw_name
        FROM scrape_runs sr
        LEFT JOIN keywords k ON sr.keyword_id = k.id
        ORDER BY sr.started_at DESC
        LIMIT 50
    """))).mappings().all()

    total_posts: int = await db.scalar(text("SELECT COUNT(*) FROM posts")) or 0
    total_comments: int = await db.scalar(text("SELECT COUNT(*) FROM comments")) or 0
    total_keywords: int = await db.scalar(text("SELECT COUNT(*) FROM keywords")) or 0

    runs = []
    for r in rows:
        runs.append({
            "run_id": str(r["id"]),
            "keyword": r["kw_name"] or r["keyword_text"],
            "api_source": r["api_source"],
            "status": r["status"],
            "triggered_by": r["triggered_by"],
            "videos_fetched": r["videos_fetched"],
            "videos_new": r["videos_new"],
            "videos_duplicate": r["videos_duplicate"],
            "comments_fetched": r["comments_fetched"],
            "comments_new": r["comments_new"],
            "duration_sec": round(float(r["duration_seconds"]), 1) if r["duration_seconds"] else None,
            "error": r["error_message"],
            "started_at": r["started_at"].isoformat() if r["started_at"] else None,
            "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
        })

    is_alive = running_count > 0 or (
        await db.scalar(
            text("SELECT COUNT(*) FROM scrape_runs WHERE started_at >= NOW() - INTERVAL '2 hours'")
        ) or 0
    ) > 0

    # ── Celery worker info via inspect ────────────────────────────────────────
    import asyncio
    from app.workers.celery_app import celery_app as _celery

    def _inspect_workers():
        insp = _celery.control.inspect(timeout=3)
        ping    = insp.ping()    or {}
        active  = insp.active()  or {}
        stats   = insp.stats()   or {}
        workers = []
        for node, _ in ping.items():
            node_stats = stats.get(node, {})
            pool       = node_stats.get("pool", {})
            active_tasks = active.get(node, [])
            workers.append({
                "name":        node,
                "status":      "online",
                "concurrency": pool.get("max-concurrency"),
                "processes":   pool.get("processes", []),
                "active_tasks": [
                    {
                        "id":   t.get("id"),
                        "name": t.get("name"),
                        "args": t.get("args"),
                        "time_start": t.get("time_start"),
                    }
                    for t in active_tasks
                ],
            })
        return workers

    try:
        workers = await asyncio.get_event_loop().run_in_executor(None, _inspect_workers)
    except Exception:
        workers = []

    return build_success_response({
        "worker_alive": is_alive,
        "currently_running": running_count,
        "stale_runs": stale_count,
        "totals": {
            "posts": total_posts,
            "comments": total_comments,
            "keywords": total_keywords,
        },
        "last_24h": stats_by_status,
        "workers": workers,
        "runs": runs,
    })


@router.get("/monitor", response_model=dict)
async def scrape_monitor(
    limit: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None, description="Filter: running | success | failed | fallback"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Monitor scraping — apakah worker jalan, scraping apa yang sedang/sudah berlangsung.

    - `running`  : sedang berjalan sekarang
    - `success`  : selesai normal via EnsembleData
    - `fallback` : selesai tapi ada error/partial, fallback ke YouTube Data API
    - `failed`   : gagal total
    """
    from app.domain.scrape_runs.models import ScrapeRun

    # Cek apakah ada run yang sedang berjalan
    running_count: int = await db.scalar(
        text("SELECT COUNT(*) FROM scrape_runs WHERE status = 'running'")
    ) or 0

    # Run yang stuck > 30 menit dianggap dead
    stale_count: int = await db.scalar(
        text("""
            SELECT COUNT(*) FROM scrape_runs
            WHERE status = 'running'
              AND started_at < NOW() - INTERVAL '30 minutes'
        """)
    ) or 0

    # Statistik 24 jam terakhir
    stats = (await db.execute(text("""
        SELECT
            status,
            COUNT(*)                            AS total,
            SUM(videos_fetched)                 AS videos_fetched,
            SUM(videos_new)                     AS videos_new,
            SUM(comments_new)                   AS comments_new,
            AVG(duration_seconds)               AS avg_duration_sec
        FROM scrape_runs
        WHERE started_at >= NOW() - INTERVAL '24 hours'
        GROUP BY status
    """))).mappings().all()

    stats_by_status = {
        r["status"]: {
            "total": r["total"],
            "videos_fetched": int(r["videos_fetched"] or 0),
            "videos_new": int(r["videos_new"] or 0),
            "comments_new": int(r["comments_new"] or 0),
            "avg_duration_sec": round(float(r["avg_duration_sec"] or 0), 1),
        }
        for r in stats
    }

    # Run terbaru
    where = "WHERE sr.status = :status" if status else ""
    params: dict = {"limit": limit}
    if status:
        params["status"] = status

    rows = (await db.execute(text(f"""
        SELECT
            sr.id, sr.keyword_text, sr.api_source, sr.status, sr.triggered_by,
            sr.videos_fetched, sr.videos_new, sr.videos_duplicate,
            sr.comments_fetched, sr.comments_new,
            sr.duration_seconds, sr.error_message,
            sr.started_at, sr.finished_at,
            k.keyword AS kw_name
        FROM scrape_runs sr
        LEFT JOIN keywords k ON sr.keyword_id = k.id
        {where}
        ORDER BY sr.started_at DESC
        LIMIT :limit
    """), params)).mappings().all()

    runs = []
    for r in rows:
        runs.append({
            "run_id":           str(r["id"]),
            "keyword":          r["kw_name"] or r["keyword_text"],
            "api_source":       r["api_source"],
            "status":           r["status"],
            "triggered_by":     r["triggered_by"],
            "videos_fetched":   r["videos_fetched"],
            "videos_new":       r["videos_new"],
            "videos_duplicate": r["videos_duplicate"],
            "comments_fetched": r["comments_fetched"],
            "comments_new":     r["comments_new"],
            "duration_sec":     round(float(r["duration_seconds"]), 1) if r["duration_seconds"] else None,
            "error":            r["error_message"],
            "started_at":       r["started_at"].isoformat() if r["started_at"] else None,
            "finished_at":      r["finished_at"].isoformat() if r["finished_at"] else None,
        })

    is_alive = running_count > 0 or (
        await db.scalar(
            text("SELECT COUNT(*) FROM scrape_runs WHERE started_at >= NOW() - INTERVAL '2 hours'")
        ) or 0
    ) > 0

    return build_success_response({
        "worker_alive": is_alive,
        "currently_running": running_count,
        "stale_runs": stale_count,
        "last_24h": stats_by_status,
        "runs": runs,
    })


@router.get("/scrape-history", response_model=dict)
async def scrape_history(
    keyword_id: uuid.UUID | None = Query(default=None, description="Filter per keyword (opsional)"),
    date_from: date | None = Query(default=None, description="Dari tanggal (YYYY-MM-DD)"),
    date_to: date | None = Query(default=None, description="Sampai tanggal (YYYY-MM-DD)"),
    group_by: str = Query(default="day", pattern="^(day|hour)$", description="Grup per 'day' atau 'hour'"),
    limit: int = Query(default=30, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Riwayat scraping — kapan video & komentar dikumpulkan, dikelompokkan per hari atau jam.

    Setiap baris mewakili satu sesi scraping (1 keyword × 1 hari/jam):
    - videos_collected: jumlah video yang masuk DB di periode itu
    - comments_collected: total komentar dari video tersebut
    - started_at / ended_at: rentang waktu pengumpulan
    """
    filters = ["p.collected_at IS NOT NULL", "p.platform = 'youtube'"]
    params: dict = {"limit": limit, "offset": offset}

    if keyword_id:
        filters.append("p.keyword_id = :keyword_id")
        params["keyword_id"] = str(keyword_id)
    if date_from:
        filters.append("DATE(p.collected_at AT TIME ZONE 'UTC') >= :date_from")
        params["date_from"] = date_from.isoformat()
    if date_to:
        filters.append("DATE(p.collected_at AT TIME ZONE 'UTC') <= :date_to")
        params["date_to"] = date_to.isoformat()

    where = " AND ".join(filters)

    if group_by == "hour":
        group_expr = "DATE_TRUNC('hour', p.collected_at)"
        period_label = "DATE_TRUNC('hour', p.collected_at) AS period"
    else:
        group_expr = "DATE(p.collected_at AT TIME ZONE 'UTC')"
        period_label = "DATE(p.collected_at AT TIME ZONE 'UTC') AS period"

    rows = (await db.execute(
        text(f"""
            SELECT
                {period_label},
                p.keyword_id,
                k.keyword                       AS keyword_name,
                COUNT(DISTINCT p.id)            AS videos_collected,
                COUNT(c.id)                     AS comments_collected,
                MIN(p.collected_at)             AS started_at,
                MAX(p.collected_at)             AS ended_at
            FROM posts p
            JOIN keywords k ON k.id = p.keyword_id
            LEFT JOIN comments c ON c.post_id = p.id
            WHERE {where}
            GROUP BY {group_expr}, p.keyword_id, k.keyword
            ORDER BY period DESC
            LIMIT :limit OFFSET :offset
        """),
        params,
    )).fetchall()

    total: int = await db.scalar(
        text(f"""
            SELECT COUNT(*) FROM (
                SELECT {group_expr}, p.keyword_id
                FROM posts p
                JOIN keywords k ON k.id = p.keyword_id
                WHERE {where}
                GROUP BY {group_expr}, p.keyword_id
            ) sub
        """),
        {k: v for k, v in params.items() if k not in ("limit", "offset")},
    ) or 0

    items = []
    for r in rows:
        period = r.period
        items.append({
            "period": period.isoformat() if hasattr(period, "isoformat") else str(period),
            "keyword_id": str(r.keyword_id),
            "keyword_name": r.keyword_name,
            "videos_collected": r.videos_collected,
            "comments_collected": r.comments_collected,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "ended_at": r.ended_at.isoformat() if r.ended_at else None,
        })

    return build_success_response({
        "group_by": group_by,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
    })


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


# ─────────────────────────────────────────────────────────────────────────────
# VIRAL TRACKING
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/viral-tracking", summary="List viral channel trackers")
async def list_viral_trackers(
    status: str | None = Query(default=None, description="active | completed"),
    tracker_type: str | None = Query(default=None, description="viral | flagged_commenter"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Daftar channel yang sedang/pernah dilacak karena post viral."""
    q = select(ViralChannelTracker)
    if status:
        q = q.where(ViralChannelTracker.status == status)
    if tracker_type:
        q = q.where(ViralChannelTracker.tracker_type == tracker_type)
    q = q.order_by(desc(ViralChannelTracker.started_at)).limit(limit).offset(offset)

    rows = list((await db.scalars(q)).all())
    total = (await db.scalar(
        select(func.count(ViralChannelTracker.id)).where(
            *(
                ([ViralChannelTracker.status == status] if status else []) +
                ([ViralChannelTracker.tracker_type == tracker_type] if tracker_type else [])
            )
        )
    )) or 0

    items = [
        {
            "id": str(t.id),
            "channel_id": t.channel_id,
            "channel_name": t.channel_name,
            "tracker_type": t.tracker_type,
            "status": t.status,
            "posts_collected": t.posts_collected,
            "started_at": t.started_at.isoformat() if t.started_at else None,
            "ends_at": t.ends_at.isoformat() if t.ends_at else None,
            "last_scraped_date": t.last_scraped_date.isoformat() if t.last_scraped_date else None,
            "trigger_post_id": str(t.trigger_post_id) if t.trigger_post_id else None,
            "keyword_id": str(t.keyword_id) if t.keyword_id else None,
        }
        for t in rows
    ]
    return build_success_response({"total": total, "limit": limit, "offset": offset, "items": items})


@router.get("/viral-tracking/{tracker_id}", summary="Detail viral tracker + timeline 7 hari + flagged accounts")
async def get_viral_tracker_detail(
    tracker_id: uuid.UUID,
    limit_posts: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Detail tracker: status 7 hari, scrape_logs per hari, post terbaru, akun yang diflag.

    scrape_timeline: riwayat scraping per hari (hari 1–7) dari scrape_logs.
    progress: persentase hari yang sudah diselesaikan.
    """
    tracker = await db.get(ViralChannelTracker, tracker_id)
    if not tracker:
        from app.shared.exceptions import NotFoundError
        raise NotFoundError("ViralChannelTracker", str(tracker_id))

    from datetime import date as _date, timezone as _tz
    now = datetime.now(timezone.utc)

    # ── Progress & timeline ───────────────────────────────────────────────────
    total_days = 7
    days_elapsed = max(0, (now.date() - tracker.started_at.date()).days + 1)
    days_elapsed = min(days_elapsed, total_days)
    days_remaining = max(0, (tracker.ends_at.date() - now.date()).days)
    progress_pct = round((days_elapsed / total_days) * 100, 1)

    # scrape_logs tersimpan di DB; isi slot hari yang belum scraping dengan placeholder
    existing_logs: dict[int, dict] = {
        entry["day"]: entry for entry in (tracker.scrape_logs or [])
    }
    scrape_timeline = []
    for day_n in range(1, total_days + 1):
        target_date = (tracker.started_at.date() + __import__("datetime").timedelta(days=day_n - 1))
        if day_n in existing_logs:
            entry = dict(existing_logs[day_n])
            entry["status"] = "error" if "error" in entry else "done"
        elif target_date > now.date():
            entry = {"day": day_n, "date": target_date.isoformat(), "status": "pending",
                     "posts_new": None, "posts_skipped": None}
        else:
            entry = {"day": day_n, "date": target_date.isoformat(), "status": "skipped",
                     "posts_new": 0, "posts_skipped": 0}
        scrape_timeline.append(entry)

    # ── Posts terbaru dari tracker ini ───────────────────────────────────────
    posts_rows = list((await db.scalars(
        select(Post)
        .where(
            Post.platform == "youtube",
            Post.metadata_["tracker_id"].as_string() == str(tracker_id),
        )
        .order_by(desc(Post.collected_at))
        .limit(limit_posts)
    )).all())

    posts = [
        {
            "id": str(p.id),
            "video_id": p.external_id,
            "title": p.content,
            "url": p.url,
            "views": (p.metadata_ or {}).get("views", 0),
            "collected_at": p.collected_at.isoformat() if p.collected_at else None,
        }
        for p in posts_rows
    ]

    # ── Flagged accounts ─────────────────────────────────────────────────────
    flagged_rows = list((await db.scalars(
        select(FlaggedAccount)
        .where(FlaggedAccount.tracker_id == tracker_id)
        .order_by(desc(FlaggedAccount.flagged_at))
    )).all())

    flagged = [
        {
            "id": str(f.id),
            "channel_id": f.channel_id,
            "channel_name": f.channel_name,
            "comment_count": f.comment_count,
            "flagged_at": f.flagged_at.isoformat() if f.flagged_at else None,
            "analysis_tracker_id": str(f.analysis_tracker_id) if f.analysis_tracker_id else None,
        }
        for f in flagged_rows
    ]

    return build_success_response({
        "tracker": {
            "id": str(tracker.id),
            "channel_id": tracker.channel_id,
            "channel_name": tracker.channel_name,
            "tracker_type": tracker.tracker_type,
            "status": tracker.status,
            "posts_collected": tracker.posts_collected,
            "flagged_accounts_count": len(flagged),
            "started_at": tracker.started_at.isoformat() if tracker.started_at else None,
            "ends_at": tracker.ends_at.isoformat() if tracker.ends_at else None,
            "last_scraped_date": tracker.last_scraped_date.isoformat() if tracker.last_scraped_date else None,
            "trigger_post_id": str(tracker.trigger_post_id) if tracker.trigger_post_id else None,
            "keyword_id": str(tracker.keyword_id) if tracker.keyword_id else None,
        },
        "progress": {
            "total_days": total_days,
            "days_elapsed": days_elapsed,
            "days_remaining": days_remaining,
            "percent": progress_pct,
            "scrape_days_done": len([e for e in scrape_timeline if e["status"] == "done"]),
            "scrape_days_error": len([e for e in scrape_timeline if e["status"] == "error"]),
        },
        "scrape_timeline": scrape_timeline,
        "posts": posts,
        "flagged_accounts": flagged,
    })


@router.get("/flagged-accounts", summary="List akun yang diflag karena komentar berulang")
async def list_flagged_accounts(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Daftar akun yang komentar >10x pada post viral dan sudah diflag sistem."""
    rows = list((await db.scalars(
        select(FlaggedAccount)
        .order_by(desc(FlaggedAccount.flagged_at))
        .limit(limit).offset(offset)
    )).all())

    total = (await db.scalar(select(func.count(FlaggedAccount.id)))) or 0

    items = [
        {
            "id": str(f.id),
            "channel_id": f.channel_id,
            "channel_name": f.channel_name,
            "comment_count": f.comment_count,
            "flagged_at": f.flagged_at.isoformat() if f.flagged_at else None,
            "tracker_id": str(f.tracker_id),
            "trigger_post_id": str(f.trigger_post_id) if f.trigger_post_id else None,
            "analysis_tracker_id": str(f.analysis_tracker_id) if f.analysis_tracker_id else None,
        }
        for f in rows
    ]
    return build_success_response({"total": total, "limit": limit, "offset": offset, "items": items})


# ─────────────────────────────────────────────────────────────────────────────
# VIRAL TRACKING — MANUAL TRIGGERS
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/viral-tracking/detect", response_model=dict, status_code=202, summary="Jalankan deteksi post viral sekarang")
async def trigger_viral_detect(
    current_user: User = Depends(get_current_user),
):
    """Trigger manual deteksi post >=1M views dan buat tracker baru (otomatis setiap 6 jam)."""
    from app.workers.viral_tracking_worker import detect_viral_posts_task
    task = detect_viral_posts_task.delay()
    return build_success_response({"job_id": task.id, "status": "queued", "message": "Deteksi post viral berjalan di background."})


@router.post("/viral-tracking/{tracker_id}/scrape", response_model=dict, status_code=202, summary="Paksa scrape channel tracker sekarang")
async def trigger_tracker_scrape(
    tracker_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger manual scrape channel untuk tracker tertentu, tanpa menunggu jadwal harian."""
    tracker = await db.get(ViralChannelTracker, tracker_id)
    if not tracker:
        from app.shared.exceptions import NotFoundError
        raise NotFoundError("ViralChannelTracker", str(tracker_id))

    tracker.last_scraped_date = None
    await db.commit()

    from app.workers.viral_tracking_worker import viral_channel_daily_scrape_task
    task = viral_channel_daily_scrape_task.delay(str(tracker_id))
    return build_success_response({
        "job_id": task.id,
        "tracker_id": str(tracker_id),
        "channel_id": tracker.channel_id,
        "channel_name": tracker.channel_name,
        "status": "queued",
        "message": "Scrape channel berjalan di background.",
    })
