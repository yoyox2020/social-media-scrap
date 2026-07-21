"""
Celery task Views Refresh Agent -- Beat jalan TIAP 15 MENIT (granularitas
terkecil), tapi cuma benar2 eksekusi kalau sudah waktunya sesuai
interval_minutes di Redis -- pola SAMA dgn youtube_metadata_worker.py.
"""
import asyncio
import logging
from datetime import datetime, timezone

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="workers.views_refresh_agent.check",
    bind=True,
    max_retries=1,
    default_retry_delay=300,
)
def views_refresh_agent_check_task(self):
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.infrastructure.redis.connection import reset_redis_client
    from app.services.views_refresh_agent import config as cfg
    from app.services.views_refresh_agent.agent import run_views_refresh_agent

    async def _run():
        # WAJIB paling awal -- lihat docstring reset_redis_client() &
        # app/workers/youtube_discovery_worker.py (bug "Event loop is
        # closed", 2026-07-18).
        await reset_redis_client()

        if await cfg.is_running():
            logger.info("views_refresh_agent_check: run sebelumnya masih berjalan, skip tick ini")
            return {"skipped": "already_running"}

        interval_minutes = await cfg.get_interval_minutes()
        last_run_iso = await cfg.get_last_run_at()
        now = datetime.now(timezone.utc)
        if last_run_iso:
            last_run = datetime.fromisoformat(last_run_iso)
            elapsed_minutes = (now - last_run).total_seconds() / 60
            if elapsed_minutes < interval_minutes:
                logger.info(
                    "views_refresh_agent_check: belum waktunya (elapsed=%.1fmnt, interval=%dmnt), skip",
                    elapsed_minutes, interval_minutes,
                )
                return {"skipped": "not_due_yet", "elapsed_minutes": round(elapsed_minutes, 1), "interval_minutes": interval_minutes}

        acquired = await cfg.acquire_running_lock()
        if not acquired:
            logger.info("views_refresh_agent_check: gagal ambil lock (race dgn proses lain), skip")
            return {"skipped": "lock_race"}

        try:
            async with AsyncSessionLocal() as db:
                result = await run_views_refresh_agent(db)
            await cfg.set_last_run_at(now.isoformat())
            return result
        finally:
            await cfg.release_running_lock()

    try:
        result = asyncio.run(_run())
        logger.info("views_refresh_agent_check done: %s", result)
        return result
    except Exception as exc:
        logger.error("views_refresh_agent_check error: %s", exc)
        raise self.retry(exc=exc)
