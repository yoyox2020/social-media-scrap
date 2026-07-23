"""Logika BERSAMA auto-crawl per-jam lintas platform (2026-07-23,
permintaan user "topic dan search pastikan terjadwal otomatis" +
"sistemnya harus generik"). Awalnya cuma YouTube (youtube_auto_crawl_
worker.py), sekarang DIEKSTRAK supaya TikTok (dan platform berikutnya)
tinggal panggil helper ini dgn `pipeline_fn` masing2 -- BUKAN
copy-paste ulang logika ambil-topik/loop/logging.

SIKLUS 2 MODE per-platform (2026-07-23, permintaan user "scraping
berdasarkan topic dulu, kalau semua topic sudah discraping lanjut
pencarian global viral, habis itu balik lagi update topic, begitu
terus"), pakai tabel `trend_recommendation_platform_usage` yg SUDAH ADA
(dibangun 2026-07-21 utk Threads, sebelumnya tidak dipakai platform
lain):
1. MODE "topic": ambil topik yg BELUM PERNAH dicoba platform ini
   (anti-join ke platform_usage), proses, tandai used satu-satu.
2. Begitu SEMUA topik sudah pernah dicoba (anti-join kosong) -> MODE
   "global_viral": 1 putaran pakai keyword generik (bukan topik
   spesifik), LALU reset seluruh tanda 'used' platform ini -- jam
   berikutnya otomatis balik ke mode "topic" dari awal lagi (topik yg
   sama diproses ULANG, sekaligus me-refresh statistiknya -- bukan
   cuma discovery, jg semacam "update").

BATAS WAKTU PER-TOPIK (2026-07-23, ditemukan bug NYATA): 1 topik TikTok
("korupsi") pernah macet 2 JAM di tengah proses (proses worker mati
tanpa sempat update status) -- krn loop di sini SEKUENSIAL (nunggu 1
topik selesai baru lanjut topik berikutnya), topik yg macet itu
BERHASIL MENGUNCI SELURUH 19 topik lain di belakangnya, tidak ada satu
pun yg sempat dicoba. `asyncio.wait_for` dipasang supaya 1 topik yg
macet DIPOTONG paksa (dicatat gagal, TIDAK ditandai used spy dicoba
lagi nanti) dan loop lanjut ke topik berikutnya -- bukan berhenti
total."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.trend_recommendations.platform_usage_service import (
    get_unused_topics_for_platform,
    mark_topics_used,
    reset_platform_usage,
)
from app.shared.config import settings

logger = logging.getLogger(__name__)

TOP_N_TOPICS = 20
TRIGGERED_BY = "celery_beat"
GLOBAL_VIRAL_TOPICS = ["viral", "fyp", "trending"]
PER_TOPIC_TIMEOUT_SECONDS = 600.0  # 10 menit -- cukup longgar utk proses normal, tapi tetap membatasi

PipelineFn = Callable[[AsyncSession, str, str], Awaitable[dict]]


async def run_auto_crawl_for_platform(platform_label: str, pipeline_fn: PipelineFn) -> dict:
    """`pipeline_fn(db, topic, triggered_by) -> dict` -- signature SAMA
    utk semua platform (adaptasi parameter tambahan spt max_results
    dilakukan di closure pemanggil, lihat youtube_auto_crawl_worker.py/
    tiktok_auto_crawl_worker.py)."""
    engine = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    summary: dict = {"platform": platform_label, "mode": None, "topics_processed": [], "success": 0, "failed": 0}
    try:
        async with session_factory() as db:
            unused = await get_unused_topics_for_platform(db, platform_label, TOP_N_TOPICS)

        if unused:
            summary["mode"] = "topic"
            work_items: list[tuple] = list(unused)
        else:
            summary["mode"] = "global_viral"
            work_items = [(None, t) for t in GLOBAL_VIRAL_TOPICS]

        if not work_items:
            logger.warning("[auto_crawl_%s] trend_recommendations kosong, tidak ada topik sama sekali", platform_label)
            return summary

        for reco_id, topic in work_items:
            async with session_factory() as db:
                try:
                    result = await asyncio.wait_for(
                        pipeline_fn(db, topic, TRIGGERED_BY), timeout=PER_TOPIC_TIMEOUT_SECONDS,
                    )
                    summary["topics_processed"].append({
                        "topic": topic, "status": result["status"],
                        "saved_to_database": result.get("saved_to_database", 0),
                    })
                    if result["status"] == "success":
                        summary["success"] += 1
                        if summary["mode"] == "topic" and reco_id is not None:
                            await mark_topics_used(db, platform_label, [reco_id])
                    else:
                        summary["failed"] += 1
                except asyncio.TimeoutError:
                    logger.error(
                        "[auto_crawl_%s] topik '%s' DIPOTONG PAKSA -- macet >%ss, lanjut ke topik berikutnya "
                        "(TIDAK ditandai used, akan dicoba lagi jadwal berikutnya)",
                        platform_label, topic, PER_TOPIC_TIMEOUT_SECONDS,
                    )
                    summary["topics_processed"].append({"topic": topic, "status": "timeout"})
                    summary["failed"] += 1
                except Exception as exc:
                    logger.exception("[auto_crawl_%s] topik '%s' gagal: %s", platform_label, topic, exc)
                    summary["topics_processed"].append({"topic": topic, "status": "error", "error": str(exc)})
                    summary["failed"] += 1

        if summary["mode"] == "global_viral":
            async with session_factory() as db:
                reset_count = await reset_platform_usage(db, platform_label)
            logger.info(
                "[auto_crawl_%s] siklus topik selesai (semua sudah dicoba) -> 1 putaran global_viral selesai, "
                "reset %s tanda 'used' -- jam berikutnya mulai siklus topik lagi dari awal",
                platform_label, reset_count,
            )
    finally:
        await engine.dispose()

    logger.info("[auto_crawl_%s] mode=%s selesai: %s sukses, %s gagal dari %s topik",
                platform_label, summary["mode"], summary["success"], summary["failed"], len(summary["topics_processed"]))
    return summary
