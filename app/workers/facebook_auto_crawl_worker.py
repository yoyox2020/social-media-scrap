"""Auto-crawl Facebook TIAP 1 JAM (2026-07-24, permintaan user "suruh
agent melakukan scraping tiap jam untuk update datanya, hal yg sama"
-- sama spt YouTube/TikTok) -- pola SAMA PERSIS, reuse logika bersama
di auto_crawl_common.py. Sumber topik SAMA (trend_recommendations, top
20 by score) -- topik yg sama dipakai lintas platform.

BELUM live-tested end-to-end (lihat app/agents/facebook/crawler_client.py
utk detail: semua token Apify pool exhausted saat pipeline ini
dibangun) -- run pertama jadwal ini kemungkinan besar 0 post tersimpan
sampai (a) kuota Apify tersedia lagi DAN (b) mapping field di
crawler_client.py diverifikasi/diperbaiki dari raw_data run pertama."""
from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import pipeline
from app.workers.auto_crawl_common import run_auto_crawl_for_platform
from app.workers.celery_app import celery_app


async def _facebook_pipeline_fn(db: AsyncSession, topic: str, triggered_by: str) -> dict:
    return await pipeline.run_facebook_pipeline(db, topic, triggered_by=triggered_by)


@celery_app.task(name="facebook.auto_crawl_top_topics")
def auto_crawl_facebook_task() -> dict:
    return asyncio.run(run_auto_crawl_for_platform("facebook", _facebook_pipeline_fn))
