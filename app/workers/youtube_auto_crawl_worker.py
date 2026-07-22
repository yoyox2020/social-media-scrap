"""Auto-crawl YouTube TIAP 1 JAM (2026-07-22, permintaan user) -- ambil
TOP 20 topik trending dari `trend_recommendations` (tabel SUDAH ADA,
dipakai bersama platform lain lewat score/recommendation_date), lalu
jalankan pipeline manual YANG SAMA PERSIS (app/agents/pipeline.py) satu
per satu utk tiap topik -- BUKAN jalur simpan-DB baru, agent-struktur-
data tetap wajib jalan & simpan spt trigger manual.

Dedup per topik (1 baris per topik, prioritas recommendation_date
terbaru lalu score tertinggi) -- `trend_recommendations` boleh punya
baris yg sama utk topik yg sama di tanggal berbeda (unique constraint-
nya (topic, recommendation_date), BUKAN topic saja).

TIDAK mengubah `trend_recommendations.status` sama sekali -- kolom itu
milik alur pending/used punya platform lain (Threads dkk, lihat
platform_usage_models.py), auto-crawl YouTube ini HANYA BACA supaya
tidak mengganggu proses platform lain yg baca status yg sama.

Dijalankan SATU PER SATU (bukan asyncio.gather semua topik sekaligus)
krn semua topik berebut KUOTA & KEY YouTube API yg SAMA (baru 1 key
asli di seluruh sistem per 2026-07-22) -- paralel besar-besaran cuma
mempercepat kuota habis, tidak menambah kapasitas nyata.

PERINGATAN KUOTA (dicatat, BUKAN diam-diam disembunyikan): 1 key
YouTube Data API asli, kuota default 10.000 unit/hari, search.list
=100 unit/panggilan -> ~100 pencarian/hari kapasitas riil. 20 topik/jam
x 24 jam = 480 pencarian/hari dibutuhkan -- kuota AKAN habis lebih
cepat dari 24 jam (kira-kira jam ke-5), run sesudahnya gagal dgn error
tercatat di scrape_runs.error_message (lihat pipeline.run_youtube_pipeline),
bukan hilang tanpa jejak. Solusi jangka panjang: isi key asli ke slot
agent_youtube03/04/05 yg sudah siap (lihat coordinator._discover_candidates)."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents import pipeline
from app.domain.trend_recommendations.models import TrendRecommendation
from app.shared.config import settings
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

TOP_N_TOPICS = 20
TRIGGERED_BY = "celery_beat"


async def _get_top_topics(db: AsyncSession, limit: int = TOP_N_TOPICS) -> list[str]:
    """Ambil kandidat lebih banyak dari `limit` dulu (supaya dedup per
    topik tidak kekurangan), urut recommendation_date DESC lalu score
    DESC -- baris PERTAMA per topik (setelah dedup) otomatis yg
    paling relevan (terbaru & tertinggi score)."""
    result = await db.execute(
        select(TrendRecommendation.topic, TrendRecommendation.score, TrendRecommendation.recommendation_date)
        .order_by(TrendRecommendation.recommendation_date.desc(), TrendRecommendation.score.desc())
        .limit(limit * 5)
    )
    seen: set[str] = set()
    topics: list[str] = []
    for topic, _score, _date in result.all():
        if topic in seen:
            continue
        seen.add(topic)
        topics.append(topic)
        if len(topics) >= limit:
            break
    return topics


async def _run_auto_crawl() -> dict:
    engine = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    summary: dict = {"topics_processed": [], "success": 0, "failed": 0}
    try:
        async with session_factory() as db:
            topics = await _get_top_topics(db)

        if not topics:
            logger.warning("[auto_crawl_youtube] trend_recommendations kosong, tidak ada topik utk di-crawl")
            return summary

        for topic in topics:
            async with session_factory() as db:
                try:
                    result = await pipeline.run_youtube_pipeline(
                        db, topic, max_results=15, triggered_by=TRIGGERED_BY,
                    )
                    summary["topics_processed"].append({
                        "topic": topic, "status": result["status"],
                        "saved_to_database": result.get("saved_to_database", 0),
                    })
                    if result["status"] == "success":
                        summary["success"] += 1
                    else:
                        summary["failed"] += 1
                except Exception as exc:
                    logger.exception("[auto_crawl_youtube] topik '%s' gagal: %s", topic, exc)
                    summary["topics_processed"].append({"topic": topic, "status": "error", "error": str(exc)})
                    summary["failed"] += 1
    finally:
        await engine.dispose()

    logger.info("[auto_crawl_youtube] selesai: %s sukses, %s gagal dari %s topik",
                summary["success"], summary["failed"], len(summary["topics_processed"]))
    return summary


@celery_app.task(name="youtube.auto_crawl_top_topics")
def auto_crawl_youtube_task() -> dict:
    return asyncio.run(_run_auto_crawl())
