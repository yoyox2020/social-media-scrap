"""
Celery task Sentiment Agent -- Beat jalan TIAP 15 MENIT (granularitas
terkecil dari pilihan interval 15/30/60/240 menit), tapi task ini cuma
benar2 eksekusi kalau sudah waktunya sesuai interval_minutes di Redis --
pola SAMA dgn app/workers/youtube_metadata_worker.py (dynamic schedule via
fixed-interval check).
"""
import asyncio
import logging
from datetime import datetime, timezone

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="workers.sentiment_agent.check",
    bind=True,
    max_retries=1,
    default_retry_delay=300,
)
def sentiment_agent_check_task(self):
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.infrastructure.redis.connection import reset_redis_client
    from app.services.sentiment_agent import config as cfg
    from app.services.sentiment_agent.agent import run_sentiment_agent

    async def _run():
        # WAJIB paling awal -- lihat docstring reset_redis_client() &
        # app/workers/youtube_discovery_worker.py (bug "Event loop is
        # closed" ditemukan+fix 2026-07-18, pola sama diterapkan di sini
        # sejak awal drpd nunggu kena di produksi lagi).
        await reset_redis_client()

        if await cfg.is_running():
            logger.info("sentiment_agent_check: run sebelumnya masih berjalan, skip tick ini")
            return {"skipped": "already_running"}

        interval_minutes = await cfg.get_interval_minutes()
        last_run_iso = await cfg.get_last_run_at()
        now = datetime.now(timezone.utc)
        if last_run_iso:
            last_run = datetime.fromisoformat(last_run_iso)
            elapsed_minutes = (now - last_run).total_seconds() / 60
            if elapsed_minutes < interval_minutes:
                logger.info(
                    "sentiment_agent_check: belum waktunya (elapsed=%.1fmnt, interval=%dmnt), skip",
                    elapsed_minutes, interval_minutes,
                )
                return {"skipped": "not_due_yet", "elapsed_minutes": round(elapsed_minutes, 1), "interval_minutes": interval_minutes}

        acquired = await cfg.acquire_running_lock()
        if not acquired:
            logger.info("sentiment_agent_check: gagal ambil lock (race dgn proses lain), skip")
            return {"skipped": "lock_race"}

        try:
            async with AsyncSessionLocal() as db:
                result = await run_sentiment_agent(db)
            await cfg.set_last_run_at(now.isoformat())
            return result
        finally:
            await cfg.release_running_lock()

    try:
        result = asyncio.run(_run())
        logger.info("sentiment_agent_check done: %s", result)
        return result
    except Exception as exc:
        logger.error("sentiment_agent_check error: %s", exc)
        raise self.retry(exc=exc)
