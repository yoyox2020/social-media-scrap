"""
Universal Topic-Based Search API.

Topik dan keyword-nya disimpan ke DB sehingga bisa ditampilkan di dashboard.
Setiap topik bisa punya banyak keyword, dan satu keyword bisa masuk banyak topik.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.keywords.models import Keyword
from app.domain.posts.models import Post
from app.domain.search_topics.models import SearchTopic, SearchTopicKeyword
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
    name: str = Field(..., description="Nama topik, contoh: 'jawa timur'")
    keywords: list[str] = Field(..., min_length=1, description="Kata kunci terkait topik ini")
    description: str | None = Field(default=None)


class TopicSearchRequest(BaseModel):
    topics: list[TopicItem] = Field(..., min_length=1)
    platforms: list[str] = Field(default=["youtube"], description="Platform: youtube, tiktok, instagram, news")
    limit_per_keyword: int = Field(default=10, ge=1, le=100)
    include_sentiment: bool = Field(default=True)
    include_comments: bool = Field(default=False)
    auto_crawl: bool = Field(default=True, description="Crawl otomatis jika data belum ada")
    scheduled_hour: int | None = Field(default=None, ge=0, le=23, description="Jam crawl harian otomatis (0-23)")
    save_topic: bool = Field(default=True, description="Simpan konfigurasi topik ke DB untuk dashboard")


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

async def _find_keyword(db: AsyncSession, q: str) -> Keyword | None:
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


async def _get_posts(db: AsyncSession, keyword_id: uuid.UUID, platforms: list[str], limit: int) -> list[dict]:
    filters = [Post.keyword_id == keyword_id]
    if platforms:
        filters.append(Post.platform.in_(platforms))
    rows = (await db.scalars(
        select(Post).where(*filters).order_by(Post.collected_at.desc()).limit(limit)
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


async def _queue_crawl(db: AsyncSession, keyword_text: str, platforms: list[str]) -> dict:
    from app.domain.projects.models import Project
    project = await db.scalar(select(Project).limit(1))
    if not project:
        return {"status": "error", "message": "Tidak ada project di DB"}

    kw = Keyword(project_id=project.id, keyword=keyword_text, is_active=True)
    db.add(kw)
    await db.flush()
    await db.refresh(kw)

    if "youtube" in platforms:
        from app.workers.youtube_worker import collect_youtube_pipeline_task
        collect_youtube_pipeline_task.apply_async(
            kwargs={"keyword_id": str(kw.id), "max_pages": 2, "max_comment_pages": 2, "max_comments_per_video": 50},
            queue="default",
        )

    return {
        "status": "crawling",
        "keyword_id": str(kw.id),
        "message": f"Keyword '{keyword_text}' dibuat dan crawl dimulai di background",
        "poll_url": f"/api/v1/youtube/status?keyword_id={kw.id}",
    }


async def _save_topic(
    db: AsyncSession,
    topic_name: str,
    description: str | None,
    keyword_objects: list[tuple[str, Keyword]],
    platforms: list[str],
    scheduled_hour: int | None,
    auto_crawl: bool,
) -> SearchTopic:
    """Simpan atau update topik ke DB. Jika nama sudah ada, update keyword-nya."""
    from sqlalchemy.orm import selectinload
    existing = await db.scalar(
        select(SearchTopic)
        .options(selectinload(SearchTopic.topic_keywords))
        .where(func.lower(SearchTopic.name) == topic_name.strip().lower()).limit(1)
    )

    if existing:
        # Update platforms dan scheduled_hour jika berubah
        existing.platforms = platforms
        existing.scheduled_hour = scheduled_hour
        existing.auto_crawl = auto_crawl
        existing.updated_at = datetime.now(timezone.utc)
        topic = existing
    else:
        topic = SearchTopic(
            name=topic_name.strip().title(),
            description=description,
            platforms=platforms,
            scheduled_hour=scheduled_hour,
            auto_crawl=auto_crawl,
        )
        db.add(topic)
        await db.flush()

    # Ambil keyword_id yang sudah terhubung
    existing_kw_ids = {stk.keyword_id for stk in topic.topic_keywords}

    # Tambah keyword baru yang belum terhubung
    for kw_text, kw_obj in keyword_objects:
        if kw_obj and kw_obj.id not in existing_kw_ids:
            link = SearchTopicKeyword(topic_id=topic.id, keyword_id=kw_obj.id, keyword_text=kw_text)
            db.add(link)

    return topic


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT: Cari + Simpan Topik
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/topics", response_model=dict)
async def search_by_topics(
    body: TopicSearchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Cari data berdasarkan topik + kata kunci, dikelompokkan per topik.
    Jika `save_topic=true` (default), topik dan keyword-nya disimpan ke DB untuk dashboard.

    **Alur:**
    - Cari setiap keyword di DB (LIKE match)
    - Jika ada data → kembalikan posts + sentimen
    - Jika belum ada + auto_crawl=true → buat keyword + crawl otomatis
    - Topik disimpan ke DB → tampil di `GET /search/topics/list`
    """
    logger.info("topic_search", topics=[t.name for t in body.topics], user=str(current_user.id))

    topic_results = []
    crawling_keywords = []

    for topic in body.topics:
        keyword_results = []
        topic_total_posts = 0
        keyword_objects: list[tuple[str, Keyword | None]] = []

        for kw_text in topic.keywords:
            keyword = await _find_keyword(db, kw_text)
            kw_result: dict = {
                "keyword": kw_text,
                "keyword_id": str(keyword.id) if keyword else None,
                "status": "not_found",
                "total": 0,
                "posts": [],
            }

            if keyword:
                posts = await _get_posts(db, keyword.id, body.platforms, body.limit_per_keyword)
                total = len(posts)
                topic_total_posts += total
                kw_result.update({"status": "found" if total > 0 else "empty", "total": total, "posts": posts})

                if body.include_sentiment and total > 0:
                    kw_result["sentiment"] = await _get_sentiment_summary(db, keyword.id)

                if total == 0 and body.auto_crawl:
                    crawl_info = await _queue_crawl(db, kw_text, body.platforms)
                    kw_result["crawl"] = crawl_info
                    crawling_keywords.append(kw_text)
                    # Refresh keyword object setelah crawl buat keyword baru
                    keyword = await _find_keyword(db, kw_text)

            else:
                if body.auto_crawl:
                    crawl_info = await _queue_crawl(db, kw_text, body.platforms)
                    kw_result.update({"status": "crawling", "crawl": crawl_info})
                    crawling_keywords.append(kw_text)
                    keyword = await _find_keyword(db, kw_text)

            keyword_objects.append((kw_text, keyword))
            keyword_results.append(kw_result)

        # Simpan topik ke DB
        if body.save_topic:
            saved_topic = await _save_topic(
                db=db,
                topic_name=topic.name,
                description=topic.description,
                keyword_objects=keyword_objects,
                platforms=body.platforms,
                scheduled_hour=body.scheduled_hour,
                auto_crawl=body.auto_crawl,
            )
            topic_id = str(saved_topic.id)
        else:
            topic_id = None

        topic_results.append({
            "topic_id": topic_id,
            "topic": topic.name.title(),
            "keywords": topic.keywords,
            "total_posts": topic_total_posts,
            "status_per_keyword": {kd["keyword"]: kd["status"] for kd in keyword_results},
            "sentiment_per_keyword": {
                kd["keyword"]: kd.get("sentiment")
                for kd in keyword_results if kd.get("sentiment")
            },
            "results": [p for kd in keyword_results for p in kd.get("posts", [])],
            "crawling": [kd["keyword"] for kd in keyword_results if kd["status"] in ("crawling",)],
        })

    await db.commit()

    overall = "ready"
    if crawling_keywords:
        overall = "partial" if any(t["total_posts"] > 0 for t in topic_results) else "crawling"

    return build_success_response({
        "status": overall,
        "platforms": body.platforms,
        "total_topics": len(topic_results),
        "crawling_keywords": crawling_keywords,
        "note": "Keyword dengan status 'crawling' sedang diproses di background." if crawling_keywords else None,
        "topics": topic_results,
    })


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT: List Semua Topik (Dashboard)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/topics/list", response_model=dict)
async def list_saved_topics(
    is_active: bool = Query(default=True, description="Filter topik aktif saja"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Daftar semua topik yang tersimpan di DB — untuk ditampilkan di dashboard.
    Setiap topik menampilkan keyword-keyword yang terkait beserta statistik singkat.
    """
    from sqlalchemy.orm import selectinload
    q = select(SearchTopic).options(selectinload(SearchTopic.topic_keywords))
    if is_active:
        q = q.where(SearchTopic.is_active == True)
    q = q.order_by(SearchTopic.created_at.desc()).offset(offset).limit(limit)

    topics = (await db.scalars(q)).all()
    total_count = await db.scalar(select(func.count(SearchTopic.id)).where(SearchTopic.is_active == is_active))

    items = []
    for topic in topics:
        # Hitung statistik per topik
        keyword_ids = [stk.keyword_id for stk in topic.topic_keywords]

        total_posts = 0
        total_comments = 0
        if keyword_ids:
            total_posts = (await db.scalar(
                select(func.count(Post.id)).where(
                    Post.keyword_id.in_(keyword_ids),
                    Post.platform.in_(topic.platforms),
                )
            )) or 0

            from app.domain.comments.models import Comment
            total_comments = (await db.scalar(
                select(func.count(Comment.id))
                .join(Post, Comment.post_id == Post.id)
                .where(Post.keyword_id.in_(keyword_ids))
            )) or 0

        items.append({
            "topic_id": str(topic.id),
            "name": topic.name,
            "description": topic.description,
            "platforms": topic.platforms,
            "keywords": [stk.keyword_text for stk in topic.topic_keywords],
            "total_keywords": len(topic.topic_keywords),
            "total_posts": total_posts,
            "total_comments": total_comments,
            "scheduled_hour": topic.scheduled_hour,
            "auto_crawl": topic.auto_crawl,
            "is_active": topic.is_active,
            "created_at": topic.created_at.isoformat(),
            "updated_at": topic.updated_at.isoformat(),
        })

    return build_success_response({
        "total": total_count,
        "offset": offset,
        "items": items,
    })


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT: Detail Satu Topik
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/topics/{topic_id}", response_model=dict)
async def get_topic_detail(
    topic_id: uuid.UUID,
    limit_per_keyword: int = Query(default=10, ge=1, le=100),
    include_sentiment: bool = Query(default=True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Detail satu topik: semua keyword + data posts + sentimen.
    Dipanggil saat user klik topik di dashboard.
    """
    from sqlalchemy.orm import selectinload
    topic = await db.scalar(
        select(SearchTopic)
        .options(selectinload(SearchTopic.topic_keywords))
        .where(SearchTopic.id == topic_id)
    )
    if not topic:
        from app.shared.exceptions import NotFoundError
        raise NotFoundError(f"Topik {topic_id} tidak ditemukan")

    keyword_details = []
    for stk in topic.topic_keywords:
        keyword = await db.scalar(select(Keyword).where(Keyword.id == stk.keyword_id))
        if not keyword:
            continue

        posts = await _get_posts(db, keyword.id, topic.platforms, limit_per_keyword)
        detail: dict = {
            "keyword": stk.keyword_text,
            "keyword_id": str(keyword.id),
            "total_posts": len(posts),
            "posts": posts,
        }
        if include_sentiment and posts:
            detail["sentiment"] = await _get_sentiment_summary(db, keyword.id)

        keyword_details.append(detail)

    return build_success_response({
        "topic_id": str(topic.id),
        "name": topic.name,
        "description": topic.description,
        "platforms": topic.platforms,
        "total_keywords": len(keyword_details),
        "total_posts": sum(k["total_posts"] for k in keyword_details),
        "keyword_details": keyword_details,
        "scheduled_hour": topic.scheduled_hour,
        "created_at": topic.created_at.isoformat(),
        "updated_at": topic.updated_at.isoformat(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT: Hapus / Nonaktifkan Topik
# ─────────────────────────────────────────────────────────────────────────────

@router.delete("/topics/{topic_id}", response_model=dict)
async def delete_topic(
    topic_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Nonaktifkan topik (soft delete — data tidak hilang)."""
    topic = await db.scalar(
        select(SearchTopic).where(SearchTopic.id == topic_id)
    )
    if not topic:
        from app.shared.exceptions import NotFoundError
        raise NotFoundError(f"Topik {topic_id} tidak ditemukan")

    topic.is_active = False
    topic.updated_at = datetime.now(timezone.utc)
    await db.commit()

    return build_success_response({"message": f"Topik '{topic.name}' dinonaktifkan"})
