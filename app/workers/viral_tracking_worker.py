"""
Celery workers untuk Viral Tracking Pipeline.

Flow otomatis:
  detect_viral_posts_task (setiap 6 jam)
    → detect_and_create_trackers()
    → queue viral_channel_daily_scrape_task per tracker baru

  viral_tracking_daily_check_task (setiap hari jam 12:00 WIB)
    → resume_active_trackers() — tandai expired, cari yang belum scraping hari ini
    → queue viral_channel_daily_scrape_task per tracker aktif

  viral_channel_daily_scrape_task(tracker_id)
    → run_daily_channel_scrape()
    → queue check_flagged_commenters_task

  check_flagged_commenters_task(tracker_id)
    → check_and_flag_commenters()
    → queue viral_channel_daily_scrape_task untuk tracker flagged_commenter baru
"""
from __future__ import annotations

import asyncio
import uuid

from app.workers.celery_app import celery_app


def _get_fresh_session():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from app.shared.config import settings

    fresh_engine = create_async_engine(
        settings.database_url, pool_size=2, max_overflow=0, echo=False
    )
    session_factory = async_sessionmaker(
        bind=fresh_engine, class_=AsyncSession,
        expire_on_commit=False, autocommit=False, autoflush=False,
    )
    return fresh_engine, session_factory


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Deteksi post viral → buat tracker baru (setiap 6 jam)
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="workers.viral_tracking.detect_viral_posts",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def detect_viral_posts_task(self):
    """Cari post >=1M views yang belum punya tracker, buat ViralChannelTracker baru."""
    from app.services.viral_tracking.service import detect_and_create_trackers

    async def _run():
        engine, factory = _get_fresh_session()
        try:
            async with factory() as db:
                new_tracker_ids = await detect_and_create_trackers(db)
            for tid in new_tracker_ids:
                viral_channel_daily_scrape_task.delay(str(tid))
            return {"new_trackers": len(new_tracker_ids), "tracker_ids": [str(t) for t in new_tracker_ids]}
        finally:
            await engine.dispose()

    try:
        return asyncio.run(_run())
    except Exception as exc:
        raise self.retry(exc=exc)


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Harian: resume tracker aktif, tandai expired (setiap hari 03:00)
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="workers.viral_tracking.daily_check",
    bind=True,
    max_retries=1,
    default_retry_delay=120,
)
def viral_tracking_daily_check_task(self):
    """Resume semua tracker aktif (channel + keyword) yang belum scraping hari ini."""
    from app.services.viral_tracking.service import resume_active_trackers, resume_active_keyword_trackers

    async def _run():
        engine, factory = _get_fresh_session()
        try:
            async with factory() as db:
                ch_result = await resume_active_trackers(db)
                kw_result = await resume_active_keyword_trackers(db)
            for tid_str in ch_result["needs_scrape"]:
                viral_channel_daily_scrape_task.delay(tid_str)
            for tid_str in kw_result["needs_scrape"]:
                viral_keyword_daily_scrape_task.delay(tid_str)
            return {
                "channel": ch_result,
                "keyword": kw_result,
            }
        finally:
            await engine.dispose()

    try:
        return asyncio.run(_run())
    except Exception as exc:
        raise self.retry(exc=exc)


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Scrape 5 video dari channel tracker
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="workers.viral_tracking.channel_daily_scrape",
    bind=True,
    max_retries=3,
    default_retry_delay=300,
)
def viral_channel_daily_scrape_task(self, tracker_id: str):
    """Scrape hingga 5 video terbaru dari channel tracker, lalu cek commenter."""
    from app.services.viral_tracking.service import run_daily_channel_scrape

    async def _run():
        engine, factory = _get_fresh_session()
        try:
            async with factory() as db:
                new_posts = await run_daily_channel_scrape(db, uuid.UUID(tracker_id))
            # Setelah scrape, cek commenter
            check_flagged_commenters_task.delay(tracker_id)
            return {"tracker_id": tracker_id, "new_posts": new_posts}
        finally:
            await engine.dispose()

    try:
        return asyncio.run(_run())
    except Exception as exc:
        raise self.retry(exc=exc)


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Cek dan flag commenter aktif pada tracker
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="workers.viral_tracking.check_flagged_commenters",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def check_flagged_commenters_task(self, tracker_id: str):
    """Flag akun yang komentar >10x, buat tracker baru untuk tiap commenter (jika channel valid)."""
    from app.services.viral_tracking.service import check_and_flag_commenters

    async def _run():
        engine, factory = _get_fresh_session()
        try:
            async with factory() as db:
                new_flagged_ids = await check_and_flag_commenters(db, uuid.UUID(tracker_id))
            # Queue scrape untuk tracker flagged_commenter yang baru dibuat
            # (analysis_tracker_id dari FlaggedAccount) — perlu query DB sekali lagi
            if new_flagged_ids:
                await _queue_analysis_trackers(new_flagged_ids)
            return {"tracker_id": tracker_id, "new_flagged": len(new_flagged_ids)}
        finally:
            await engine.dispose()

    try:
        return asyncio.run(_run())
    except Exception as exc:
        raise self.retry(exc=exc)


# ─────────────────────────────────────────────────────────────────────────────
# 5.  Keyword-based daily scrape (7 hari per keyword tracker)
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="workers.viral_tracking.keyword_daily_scrape",
    bind=True,
    max_retries=3,
    default_retry_delay=300,
)
def viral_keyword_daily_scrape_task(self, tracker_id: str):
    """Scrape video YouTube berdasarkan keyword tracker, kumpulkan komentar per video."""
    from app.services.viral_tracking.service import run_daily_keyword_scrape

    async def _run():
        engine, factory = _get_fresh_session()
        try:
            async with factory() as db:
                new_posts = await run_daily_keyword_scrape(db, uuid.UUID(tracker_id))
            return {"tracker_id": tracker_id, "new_posts": new_posts}
        finally:
            await engine.dispose()

    try:
        return asyncio.run(_run())
    except Exception as exc:
        raise self.retry(exc=exc)


async def _queue_analysis_trackers(flagged_ids: list[uuid.UUID]) -> None:
    """Baca analysis_tracker_id dari FlaggedAccount yang baru, queue scrapenya."""
    from sqlalchemy import select
    from app.domain.viral_tracking.models import FlaggedAccount

    engine, factory = _get_fresh_session()
    try:
        async with factory() as db:
            rows = await db.execute(
                select(FlaggedAccount.analysis_tracker_id).where(
                    FlaggedAccount.id.in_(flagged_ids),
                    FlaggedAccount.analysis_tracker_id.isnot(None),
                )
            )
            for (at_id,) in rows.fetchall():
                viral_channel_daily_scrape_task.delay(str(at_id))
    finally:
        await engine.dispose()
