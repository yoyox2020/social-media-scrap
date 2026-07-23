"""Logika BERSAMA auto-crawl per-jam lintas platform (2026-07-23,
permintaan user "topic dan search pastikan terjadwal otomatis" +
"sistemnya harus generik"). Awalnya cuma YouTube (youtube_auto_crawl_
worker.py), sekarang DIEKSTRAK supaya TikTok (dan platform berikutnya)
tinggal panggil helper ini dgn `pipeline_fn` masing2 -- BUKAN
copy-paste ulang logika ambil-topik/loop/logging.

Sumber topik SAMA utk semua platform: `trend_recommendations` (lintas
platform by desain -- lihat docstring modelnya), top 20 by score,
dedup per topik. TIDAK mengubah `status` (dipakai bareng platform lain
spt Threads)."""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.trend_recommendations.models import TrendRecommendation
from app.shared.config import settings

logger = logging.getLogger(__name__)

TOP_N_TOPICS = 20
TRIGGERED_BY = "celery_beat"

PipelineFn = Callable[[AsyncSession, str, str], Awaitable[dict]]


async def get_top_topics(db: AsyncSession, limit: int = TOP_N_TOPICS) -> list[str]:
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


async def run_auto_crawl_for_platform(platform_label: str, pipeline_fn: PipelineFn) -> dict:
    """`pipeline_fn(db, topic, triggered_by) -> dict` -- signature SAMA
    utk semua platform (adaptasi parameter tambahan spt max_results
    dilakukan di closure pemanggil, lihat youtube_auto_crawl_worker.py/
    tiktok_auto_crawl_worker.py)."""
    engine = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    summary: dict = {"platform": platform_label, "topics_processed": [], "success": 0, "failed": 0}
    try:
        async with session_factory() as db:
            topics = await get_top_topics(db)

        if not topics:
            logger.warning("[auto_crawl_%s] trend_recommendations kosong, tidak ada topik utk di-crawl", platform_label)
            return summary

        for topic in topics:
            async with session_factory() as db:
                try:
                    result = await pipeline_fn(db, topic, TRIGGERED_BY)
                    summary["topics_processed"].append({
                        "topic": topic, "status": result["status"],
                        "saved_to_database": result.get("saved_to_database", 0),
                    })
                    if result["status"] == "success":
                        summary["success"] += 1
                    else:
                        summary["failed"] += 1
                except Exception as exc:
                    logger.exception("[auto_crawl_%s] topik '%s' gagal: %s", platform_label, topic, exc)
                    summary["topics_processed"].append({"topic": topic, "status": "error", "error": str(exc)})
                    summary["failed"] += 1
    finally:
        await engine.dispose()

    logger.info("[auto_crawl_%s] selesai: %s sukses, %s gagal dari %s topik",
                platform_label, summary["success"], summary["failed"], len(summary["topics_processed"]))
    return summary
