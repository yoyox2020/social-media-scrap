"""Auto-crawl Instagram TIAP 1 JAM (2026-07-24) -- pola SAMA dgn
Facebook/TikTok/YouTube, reuse auto_crawl_common.py. Beda: topik yg
TIDAK py akun Instagram terdaftar di trend_recommendations.related_accounts
tetap dicoba (fallback topik sbg username, lihat crawler_client.py),
jadi TIDAK semua run menghasilkan post -- ini WAJAR (bukan bug), sama
spt keterbatasan hashtag TikTok utk keyword berspasi."""
from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import pipeline
from app.workers.auto_crawl_common import run_auto_crawl_for_platform
from app.workers.celery_app import celery_app


async def _instagram_pipeline_fn(db: AsyncSession, topic: str, triggered_by: str) -> dict:
    return await pipeline.run_instagram_pipeline(db, topic, triggered_by=triggered_by)


@celery_app.task(name="instagram.auto_crawl_top_topics")
def auto_crawl_instagram_task() -> dict:
    return asyncio.run(run_auto_crawl_for_platform("instagram", _instagram_pipeline_fn))
