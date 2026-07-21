"""
Celery task Metadata Agent -- Beat jalan TIAP 15 MENIT (granularitas
terkecil dari pilihan interval 15/30/60/240 menit), tapi task ini cuma
benar2 eksekusi kalau sudah waktunya sesuai interval_minutes di Redis --
pola SAMA dgn app/workers/youtube_discovery_worker.py (dynamic schedule via
fixed-interval check).
"""
import asyncio
import logging
from datetime import datetime, timezone

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="workers.youtube_metadata.check",
    bind=True,
    max_retries=1,
    default_retry_delay=300,
)
def youtube_metadata_check_task(self):
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.infrastructure.redis.connection import reset_redis_client
    from app.services.youtube_metadata import config as cfg
    from app.services.youtube_metadata.agent import run_metadata_agent

    async def _run():
        # WAJIB paling awal -- lihat docstring reset_redis_client() &
        # app/workers/youtube_discovery_worker.py (bug+fix yg SAMA,
        # ditemukan 2026-07-18).
        await reset_redis_client()

        if await cfg.is_running():
            logger.info("youtube_metadata_check: run sebelumnya masih berjalan, skip tick ini")
            return {"skipped": "already_running"}

        interval_minutes = await cfg.get_interval_minutes()
        last_run_iso = await cfg.get_last_run_at()
        now = datetime.now(timezone.utc)
        if last_run_iso:
            last_run = datetime.fromisoformat(last_run_iso)
            elapsed_minutes = (now - last_run).total_seconds() / 60
            if elapsed_minutes < interval_minutes:
                logger.info(
                    "youtube_metadata_check: belum waktunya (elapsed=%.1fmnt, interval=%dmnt), skip",
                    elapsed_minutes, interval_minutes,
                )
                return {"skipped": "not_due_yet", "elapsed_minutes": round(elapsed_minutes, 1), "interval_minutes": interval_minutes}

        acquired = await cfg.acquire_running_lock()
        if not acquired:
            logger.info("youtube_metadata_check: gagal ambil lock (race dgn proses lain), skip")
            return {"skipped": "lock_race"}

        try:
            async with AsyncSessionLocal() as db:
                result = await run_metadata_agent(db)
            await cfg.set_last_run_at(now.isoformat())
            return result
        finally:
            await cfg.release_running_lock()

    try:
        result = asyncio.run(_run())
        logger.info("youtube_metadata_check done: %s", result)
        return result
    except Exception as exc:
        logger.error("youtube_metadata_check error: %s", exc)
        raise self.retry(exc=exc)
