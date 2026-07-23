"""Auto-crawl YouTube TIAP 1 JAM (2026-07-22) -- logika bersama (ambil
topik/loop/logging) sekarang di app/workers/auto_crawl_common.py
(diekstrak 2026-07-23 supaya TikTok dkk tinggal reuse, lihat docstring
di sana). File ini tinggal adaptasi signature run_youtube_pipeline
(py max_results, beda dari platform lain) + registrasi task Celery.

PERINGATAN KUOTA (masih berlaku): 1 key YouTube Data API asli, kuota
default 10.000 unit/hari, search.list=100 unit/panggilan -> ~100
pencarian/hari kapasitas riil. 20 topik/jam x 24 jam = 480 pencarian/
hari dibutuhkan -- kuota AKAN habis lebih cepat dari 24 jam, run
sesudahnya gagal dgn error tercatat di scrape_runs.error_message,
bukan hilang tanpa jejak."""
from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import pipeline
from app.workers.auto_crawl_common import run_auto_crawl_for_platform
from app.workers.celery_app import celery_app


async def _youtube_pipeline_fn(db: AsyncSession, topic: str, triggered_by: str) -> dict:
    return await pipeline.run_youtube_pipeline(db, topic, max_results=15, triggered_by=triggered_by)


@celery_app.task(name="youtube.auto_crawl_top_topics")
def auto_crawl_youtube_task() -> dict:
    return asyncio.run(run_auto_crawl_for_platform("youtube", _youtube_pipeline_fn))
