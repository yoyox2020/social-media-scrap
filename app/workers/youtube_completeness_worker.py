"""Task terjadwal utk audit kelengkapan + backfill SEMUA post YouTube
(2026-07-24, permintaan user "buat script terpisah... biarkan agent yang
mengeceknya" -- jadi bukan cuma script manual, dijadwalkan Celery Beat
spy jalan sendiri). Lihat app/agents/youtube/completeness_audit.py utk
logika + alasan kenapa ini TERPISAH dari youtube_refresh_worker.py yg
sudah ada (itu batch kecil/jam, ini full-pass semua post).

Jadwal HARIAN (bukan tiap jam spt refresh.py) -- 1x full pass ~383 unit
kuota API (murah), tapi kalau dijadwalkan tiap jam bakal numpuk bareng
auto-crawl discovery (search.list 100 unit/panggilan) + refresh hourly,
berisiko re-create masalah "kuota YouTube habis" yg pernah kejadian
(lihat [[project_youtube_quota_incident_2026_07]]). Harian dianggap
cukup krn kelengkapan yg dibenerkan run pertama TIDAK bakal rusak lagi
sendirian -- cuma post BARU (dari discovery) yg nambah "belum lengkap",
dan itu kepilih prioritas #1 di run harian berikutnya."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.youtube.completeness_audit import audit_and_backfill_all_youtube_posts
from app.shared.config import settings
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Full pass ribuan post -- jaring pengaman wall-clock (lihat pola yg sama
# di tiktok_reply_enrichment_worker.py) spy kalau macet, TIDAK menyandera
# slot worker selamanya.
WALL_CLOCK_TIMEOUT_SECONDS = 3300.0


async def _run_audit() -> dict:
    engine = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as db:
            result = await asyncio.wait_for(
                audit_and_backfill_all_youtube_posts(db), timeout=WALL_CLOCK_TIMEOUT_SECONDS,
            )
        logger.info("[youtube_completeness_audit] %s", result)
        return result
    finally:
        await engine.dispose()


@celery_app.task(name="youtube.audit_completeness")
def audit_youtube_completeness_task() -> dict:
    return asyncio.run(_run_audit())
