"""
Celery workers untuk YouTube Intelligence Pipeline.

Flow otomatis (dipanggil Celery Beat setiap 1 jam):
  fetch_trending_youtube_task
    → ambil Google Trends → simpan TrendingTopic → buat/update Keyword
    → queue collect_youtube_pipeline_task per keyword

  collect_youtube_pipeline_task(keyword_id)
    → cari video YouTube (EnsembleData /youtube/search)
    → simpan Post ke DB
    → queue collect_youtube_comments_task per video baru

  collect_youtube_comments_task(post_id, keyword_id)
    → ambil komentar (semua halaman via cursor)
    → simpan Comment ke DB
    → jalankan lexicon sentiment
    → simpan LexiconAnalysis ke DB
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _get_fresh_session():
    """
    Buat AsyncSession baru dengan engine baru per-task.
    Diperlukan karena Celery ForkPoolWorker mewarisi engine dari parent process,
    tapi asyncpg connections terikat ke event loop parent yang sudah tidak valid
    di child process ketika asyncio.run() membuat event loop baru.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from app.shared.config import settings

    fresh_engine = create_async_engine(
        settings.database_url,
        pool_size=2,
        max_overflow=0,
        echo=False,
    )
    session_factory = async_sessionmaker(
        bind=fresh_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    return fresh_engine, session_factory


# ─────────────────────────────────────────────────────────────────────────────
# 1.  SCHEDULED TASK — dipanggil Celery Beat setiap 1 jam
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="workers.youtube.fetch_trending",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def fetch_trending_youtube_task(
    self,
    project_id: str = "",
    geo: str = "ID",
    period: str = "24h",
    limit: int = 10,
    max_pages_per_keyword: int = 2,
) -> dict:
    """
    Cron task: ambil trending → simpan → buat keyword → queue pipeline.

    project_id boleh kosong — task akan otomatis pilih project pertama di DB.
    """
    try:
        return asyncio.run(
            _run_fetch_trending(project_id, geo, period, limit, max_pages_per_keyword)
        )
    except Exception as exc:
        raise self.retry(exc=exc)


async def _run_fetch_trending(
    project_id: str,
    geo: str,
    period: str,
    limit: int,
    max_pages_per_keyword: int,
) -> dict:
    from sqlalchemy import select
    from app.domain.projects.models import Project
    from app.services.youtube.pipeline_service import fetch_and_store_trending
    from app.services.youtube.schemas import TrendingFetchRequest

    fresh_engine, session_factory = _get_fresh_session()
    async with session_factory() as db:
        # Auto-detect project jika project_id kosong
        if not project_id:
            pid = await db.scalar(
                select(Project.id).where(Project.is_active == True).limit(1)
            )
            if not pid:
                return {"error": "Tidak ada project aktif di DB. Buat project dulu via API."}
            project_id = str(pid)

        request = TrendingFetchRequest(
            geo=geo,
            period=period,
            limit=limit,
            project_id=uuid.UUID(project_id),
            auto_collect=True,
            max_pages_per_keyword=max_pages_per_keyword,
        )
        response = await fetch_and_store_trending(db, request)

    await fresh_engine.dispose()
    return {
        "geo": response.geo,
        "period": response.period,
        "topics_fetched": len(response.items),
        "keywords_created": response.keywords_created,
        "jobs_queued": response.jobs_queued,
        "project_id": project_id,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2.  PIPELINE PER KEYWORD — video → dispatch comment tasks
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="workers.youtube.collect_pipeline",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def collect_youtube_pipeline_task(
    self,
    keyword_id: str,
    max_pages: int = 2,
    max_comments_per_video: int = 100,
    max_comment_pages: int = 3,
) -> dict:
    """
    Pipeline per keyword:
      1. Collect video dari YouTube
      2. Untuk setiap video baru → dispatch comment task
    """
    try:
        return asyncio.run(
            _run_youtube_pipeline(
                keyword_id, max_pages, max_comments_per_video, max_comment_pages
            )
        )
    except Exception as exc:
        raise self.retry(exc=exc)


async def _run_youtube_pipeline(
    keyword_id: str,
    max_pages: int,
    max_comments_per_video: int,
    max_comment_pages: int,
) -> dict:
    from sqlalchemy import select, desc
    from app.domain.posts.models import Post
    from app.repositories.keyword_repository import KeywordRepository
    from app.services.collector.service import CollectorService

    kw_uuid = uuid.UUID(keyword_id)
    fresh_engine, session_factory = _get_fresh_session()

    # ── Step 1: collect videos ────────────────────────────────────────────────
    logger.info("[Worker/Pipeline] MULAI — keyword_id=%s", keyword_id)
    async with session_factory() as db:
        kw_repo = KeywordRepository(db)
        svc = CollectorService(kw_repo)
        collection_result = await svc.collect_for_platform(
            keyword_id=kw_uuid,
            platform="youtube",
            max_pages=max_pages,
            db=db,
        )

    videos_new = collection_result.new_posts
    videos_fetched = collection_result.total_fetched
    logger.info(
        "[Worker/Pipeline] Collect video selesai — fetched=%d new=%d duplikat=%d errors=%s",
        videos_fetched, videos_new, collection_result.skipped_duplicates, collection_result.errors,
    )

    # ── Step 2: ambil video terbaru & dispatch comment task per video ──────────
    fetch_limit = max(videos_fetched, 10)

    async with session_factory() as db:
        result = await db.scalars(
            select(Post)
            .where(Post.keyword_id == kw_uuid, Post.platform == "youtube")
            .order_by(desc(Post.collected_at))
            .limit(fetch_limit)
        )
        posts = list(result.all())

    await fresh_engine.dispose()

    jobs_dispatched = 0
    for post in posts:
        collect_youtube_comments_task.delay(
            str(post.id),
            keyword_id,
            max_comments=max_comments_per_video,
            max_pages=max_comment_pages,
        )
        jobs_dispatched += 1

    logger.info("[Worker/Pipeline] Dispatch comment tasks: %d task dikirim ke queue", jobs_dispatched)

    return {
        "keyword_id": keyword_id,
        "videos_new": videos_new,
        "videos_fetched": videos_fetched,
        "comment_jobs_dispatched": jobs_dispatched,
        "errors": collection_result.errors,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3.  COMMENT + SENTIMENT — satu video, semua halaman komentar
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="workers.youtube.collect_comments",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def collect_youtube_comments_task(
    self,
    post_id: str,
    keyword_id: str,
    max_comments: int = 100,
    max_pages: int = 3,
) -> dict:
    """
    Kumpulkan komentar + jalankan lexicon sentiment untuk satu video.
    Ambil semua halaman komentar sampai cursor habis atau limit tercapai.
    """
    try:
        return asyncio.run(
            _run_comments(post_id, keyword_id, max_comments, max_pages)
        )
    except Exception as exc:
        raise self.retry(exc=exc)


async def _run_comments(
    post_id: str,
    keyword_id: str,
    max_comments: int,
    max_pages: int,
) -> dict:
    from app.services.youtube.pipeline_service import collect_comments_for_video

    logger.info("[Worker/Comments] MULAI — post_id=%s keyword_id=%s max=%d", post_id, keyword_id, max_comments)
    fresh_engine, session_factory = _get_fresh_session()
    async with session_factory() as db:
        result = await collect_comments_for_video(
            db=db,
            post_id=uuid.UUID(post_id),
            keyword_id=uuid.UUID(keyword_id),
            max_comments=max_comments,
            max_pages=max_pages,
        )
    await fresh_engine.dispose()
    data = result.model_dump()
    logger.info(
        "[Worker/Comments] SELESAI — post_id=%s komentar_baru=%s",
        post_id, data.get("new_comments", data.get("comments_saved", "?")),
    )
    return data
