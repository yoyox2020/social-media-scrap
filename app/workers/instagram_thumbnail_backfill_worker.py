"""
Celery task Instagram Thumbnail Backfill Agent -- lihat
app/services/instagram_thumbnail_backfill/service.py utk detail lengkap.

Beat schedule (di celery_app.py): harian, budget kecil (default 5 akun/run)
-- kendali biaya Apify/EnsembleData, backfill BUKAN pekerjaan mendesak
(cuma foto post lama), jadi cukup pelan-pelan tiap hari.
"""
import asyncio
import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="workers.instagram_thumbnail_backfill.daily",
    bind=True,
    max_retries=1,
    default_retry_delay=300,
)
def instagram_thumbnail_backfill_daily_task(self):
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.infrastructure.redis.connection import reset_redis_client
    from app.services.instagram_thumbnail_backfill.service import run_thumbnail_backfill

    async def _run():
        # WAJIB paling awal -- lihat docstring reset_redis_client() /
        # project_redis_event_loop_bug (client Redis global bisa terikat
        # event loop task Celery SEBELUMNYA yg sudah ditutup).
        await reset_redis_client()
        async with AsyncSessionLocal() as db:
            return await run_thumbnail_backfill(db)

    try:
        result = asyncio.run(_run())
        logger.info("instagram_thumbnail_backfill_daily done: %s", result)
        return result
    except Exception as exc:
        logger.error("instagram_thumbnail_backfill_daily error: %s", exc)
        raise self.retry(exc=exc)
