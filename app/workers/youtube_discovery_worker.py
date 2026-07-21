"""
Celery task YouTube Discovery Agent -- Beat jalan TIAP JAM
(workers.youtube_discovery.hourly_check, granularitas terkecil dari pilihan
scheduler 1/4/8/24 jam), tapi task ini cuma benar2 EKSEKUSI pencarian kalau
sudah waktunya sesuai interval yg diatur user di Redis (lihat
app/services/youtube_discovery/config.py) -- pola "dynamic schedule via
fixed-interval check", BUKAN reconfigure Celery Beat schedule langsung
(itu butuh restart utk kepakai, bertentangan dgn requirement "real-time
dari dashboard").
"""
import asyncio
import logging
from datetime import datetime, timezone

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="workers.youtube_discovery.hourly_check",
    bind=True,
    max_retries=1,
    default_retry_delay=300,
)
def youtube_discovery_hourly_check_task(self):
    """
    Cek tiap jam: sudah waktunya run berikutnya blm (berdasar
    interval_hours + last_run_at di Redis)? Kalau ya, jalankan
    run_discovery_agent() SATU KALI (dilindungi lock Redis, cegah
    tumpang tindih kalau run sebelumnya masih jalan lewat 1 jam).
    """
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.infrastructure.redis.connection import reset_redis_client
    from app.services.youtube_discovery import config as cfg
    from app.services.youtube_discovery.agent import run_discovery_agent

    async def _run():
        # WAJIB paling awal -- lihat docstring reset_redis_client(). Tanpa
        # ini, task KEDUA dst yg diproses worker process yg SAMA bisa dapat
        # client Redis basi (terikat event loop task sebelumnya yg sudah
        # tertutup) -> RuntimeError: Event loop is closed (ditemukan
        # 2026-07-18 lewat log produksi, direproduksi+diverifikasi fix-nya
        # di tests/integration/test_redis_event_loop_bug_manual.py).
        await reset_redis_client()

        if await cfg.is_running():
            logger.info("youtube_discovery_hourly_check: run sebelumnya masih berjalan, skip tick ini")
            return {"skipped": "already_running"}

        interval_hours = await cfg.get_interval_hours()
        last_run_iso = await cfg.get_last_run_at()
        now = datetime.now(timezone.utc)
        if last_run_iso:
            last_run = datetime.fromisoformat(last_run_iso)
            elapsed_hours = (now - last_run).total_seconds() / 3600
            if elapsed_hours < interval_hours:
                logger.info(
                    "youtube_discovery_hourly_check: belum waktunya (elapsed=%.1fj, interval=%dj), skip",
                    elapsed_hours, interval_hours,
                )
                return {"skipped": "not_due_yet", "elapsed_hours": round(elapsed_hours, 1), "interval_hours": interval_hours}

        acquired = await cfg.acquire_running_lock()
        if not acquired:
            logger.info("youtube_discovery_hourly_check: gagal ambil lock (race dgn proses lain), skip")
            return {"skipped": "lock_race"}

        try:
            async with AsyncSessionLocal() as db:
                result = await run_discovery_agent(db)
            await cfg.set_last_run_at(now.isoformat())
            return result
        finally:
            await cfg.release_running_lock()

    try:
        result = asyncio.run(_run())
        logger.info("youtube_discovery_hourly_check done: %s", result)
        return result
    except Exception as exc:
        logger.error("youtube_discovery_hourly_check error: %s", exc)
        raise self.retry(exc=exc)


@celery_app.task(
    name="workers.youtube_discovery.agent2_hourly_check",
    bind=True,
    max_retries=1,
    default_retry_delay=300,
)
def youtube_discovery_agent2_hourly_check_task(self):
    """Agent 2 -- DISKOVERI TERPISAH dari Agent 1 di atas (bukan fallback),
    bawa YouTube Data API key + OpenRouter key/model SENDIRI, jadwal SENDIRI
    (default tiap 1 jam), HANYA mode topic-guided. Pola pengecekan SAMA
    persis dgn task Agent 1 (dynamic interval via Redis), TAPI lock/config/
    riwayat run SEMUA terpisah (agent2_config.py, agent_label='agent2' di
    youtube_discovery_runs) -- permintaan user 2026-07-18."""
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.infrastructure.redis.connection import reset_redis_client
    from app.services.youtube_discovery import agent2_config as cfg2
    from app.services.youtube_discovery.agent import run_discovery_agent_2

    async def _run():
        await reset_redis_client()

        if not await cfg2.get_enabled():
            logger.info("youtube_discovery_agent2_hourly_check: dimatikan (tombol OFF), skip")
            return {"skipped": "disabled"}

        if await cfg2.is_running():
            logger.info("youtube_discovery_agent2_hourly_check: run sebelumnya masih berjalan, skip tick ini")
            return {"skipped": "already_running"}

        interval_hours = await cfg2.get_interval_hours()
        last_run_iso = await cfg2.get_last_run_at()
        now = datetime.now(timezone.utc)
        if last_run_iso:
            last_run = datetime.fromisoformat(last_run_iso)
            elapsed_hours = (now - last_run).total_seconds() / 3600
            if elapsed_hours < interval_hours:
                logger.info(
                    "youtube_discovery_agent2_hourly_check: belum waktunya (elapsed=%.1fj, interval=%dj), skip",
                    elapsed_hours, interval_hours,
                )
                return {"skipped": "not_due_yet", "elapsed_hours": round(elapsed_hours, 1), "interval_hours": interval_hours}

        acquired = await cfg2.acquire_running_lock()
        if not acquired:
            logger.info("youtube_discovery_agent2_hourly_check: gagal ambil lock (race dgn proses lain), skip")
            return {"skipped": "lock_race"}

        try:
            async with AsyncSessionLocal() as db:
                result = await run_discovery_agent_2(db)
            await cfg2.set_last_run_at(now.isoformat())
            return result
        finally:
            await cfg2.release_running_lock()

    try:
        result = asyncio.run(_run())
        logger.info("youtube_discovery_agent2_hourly_check done: %s", result)
        return result
    except Exception as exc:
        logger.error("youtube_discovery_agent2_hourly_check error: %s", exc)
        raise self.retry(exc=exc)
