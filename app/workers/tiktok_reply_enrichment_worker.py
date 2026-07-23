"""Ambil balasan komentar utk post TikTok, batch besar ("unlimited",
2026-07-24). Jadwal terpisah dari discovery. Lihat
app/agents/tiktok/reply_enrichment.py.

BATAS WAKTU KESELURUHAN (2026-07-24, ditemukan bug NYATA): sebelumnya
task ini bisa macet BERJAM-JAM (3 instance nyangkut 2-5 jam, memakai
semua slot worker shg task lain tidak bisa jalan) -- akar masalahnya
BUG TERPISAH (metadata_ gagal tersimpan, lihat reply_enrichment.py)
bikin post yg SAMA diproses ULANG tanpa henti krn tidak pernah
ketandai selesai. Bug itu SUDAH diperbaiki, tapi `asyncio.wait_for` di
sini tetap dipasang sbg jaring pengaman kedua -- kalau apapun bikin
task ini lambat lagi, dipotong paksa sebelum jadwal jam berikutnya
tiba (bukan menumpuk task baru di atas yg lama)."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.tiktok.reply_enrichment import enrich_viral_posts_with_replies
from app.shared.config import settings
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

WALL_CLOCK_TIMEOUT_SECONDS = 3300.0  # 55 menit -- sisa 5 menit buffer sblm jadwal jam berikutnya


async def _run() -> dict:
    engine = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as db:
            try:
                result = await asyncio.wait_for(
                    enrich_viral_posts_with_replies(db), timeout=WALL_CLOCK_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "[tiktok_reply_enrichment] DIPOTONG PAKSA -- belum selesai dlm %ss. "
                    "Post yg SUDAH diproses tetap tersimpan (commit per-post), sisanya dicoba lagi jadwal berikutnya.",
                    WALL_CLOCK_TIMEOUT_SECONDS,
                )
                return {"processed": None, "note": "timeout -- lihat log"}
        logger.info("[tiktok_reply_enrichment] %s", result)
        return result
    finally:
        await engine.dispose()


@celery_app.task(name="tiktok.enrich_viral_replies")
def enrich_viral_replies_task() -> dict:
    return asyncio.run(_run())
