"""
Celery workers untuk YouTube Intelligence Pipeline.

Flow otomatis (Celery Beat setiap hari jam 12.00 WIB):
  fetch_trending_youtube_task
    → ambil Google Trends → simpan TrendingTopic → buat/update Keyword
    → queue collect_youtube_pipeline_task per keyword

  collect_youtube_pipeline_task(keyword_id)
    → catat ScrapeRun (status=running)
    → cari video YouTube (EnsembleData, fallback ke YouTube Data API v3 saat 495)
    → simpan Post ke DB
    → update ScrapeRun (status=success/failed)
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
from datetime import datetime, timezone

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _get_fresh_session():
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
# Helper: catat ScrapeRun ke DB
# ─────────────────────────────────────────────────────────────────────────────

async def _create_scrape_run(session_factory, keyword_id: str, keyword_text: str, triggered_by: str = "celery_beat") -> str:
    """Buat record ScrapeRun baru (status=running), return run_id."""
    from app.domain.scrape_runs.models import ScrapeRun

    run = ScrapeRun(
        id=uuid.uuid4(),
        keyword_id=uuid.UUID(keyword_id) if keyword_id else None,
        keyword_text=keyword_text,
        platform="youtube",
        api_source="ensembledata",
        status="running",
        triggered_by=triggered_by,
        started_at=datetime.now(timezone.utc),
    )
    async with session_factory() as db:
        db.add(run)
        await db.commit()
    return str(run.id)


async def _finish_scrape_run(
    session_factory,
    run_id: str,
    status: str,
    api_source: str = "ensembledata",
    videos_fetched: int = 0,
    videos_new: int = 0,
    videos_duplicate: int = 0,
    error_message: str | None = None,
    started_at: datetime | None = None,
) -> None:
    """Update ScrapeRun dengan hasil akhir."""
    from sqlalchemy import update
    from app.domain.scrape_runs.models import ScrapeRun

    finished_at = datetime.now(timezone.utc)
    duration = (finished_at - started_at).total_seconds() if started_at else None

    async with session_factory() as db:
        await db.execute(
            update(ScrapeRun)
            .where(ScrapeRun.id == uuid.UUID(run_id))
            .values(
                status=status,
                api_source=api_source,
                videos_fetched=videos_fetched,
                videos_new=videos_new,
                videos_duplicate=videos_duplicate,
                error_message=error_message,
                finished_at=finished_at,
                duration_seconds=duration,
            )
        )
        await db.commit()


async def _update_comments_count(
    session_factory,
    run_id: str,
    comments_fetched: int,
    comments_new: int,
    error: str | None = None,
) -> None:
    """Update jumlah komentar di ScrapeRun setelah comment task selesai."""
    from sqlalchemy import update, text
    from app.domain.scrape_runs.models import ScrapeRun

    run_uuid = uuid.UUID(run_id)
    async with session_factory() as db:
        await db.execute(
            update(ScrapeRun)
            .where(ScrapeRun.id == run_uuid)
            .values(
                comments_fetched=ScrapeRun.comments_fetched + comments_fetched,
                comments_new=ScrapeRun.comments_new + comments_new,
            )
        )
        if error:
            await db.execute(
                text("""
                    UPDATE scrape_runs
                    SET error_message = CASE
                        WHEN error_message IS NULL THEN :err
                        ELSE error_message || '; ' || :err
                    END
                    WHERE id = :run_id
                """),
                {"err": error[:500], "run_id": str(run_uuid)},
            )
        await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# 1.  SCHEDULED TASK — dipanggil Celery Beat setiap hari jam 12.00 WIB
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
    """Cron task: ambil trending → simpan → buat keyword → queue pipeline."""
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

    logger.info("[Trending] Mulai fetch trending — geo=%s period=%s limit=%d", geo, period, limit)
    fresh_engine, session_factory = _get_fresh_session()
    async with session_factory() as db:
        if not project_id:
            pid = await db.scalar(
                select(Project.id).where(Project.is_active == True).limit(1)
            )
            if not pid:
                logger.error("[Trending] Tidak ada project aktif di DB")
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
    logger.info(
        "[Trending] Selesai — topics=%d keywords_created=%d jobs_queued=%d",
        len(response.items), response.keywords_created, response.jobs_queued,
    )
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
    max_pages: int = 1,
    max_comments_per_video: int = 5,
    max_comment_pages: int = 1,
    triggered_by: str = "celery_beat",
) -> dict:
    """Pipeline per keyword: collect video → dispatch comment tasks."""
    try:
        return asyncio.run(
            _run_youtube_pipeline(
                keyword_id, max_pages, max_comments_per_video, max_comment_pages, triggered_by
            )
        )
    except Exception as exc:
        raise self.retry(exc=exc)


async def _run_youtube_pipeline(
    keyword_id: str,
    max_pages: int,
    max_comments_per_video: int,
    max_comment_pages: int,
    triggered_by: str,
) -> dict:
    from sqlalchemy import select, desc
    from app.domain.posts.models import Post
    from app.domain.keywords.models import Keyword
    from app.repositories.keyword_repository import KeywordRepository
    from app.services.collector.service import CollectorService

    kw_uuid = uuid.UUID(keyword_id)
    fresh_engine, session_factory = _get_fresh_session()
    started_at = datetime.now(timezone.utc)

    # Ambil nama keyword untuk log
    async with session_factory() as db:
        kw = await db.get(Keyword, kw_uuid)
        keyword_text = kw.keyword if kw else keyword_id

    if not kw:
        logger.error("[Worker/Pipeline] Keyword tidak ditemukan di DB — id=%s", keyword_id)
        await fresh_engine.dispose()
        return {"error": f"Keyword {keyword_id} tidak ada di DB", "keyword_id": keyword_id}

    logger.info("[Worker/Pipeline] MULAI — keyword=%r triggered_by=%s", keyword_text, triggered_by)

    # Catat ke scrape_runs
    run_id = await _create_scrape_run(session_factory, keyword_id, keyword_text, triggered_by)
    logger.info("[Worker/Pipeline] ScrapeRun dibuat — run_id=%s", run_id)

    # ── Collect videos ─────────────────────────────────────────────────────────
    collection_result = None
    api_source = "ensembledata"
    try:
        async with session_factory() as db:
            kw_repo = KeywordRepository(db)
            svc = CollectorService(kw_repo)
            collection_result = await svc.collect_for_platform(
                keyword_id=kw_uuid,
                platform="youtube",
                max_pages=max_pages,
                db=db,
            )

        # Deteksi apakah fallback ke YouTube Data API dipakai -- dari flag
        # used_fallback (marker `_source` di raw response connector), BUKAN
        # tebak dari teks error. Bug lama: kalau fallback-nya BERHASIL (tanpa
        # exception), `errors` kosong jadi cek string "495" di errors tidak
        # pernah kena, scrape_runs.api_source salah tercatat "ensembledata"
        # padahal sebenarnya sudah pakai YouTube Data API v3 (ditemukan
        # 2026-07-16 lewat cross-check posts.metadata->>'source').
        if collection_result.used_fallback:
            api_source = "youtube_data_api"
        elif collection_result.total_fetched == 0 and collection_result.errors:
            api_source = "unknown"

        videos_new = collection_result.new_posts
        videos_fetched = collection_result.total_fetched

        logger.info(
            "[Worker/Pipeline] Video selesai — api=%s fetched=%d new=%d duplikat=%d errors=%s",
            api_source, videos_fetched, videos_new,
            collection_result.skipped_duplicates, collection_result.errors,
        )

        await _finish_scrape_run(
            session_factory, run_id,
            status="success" if not collection_result.errors else "fallback",
            api_source=api_source,
            videos_fetched=videos_fetched,
            videos_new=videos_new,
            videos_duplicate=collection_result.skipped_duplicates,
            error_message="; ".join(collection_result.errors) if collection_result.errors else None,
            started_at=started_at,
        )

    except Exception as exc:
        logger.error("[Worker/Pipeline] ERROR — keyword=%r error=%s", keyword_text, exc)
        await _finish_scrape_run(
            session_factory, run_id,
            status="failed",
            api_source=api_source,
            error_message=str(exc),
            started_at=started_at,
        )
        await fresh_engine.dispose()
        raise

    # ── Dispatch comment tasks — max 2 video per run untuk hemat token ───────
    async with session_factory() as db:
        result = await db.scalars(
            select(Post)
            .where(Post.keyword_id == kw_uuid, Post.platform == "youtube")
            .order_by(desc(Post.collected_at))
            .limit(2)
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
            run_id=run_id,
        )
        jobs_dispatched += 1

    logger.info("[Worker/Pipeline] Dispatch comment tasks: %d task dikirim", jobs_dispatched)

    return {
        "run_id": run_id,
        "keyword_id": keyword_id,
        "keyword_text": keyword_text,
        "api_source": api_source,
        "videos_new": videos_new,
        "videos_fetched": videos_fetched,
        "comment_jobs_dispatched": jobs_dispatched,
        "errors": collection_result.errors if collection_result else [],
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
    run_id: str | None = None,
) -> dict:
    """Kumpulkan komentar + lexicon sentiment untuk satu video."""
    try:
        return asyncio.run(
            _run_comments(post_id, keyword_id, max_comments, max_pages, run_id)
        )
    except Exception as exc:
        raise self.retry(exc=exc)


async def _run_comments(
    post_id: str,
    keyword_id: str,
    max_comments: int,
    max_pages: int,
    run_id: str | None,
) -> dict:
    from app.services.youtube.pipeline_service import collect_comments_for_video

    logger.info("[Worker/Comments] MULAI — post_id=%s", post_id)
    fresh_engine, session_factory = _get_fresh_session()
    async with session_factory() as db:
        result = await collect_comments_for_video(
            db=db,
            post_id=uuid.UUID(post_id),
            keyword_id=uuid.UUID(keyword_id),
            max_comments=max_comments,
            max_pages=max_pages,
        )
    data = result.model_dump()

    comments_new     = data.get("comments_new", 0) or 0
    comments_fetched = data.get("comments_fetched", 0) or 0
    comment_errors   = data.get("errors") or []

    logger.info("[Worker/Comments] SELESAI — post_id=%s komentar_baru=%d errors=%s",
                post_id, comments_new, comment_errors)

    # Update hitungan komentar di ScrapeRun parent (jika ada run_id)
    if run_id:
        try:
            await _update_comments_count(
                session_factory, run_id, comments_fetched, comments_new,
                error="; ".join(comment_errors) if comment_errors else None,
            )
        except Exception as exc:
            logger.warning("[Worker/Comments] Gagal update ScrapeRun comments: %s", exc)

    await fresh_engine.dispose()
    return data


# ─────────────────────────────────────────────────────────────────────────────
# 4.  BACKFILL views/likes/comments yang stuck 0 -- ditambahkan 2026-07-16
#     setelah ditemukan ~5.400 post YouTube ke-stuck views=0 permanen (enrichment
#     gagal sesaat, tidak pernah dicoba ulang -- root cause dijelaskan di
#     app/integrations/youtube_data_api/client.py get_videos_statistics(), yang
#     sekarang sudah dikasih retry supaya tidak terjadi lagi ke depan). Task ini
#     BUKAN untuk kejadian baru (itu tugas retry-nya) -- ini KHUSUS beresin post
#     LAMA yang sudah terlanjur stuck.
#
#     Dikontrol via flag Redis (bukan Celery Beat/env var) supaya bisa
#     dinyala/dimatikan LIVE dari dashboard (POST /youtube/backfill-stats/toggle)
#     tanpa perlu restart apa pun -- lihat app/api/v1/youtube/router.py.
#     Flag dicek ULANG tiap batch (bukan cuma di awal) supaya toggle OFF
#     bikin task berhenti AMAN dalam hitungan detik (batch berikutnya), BUKAN
#     langsung di-terminate paksa di tengah operasi DB (resiko data korup).
# ─────────────────────────────────────────────────────────────────────────────

BACKFILL_REDIS_KEY = "youtube:backfill:enabled"
BACKFILL_SCRAPE_KEYWORD = "youtube_backfill_stats"
BACKFILL_BATCH_SIZE = 50


async def _backfill_enabled() -> bool:
    from app.infrastructure.redis.connection import get_redis

    redis = await get_redis()
    return (await redis.get(BACKFILL_REDIS_KEY)) == "true"


@celery_app.task(name="workers.youtube.backfill_stats", bind=True, max_retries=0)
def backfill_youtube_stats_task(self):
    """Dipicu manual via toggle ON (bukan Celery Beat) -- lihat docstring blok di atas."""
    try:
        return asyncio.run(_run_backfill())
    except Exception as exc:
        logger.error("[Worker/Backfill] error fatal: %s", exc)
        raise


async def _run_backfill() -> dict:
    from sqlalchemy import func, select, text
    from sqlalchemy.orm.attributes import flag_modified

    from app.domain.posts.models import Post
    from app.domain.scrape_runs.models import ScrapeRun
    from app.integrations.youtube_data_api.client import YouTubeDataAPIClient
    from app.shared.config import settings

    if not await _backfill_enabled():
        logger.info("[Worker/Backfill] flag OFF, tidak jalan (dicek di awal)")
        return {"status": "disabled", "processed": 0, "updated": 0}

    if not settings.youtube_data_api_key:
        logger.warning("[Worker/Backfill] YOUTUBE_DATA_API_KEY belum di-set, dibatalkan")
        return {"status": "no_api_key", "processed": 0, "updated": 0}

    fresh_engine, session_factory = _get_fresh_session()
    client = YouTubeDataAPIClient(api_key=settings.youtube_data_api_key)
    started_at = datetime.now(timezone.utc)

    async with session_factory() as db:
        total_target = await db.scalar(
            select(func.count(Post.id)).where(
                Post.platform == "youtube",
                text("(metadata->>'views')::bigint = 0"),
                text("metadata->>'stats_backfill_checked_at' IS NULL"),
            )
        ) or 0

        run = ScrapeRun(
            keyword_text=BACKFILL_SCRAPE_KEYWORD, platform="youtube_backfill",
            api_source="youtube_data_api", status="running", triggered_by="manual_toggle",
            started_at=started_at, videos_fetched=total_target, videos_new=0, videos_duplicate=0,
        )
        db.add(run)
        await db.commit()
        run_id = run.id

    logger.info("[Worker/Backfill] MULAI -- target=%d post", total_target)

    updated_total = 0
    checked_total = 0
    stop_reason = "completed"

    while True:
        if not await _backfill_enabled():
            stop_reason = "stopped_by_user"
            logger.info("[Worker/Backfill] flag dimatikan, berhenti aman di batch berikutnya")
            break

        async with session_factory() as db:
            batch = list((await db.scalars(
                select(Post).where(
                    Post.platform == "youtube",
                    text("(metadata->>'views')::bigint = 0"),
                    text("metadata->>'stats_backfill_checked_at' IS NULL"),
                ).order_by(Post.id).limit(BACKFILL_BATCH_SIZE)
            )).all())

            if not batch:
                break

            external_ids = [p.external_id for p in batch if p.external_id]
            try:
                stats_by_id = await client.get_videos_statistics(external_ids)
            except Exception as exc:
                # Chunk ini gagal walau sudah di-retry 3x (get_videos_statistics) --
                # JANGAN tandai checked (biar dicoba lagi run berikutnya), stop
                # run ini supaya tidak infinite-loop kena error yang sama terus.
                logger.error("[Worker/Backfill] batch gagal total, berhenti: %s", exc)
                stop_reason = "error"
                break

            now_iso = datetime.now(timezone.utc).isoformat()
            batch_updated = 0
            for p in batch:
                stats = stats_by_id.get(p.external_id)
                if stats:
                    p.metadata_["views"] = stats["views"]
                    p.metadata_["likes"] = stats["likes"]
                    p.metadata_["comments"] = stats["comments"]
                    if stats["views"] > 0:
                        batch_updated += 1
                p.metadata_["stats_backfill_checked_at"] = now_iso
                # WAJIB -- SQLAlchemy TIDAK otomatis mendeteksi mutasi in-place
                # dict JSON pada objek yg SUDAH persisted (beda dari kasus insert
                # baru di post_repository.bulk_create), tanpa ini UPDATE-nya
                # senyap tidak pernah ke-commit ke DB.
                flag_modified(p, "metadata_")

            await db.commit()
            updated_total += batch_updated
            checked_total += len(batch)

            # Update progress supaya kelihatan live di dashboard, bukan cuma di akhir
            run_row = await db.get(ScrapeRun, run_id)
            if run_row:
                run_row.videos_new = updated_total
                run_row.videos_duplicate = checked_total - updated_total
                await db.commit()

        logger.info(
            "[Worker/Backfill] batch selesai -- checked_total=%d/%d updated_total=%d",
            checked_total, total_target, updated_total,
        )
        await asyncio.sleep(0.5)  # jeda kecil, jangan hajar API bertubi-tubi

    async with session_factory() as db:
        run_row = await db.get(ScrapeRun, run_id)
        if run_row:
            run_row.status = "success" if stop_reason in ("completed", "stopped_by_user") else "failed"
            run_row.videos_new = updated_total
            run_row.videos_duplicate = checked_total - updated_total
            run_row.error_message = None if stop_reason != "error" else "Batch gagal setelah 3x retry, lihat log worker"
            run_row.finished_at = datetime.now(timezone.utc)
            run_row.duration_seconds = (run_row.finished_at - started_at).total_seconds()
            await db.commit()

        # completed beneran (bukan berhenti manual) -> matikan flag sendiri,
        # supaya toggle di dashboard otomatis balik "OFF" dan jelas kelihatan
        # selesai, bukan nyangkut "ON" padahal sudah tidak ada kerjaan.
        if stop_reason == "completed":
            from app.infrastructure.redis.connection import get_redis
            redis = await get_redis()
            await redis.set(BACKFILL_REDIS_KEY, "false")

    await fresh_engine.dispose()
    logger.info(
        "[Worker/Backfill] SELESAI -- reason=%s checked=%d updated=%d",
        stop_reason, checked_total, updated_total,
    )
    return {"status": stop_reason, "processed": checked_total, "updated": updated_total}
