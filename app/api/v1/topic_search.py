"""
Universal Topic-Based Search API.

Mencari data berdasarkan kombinasi topik dan kata kunci,
dikelompokkan per topik. Berlaku untuk semua platform (YouTube, TikTok, Instagram, News).

Contoh pemanggilan:
    POST /api/v1/search/topics
    {
        "topics": [
            {"name": "jawa timur", "keywords": ["polisi ditembak preman", "kasus suap bupati surabaya"]},
            {"name": "jawa tengah", "keywords": ["hamengkubuwono", "ojon jogja"]}
        ],
        "platforms": ["youtube"],
        "auto_crawl": true
    }
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.keywords.models import Keyword
from app.domain.posts.models import Post
from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.infrastructure.logging.logger import get_logger
from app.services.auth.dependencies import get_current_user
from app.shared.utils import build_success_response

router = APIRouter(prefix="/search", tags=["topic-search"])
logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# SCHEMAS
# ─────────────────────────────────────────────────────────────────────────────

class TopicItem(BaseModel):
    name: str = Field(..., description="Nama topik / lokasi, contoh: 'jawa timur'")
    keywords: list[str] = Field(..., min_length=1, description="Kata kunci terkait topik ini")


class TopicSearchRequest(BaseModel):
    topics: list[TopicItem] = Field(..., min_length=1, description="Daftar topik beserta kata kuncinya")
    platforms: list[str] = Field(default=["youtube"], description="Platform: youtube, tiktok, instagram, news")
    limit_per_keyword: int = Field(default=10, ge=1, le=100, description="Maks hasil per kata kunci")
    include_sentiment: bool = Field(default=True, description="Sertakan ringkasan sentimen")
    include_comments: bool = Field(default=False, description="Sertakan sample komentar")
    auto_crawl: bool = Field(default=True, description="Crawl otomatis jika data belum ada di DB")
    scheduled_hour: int | None = Field(default=None, ge=0, le=23, description="Jam crawl harian otomatis (WIB) jika auto_crawl=true dan data kosong")


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

async def _find_keyword(db: AsyncSession, q: str) -> Keyword | None:
    """Cari keyword di DB: exact → LIKE → all-words."""
    q_clean = q.strip().lower()

    kw = await db.scalar(select(Keyword).where(func.lower(Keyword.keyword) == q_clean).limit(1))
    if kw:
        return kw

    kw = await db.scalar(select(Keyword).where(func.lower(Keyword.keyword).like(f"%{q_clean}%")).limit(1))
    if kw:
        return kw

    words = q_clean.split()
    if len(words) > 1:
        from sqlalchemy import and_
        conditions = [func.lower(Keyword.keyword).contains(w) for w in words]
        kw = await db.scalar(select(Keyword).where(and_(*conditions)).limit(1))

    return kw


async def _get_posts(
    db: AsyncSession,
    keyword_id: uuid.UUID,
    platforms: list[str],
    limit: int,
) -> list[dict]:
    """Ambil posts dari DB untuk keyword tertentu."""
    filters = [Post.keyword_id == keyword_id]
    if platforms:
        filters.append(Post.platform.in_(platforms))

    from sqlalchemy import cast, BigInteger
    rows = (await db.scalars(
        select(Post)
        .where(*filters)
        .order_by(Post.collected_at.desc())
        .limit(limit)
    )).all()

    results = []
    for p in rows:
        meta = p.metadata_ or {}
        raw_views = meta.get("views", meta.get("view_count", 0))
        try:
            view_count = int(str(raw_views).replace(",", "").split()[0]) if raw_views else 0
        except (ValueError, IndexError):
            view_count = 0

        results.append({
            "id": str(p.id),
            "platform": p.platform,
            "title": p.content,
            "author": p.author,
            "url": p.url,
            "view_count": view_count,
            "published_at": p.published_at.isoformat() if p.published_at else None,
            "collected_at": p.collected_at.isoformat() if p.collected_at else None,
            "thumbnail_url": meta.get("thumbnail", meta.get("thumbnail_url", "")),
        })

    return results


async def _get_sentiment_summary(db: AsyncSession, keyword_id: uuid.UUID) -> dict:
    """Hitung ringkasan sentimen untuk keyword."""
    from sqlalchemy import case
    from app.domain.youtube_analysis.models import LexiconAnalysis
    from app.domain.comments.models import Comment

    rows = await db.execute(
        select(LexiconAnalysis.label, func.count(LexiconAnalysis.id))
        .join(Comment, LexiconAnalysis.comment_id == Comment.id)
        .join(Post, Comment.post_id == Post.id)
        .where(Post.keyword_id == keyword_id)
        .group_by(LexiconAnalysis.label)
    )

    summary = {"positif": 0, "negatif": 0, "netral": 0}
    total = 0
    for label, count in rows.all():
        if label in summary:
            summary[label] = count
            total += count

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


async def _get_sample_comments(db: AsyncSession, keyword_id: uuid.UUID, limit: int = 5) -> list[dict]:
    """Ambil sample komentar untuk keyword."""
    from app.domain.comments.models import Comment

    rows = (await db.scalars(
        select(Comment)
        .join(Post, Comment.post_id == Post.id)
        .where(Post.keyword_id == keyword_id)
        .order_by(Comment.published_at.desc().nullslast())
        .limit(limit)
    )).all()

    return [
        {
            "author": c.author,
            "content": c.content,
            "published_at": c.published_at.isoformat() if c.published_at else None,
        }
        for c in rows
    ]


async def _queue_crawl(db: AsyncSession, keyword_text: str, platforms: list[str]) -> dict:
    """Buat keyword di DB dan queue crawl via Celery."""
    from app.domain.projects.models import Project
    from app.workers.youtube_worker import run_youtube_pipeline

    # Ambil project pertama
    project = await db.scalar(select(Project).limit(1))
    if not project:
        return {"status": "error", "message": "Tidak ada project di DB"}

    # Buat keyword jika belum ada
    kw = Keyword(project_id=project.id, keyword=keyword_text, is_active=True)
    db.add(kw)
    await db.flush()
    await db.refresh(kw)

    # Queue Celery task untuk YouTube
    if "youtube" in platforms:
        run_youtube_pipeline.apply_async(
            kwargs={
                "keyword_id": str(kw.id),
                "max_pages": 2,
                "max_comment_pages": 2,
                "max_comments_per_video": 50,
            },
            queue="default",
        )

    return {
        "status": "crawling",
        "keyword_id": str(kw.id),
        "message": f"Keyword '{keyword_text}' dibuat dan crawl dimulai di background",
        "poll_url": f"/api/v1/youtube/status?keyword_id={kw.id}",
    }


def _add_scheduled_crawl(keyword_text: str, hour: int, platforms: list[str]):
    """Tambah jadwal crawl harian via Celery beat (dynamic schedule)."""
    from app.workers.celery_app import celery_app

    task_name = f"scheduled-topic-{keyword_text.replace(' ', '-')[:40]}"
    celery_app.conf.beat_schedule[task_name] = {
        "task": "workers.youtube.run_pipeline",
        "schedule": __import__("celery.schedules", fromlist=["crontab"]).crontab(hour=hour, minute=0),
        "kwargs": {"keyword_text": keyword_text},
        "options": {"queue": "default"},
    }
    logger.info("scheduled_crawl_added", keyword=keyword_text, hour=hour)


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT UTAMA
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/topics", response_model=dict)
async def search_by_topics(
    body: TopicSearchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Cari data berdasarkan topik + kata kunci, dikelompokkan per topik.

    **Alur:**
    - Untuk setiap topik → cari setiap kata kunci di DB (LIKE match)
    - Jika ditemukan → kembalikan posts + sentimen + komentar (opsional)
    - Jika tidak ditemukan + `auto_crawl=true` → buat keyword + queue crawl otomatis
    - Jika `scheduled_hour` diisi → tambah jadwal crawl harian

    **Platform yang didukung:**
    - `youtube` — sudah aktif
    - `tiktok`, `instagram`, `news` — akan aktif saat connector tersedia

    **Contoh request:**
    ```json
    {
        "topics": [
            {"name": "jawa timur", "keywords": ["polisi ditembak preman", "kasus suap bupati"]},
            {"name": "jawa tengah", "keywords": ["hamengkubuwono", "ojon jogja"]}
        ],
        "platforms": ["youtube"],
        "auto_crawl": true,
        "include_sentiment": true
    }
    ```
    """
    logger.info("topic_search_start", topics=[t.name for t in body.topics], user=str(current_user.id))

    topic_results = []
    crawling_keywords = []

    for topic in body.topics:
        keyword_results = []
        topic_total_posts = 0

        for kw_text in topic.keywords:
            # Cari keyword di DB
            keyword = await _find_keyword(db, kw_text)

            if keyword:
                # Ambil posts
                posts = await _get_posts(db, keyword.id, body.platforms, body.limit_per_keyword)
                total = len(posts)
                topic_total_posts += total

                kw_result: dict = {
                    "keyword": kw_text,
                    "keyword_id": str(keyword.id),
                    "status": "found" if total > 0 else "empty",
                    "total": total,
                    "platforms": body.platforms,
                    "posts": posts,
                }

                if body.include_sentiment and total > 0:
                    kw_result["sentiment"] = await _get_sentiment_summary(db, keyword.id)

                if body.include_comments and total > 0:
                    kw_result["sample_comments"] = await _get_sample_comments(db, keyword.id)

                if total == 0 and body.auto_crawl:
                    crawl_info = await _queue_crawl(db, kw_text, body.platforms)
                    kw_result["crawl"] = crawl_info
                    crawling_keywords.append(kw_text)

            else:
                # Keyword tidak ada di DB sama sekali
                kw_result = {
                    "keyword": kw_text,
                    "keyword_id": None,
                    "status": "not_found",
                    "total": 0,
                    "platforms": body.platforms,
                    "posts": [],
                }

                if body.auto_crawl:
                    crawl_info = await _queue_crawl(db, kw_text, body.platforms)
                    kw_result["crawl"] = crawl_info
                    kw_result["status"] = "crawling"
                    crawling_keywords.append(kw_text)

                    if body.scheduled_hour is not None:
                        _add_scheduled_crawl(kw_text, body.scheduled_hour, body.platforms)

            keyword_results.append(kw_result)

        topic_results.append({
            "topic": topic.name,
            "keywords_searched": topic.keywords,
            "total_posts": topic_total_posts,
            "keyword_details": keyword_results,
        })

    await db.commit()

    # Format output terstruktur per topik
    formatted = []
    for t in topic_results:
        posts_all = []
        for kd in t["keyword_details"]:
            for p in kd.get("posts", []):
                p["_keyword"] = kd["keyword"]
                posts_all.append(p)

        formatted.append({
            "topic": t["topic"].title(),
            "total_posts": t["total_posts"],
            "keywords": t["keywords_searched"],
            "status_per_keyword": {
                kd["keyword"]: kd["status"] for kd in t["keyword_details"]
            },
            "sentiment_per_keyword": {
                kd["keyword"]: kd.get("sentiment")
                for kd in t["keyword_details"]
                if kd.get("sentiment")
            },
            "results": posts_all,
            "crawling": [
                kd["keyword"] for kd in t["keyword_details"]
                if kd["status"] in ("crawling", "not_found") and body.auto_crawl
            ],
        })

    summary_status = "ready"
    if crawling_keywords:
        summary_status = "partial" if any(t["total_posts"] > 0 for t in topic_results) else "crawling"

    logger.info("topic_search_done", topics=[t["topic"] for t in topic_results], crawling=crawling_keywords)

    return build_success_response({
        "status": summary_status,
        "platforms": body.platforms,
        "total_topics": len(topic_results),
        "crawling_keywords": crawling_keywords,
        "note": "Keyword dengan status 'crawling' sedang diproses di background. Panggil ulang dalam beberapa menit." if crawling_keywords else None,
        "topics": formatted,
    })


@router.get("/topics", response_model=dict)
async def get_topic_search(
    topics: str = Query(..., description="Topik dipisah koma, contoh: jawa timur,jawa tengah"),
    keywords: str = Query(..., description="Kata kunci per topik dipisah | antar topik dan , antar kata kunci. Contoh: polisi,suap|hamengku,jogja"),
    platforms: str = Query(default="youtube", description="Platform dipisah koma: youtube,tiktok"),
    limit: int = Query(default=10, ge=1, le=100),
    include_sentiment: bool = Query(default=True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Versi GET dari topic search — untuk akses mudah via URL/browser.

    Contoh:
    ```
    GET /api/v1/search/topics?topics=jawa timur,jawa tengah&keywords=polisi,suap|hamengku,jogja
    ```

    Format parameter `keywords`: pisah `|` antar topik, pisah `,` antar kata kunci dalam satu topik.
    Urutan harus sesuai dengan urutan `topics`.
    """
    topic_list = [t.strip() for t in topics.split(",") if t.strip()]
    keyword_groups = [
        [k.strip() for k in group.split(",") if k.strip()]
        for group in keywords.split("|")
    ]
    platform_list = [p.strip() for p in platforms.split(",") if p.strip()]

    # Pad keyword_groups jika kurang dari jumlah topik
    while len(keyword_groups) < len(topic_list):
        keyword_groups.append([topic_list[len(keyword_groups)]])

    body = TopicSearchRequest(
        topics=[
            TopicItem(name=name, keywords=kws)
            for name, kws in zip(topic_list, keyword_groups)
        ],
        platforms=platform_list,
        limit_per_keyword=limit,
        include_sentiment=include_sentiment,
        auto_crawl=False,  # GET tidak auto-crawl
    )

    return await search_by_topics(body, current_user, db)
