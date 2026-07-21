"""
Celery tasks untuk Threads — search via EnsembleData.

Mirroring app/workers/tiktok_trending_worker.py, TERPISAH TOTAL (tidak
sentuh/import apa pun punya TikTok/Facebook/Instagram/YouTube).

Beat schedule (di celery_app.py):
  threads-trend-recommendation-daily → threads_trend_recommendation_daily_task
  threads-queue-drain → threads_queue_drain_task (Fase 2, 2026-07-20)

On-demand tasks:
  workers.threads.search_keyword — cari keyword sembarang (manual)
"""
import asyncio
import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="workers.threads_trend_recommendation.daily",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def threads_trend_recommendation_daily_task(self):
    """
    Task harian: search Threads dari topik `trend_recommendations`.

    Ambil maks `settings.threads_trend_daily_budget` topik status='pending'
    (urut score tertinggi), pakai TEKS topiknya langsung sbg keyword
    pencarian (BEDA dari TikTok/Facebook yg butuh related_account -- lihat
    docstring app/services/threads/trend_scrape_service.py). Verifikasi
    hasil sebelum tandai status='used'.
    """
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.infrastructure.redis.connection import reset_redis_client
    from app.services.threads.trend_scrape_service import run_daily_trend_scrape_threads

    async def _run():
        # WAJIB paling awal -- lihat project_redis_event_loop_bug. Tanpa ini,
        # client Redis yg di-cache global masih terikat ke event loop task
        # Celery SEBELUMNYA yg sudah ditutup -> "Event loop is closed"
        # (ditemukan live 2026-07-20 di scrape_runs platform=threads).
        await reset_redis_client()
        async with AsyncSessionLocal() as db:
            return await run_daily_trend_scrape_threads(db)

    try:
        result = asyncio.run(_run())
        logger.info("threads_trend_recommendation_daily done: %s", result)
        return result
    except Exception as exc:
        logger.error("threads_trend_recommendation_daily error: %s", exc)
        raise self.retry(exc=exc)


@celery_app.task(
    name="workers.threads.search_keyword",
    bind=True,
    max_retries=1,
    default_retry_delay=60,
)
def threads_search_keyword_task(
    self,
    keyword: str,
    max_posts: int = 10,
    comments_top_n: int = 3,
):
    """
    Search keyword Threads sembarang secara async (background), via
    EnsembleData. Manual/on-demand, mirroring tiktok_scrape_identifier_task.
    Simpan post+balasan+lexicon ke DB. Bisa dipanggil dari POST /threads/search.
    """
    from datetime import datetime, timezone

    from app.domain.scrape_runs.models import ScrapeRun
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.infrastructure.redis.connection import reset_redis_client
    from app.services.threads.pipeline_service import search_threads_posts

    clean_keyword = keyword.strip()

    async def _run():
        await reset_redis_client()  # lihat project_redis_event_loop_bug
        async with AsyncSessionLocal() as db:
            started_at = datetime.now(timezone.utc)
            scrape_run = ScrapeRun(
                keyword_text=clean_keyword, platform="threads", api_source="ensembledata",
                status="running", triggered_by="manual_cli", started_at=started_at,
            )
            db.add(scrape_run)
            await db.commit()  # commit status='running' segera supaya kelihatan di monitor live

            result = await search_threads_posts(
                db=db, keyword=clean_keyword, max_posts=max_posts,
                comments_top_n=comments_top_n, keyword_id=None,
            )

            scrape_run.status = "success" if result.get("posts_found", 0) > 0 else "failed"
            scrape_run.videos_fetched = result.get("posts_found", 0)
            scrape_run.videos_new = result.get("posts_saved", 0)
            scrape_run.error_message = "; ".join(str(e) for e in result.get("errors", [])[:3]) or None
            scrape_run.finished_at = datetime.now(timezone.utc)
            scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()
            await db.commit()
            return result

    try:
        result = asyncio.run(_run())
        logger.info(
            "threads_search_keyword done: keyword=%s posts_saved=%s errors=%s",
            keyword, result.get("posts_saved"), result.get("errors"),
        )
        return result
    except Exception as exc:
        logger.error("threads_search_keyword error: keyword=%s exc=%s", keyword, exc)
        raise self.retry(exc=exc)


@celery_app.task(name="workers.threads.queue_drain")
def threads_queue_drain_task():
    """
    Fase 2 (2026-07-20): proses `threads_search_queue` (item yg tertunda
    krn slot job Threads penuh ATAU semua token EnsembleData exhausted
    saat POST /threads/search dipanggil -- lihat search_tier_service.py).

    Jalan tiap 10 menit (beat schedule), ambil item status='pending'
    SESUAI sisa slot yg tersedia (tidak melebihi `max_concurrent_jobs`),
    FIFO by requested_at. Item yg kena error KUOTA tetap 'pending' (dicoba
    lagi tick berikutnya) sampai `attempts` melewati `queue_max_attempts`
    -> 'failed_permanent'. Item yg sukses ATAU genuinely 0 hasil (bukan
    error) ditandai 'done'.
    """
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.domain.scrape_runs.models import ScrapeRun
    from app.domain.threads.models import ThreadsSearchQueue
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.infrastructure.redis.connection import reset_redis_client
    from app.services.threads import search_tier_config as cfg
    from app.services.threads import search_tier_service as tier
    from app.services.threads.pipeline_service import search_threads_posts
    from app.shared.ensembledata_errors import is_quota_error

    async def _run():
        await reset_redis_client()  # lihat project_redis_event_loop_bug
        async with AsyncSessionLocal() as db:
            max_jobs = await cfg.get_max_concurrent_jobs()
            max_attempts = await cfg.get_queue_max_attempts()
            running = await tier.count_running_threads_jobs(db)
            budget = max(0, max_jobs - running)
            if budget == 0:
                return {"processed": 0, "note": "slot masih penuh, tunggu tick berikutnya"}

            pending_items = (await db.scalars(
                select(ThreadsSearchQueue)
                .where(ThreadsSearchQueue.status == "pending")
                .order_by(ThreadsSearchQueue.requested_at.asc())
                .limit(budget)
            )).all()

            processed = []
            for item in pending_items:
                started_at = datetime.now(timezone.utc)
                scrape_run = ScrapeRun(
                    keyword_text=item.keyword_text, platform="threads", api_source="ensembledata",
                    status="running", triggered_by="queue_retry", started_at=started_at,
                )
                db.add(scrape_run)
                await db.commit()

                item.attempts += 1
                try:
                    result = await search_threads_posts(
                        db=db, keyword=item.keyword_text, max_posts=10,
                        comments_top_n=3, keyword_id=None,
                    )
                    errors = result.get("errors") or []
                    quota_hit = any(is_quota_error(message=str(e)) for e in errors)

                    scrape_run.status = "success" if result.get("posts_found", 0) > 0 else "failed"
                    scrape_run.videos_fetched = result.get("posts_found", 0)
                    scrape_run.videos_new = result.get("posts_saved", 0)
                    scrape_run.error_message = "; ".join(str(e) for e in errors[:3]) or None
                    scrape_run.finished_at = datetime.now(timezone.utc)
                    scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()

                    if quota_hit and item.attempts < max_attempts:
                        # Tetap 'pending' -- dicoba lagi tick berikutnya, JANGAN dianggap gagal.
                        item.last_error = scrape_run.error_message
                        processed.append({"keyword": item.keyword_text, "outcome": "retry_pending"})
                    elif quota_hit:
                        item.status = "failed_permanent"
                        item.last_error = f"Melebihi batas {max_attempts}x percobaan, terakhir: {scrape_run.error_message}"
                        item.processed_at = datetime.now(timezone.utc)
                        processed.append({"keyword": item.keyword_text, "outcome": "failed_permanent_quota"})
                    else:
                        # Sukses ATAU genuinely 0 hasil (bukan error kuota) -> selesai, bukan retry.
                        item.status = "done"
                        item.last_error = scrape_run.error_message
                        item.processed_at = datetime.now(timezone.utc)
                        processed.append({"keyword": item.keyword_text, "outcome": "done"})
                except Exception as exc:
                    scrape_run.status = "failed"
                    scrape_run.error_message = str(exc)[:1000]
                    scrape_run.finished_at = datetime.now(timezone.utc)
                    scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()
                    if item.attempts >= max_attempts:
                        item.status = "failed_permanent"
                        item.processed_at = datetime.now(timezone.utc)
                        processed.append({"keyword": item.keyword_text, "outcome": "failed_permanent_error"})
                    else:
                        processed.append({"keyword": item.keyword_text, "outcome": "retry_pending_error"})
                    item.last_error = str(exc)[:1000]

                await db.commit()

            return {"processed": len(processed), "results": processed}

    result = asyncio.run(_run())
    logger.info("threads_queue_drain done: %s", result)
    return result
