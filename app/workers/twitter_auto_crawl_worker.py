"""Auto-crawl Twitter/X TIAP 1 JAM (2026-07-24) -- pola SAMA dgn
Facebook/Threads, reuse auto_crawl_common.py."""
from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import pipeline
from app.workers.auto_crawl_common import run_auto_crawl_for_platform
from app.workers.celery_app import celery_app


async def _twitter_pipeline_fn(db: AsyncSession, topic: str, triggered_by: str) -> dict:
    return await pipeline.run_twitter_pipeline(db, topic, triggered_by=triggered_by)


@celery_app.task(name="twitter.auto_crawl_top_topics")
def auto_crawl_twitter_task() -> dict:
    return asyncio.run(run_auto_crawl_for_platform("twitter", _twitter_pipeline_fn))
