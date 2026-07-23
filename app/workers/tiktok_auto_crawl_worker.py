"""Auto-crawl TikTok TIAP 1 JAM (2026-07-23, permintaan user "topic dan
search pastikan terjadwal otomatis") -- pola SAMA PERSIS dgn YouTube
(app/workers/youtube_auto_crawl_worker.py), reuse logika bersama di
auto_crawl_common.py. Sumber topik SAMA (trend_recommendations, top 20
by score) -- topik yg sama dipakai lintas platform, bukan per-platform
terpisah.

Beda dari YouTube: TIDAK ada peringatan kuota API resmi (TikTok gak
py "API resmi"), tapi TETAP terbatas oleh saldo Apify/EnsembleData yg
terdaftar -- token yg habis otomatis dilewati (lihat {{ROTATE:Apify}}
di app/services/agent_curl_targets/service.py), BUKAN bikin run gagal
total selama masih ada 1 token available di pool."""
from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import pipeline
from app.workers.auto_crawl_common import run_auto_crawl_for_platform
from app.workers.celery_app import celery_app


async def _tiktok_pipeline_fn(db: AsyncSession, topic: str, triggered_by: str) -> dict:
    return await pipeline.run_tiktok_pipeline(db, topic, triggered_by=triggered_by)


@celery_app.task(name="tiktok.auto_crawl_top_topics")
def auto_crawl_tiktok_task() -> dict:
    return asyncio.run(run_auto_crawl_for_platform("tiktok", _tiktok_pipeline_fn))
