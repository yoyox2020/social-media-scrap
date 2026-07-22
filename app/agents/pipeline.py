"""Orkestrator pipeline multi-agent YouTube (2026-07-22, permintaan
user, spec "SYSTEM ROLE Multi-Agent Orchestrator").

Alur: agent_topic -> agent_search -> agent_youtube (coordinator/parent)
-> agent_youtube01 (API) + agent_youtube02 (Crawler) paralel ->
agent-struktur-data (merge/normalize/score/AI/save DB).

MVP -- versi sederhana ("yang penting bisa jalan dulu"): SINKRON
(bukan Celery task terjadwal), dipicu manual lewat endpoint. Tidak ada
scheduling otomatis di versi ini."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import agent_search, agent_struktur_data, agent_topic
from app.agents.activity_log import get_run_log, log_activity
from app.agents.youtube import coordinator
from app.domain.scrape_runs.models import ScrapeRun


async def run_youtube_pipeline(db: AsyncSession, topic: str, max_results: int = 15) -> dict:
    run_id = uuid.uuid4()
    started_at = datetime.now(timezone.utc)

    scrape_run = ScrapeRun(
        keyword_text=topic, platform="youtube", api_source="youtube_data_api",
        status="running", triggered_by="manual_api", started_at=started_at,
    )
    db.add(scrape_run)
    await db.commit()

    await log_activity(db, run_id, "pipeline", "start", f"Pipeline YouTube dimulai utk topik '{topic}'")

    try:
        topic_result = await agent_topic.determine_topic(db, run_id, topic, platform="youtube")
        search_result = await agent_search.build_keywords(db, run_id, topic_result["topic"], platform="youtube")
        children_result = await coordinator.run_children(db, run_id, search_result["keywords"], max_results=max_results)
        struktur_result = await agent_struktur_data.process_and_save(
            db, run_id, topic_result["topic"],
            children_result["api_videos"], children_result["api_channels"], children_result["crawler_videos"],
        )

        scrape_run.status = "success"
        scrape_run.videos_fetched = struktur_result["total_video"]
        scrape_run.videos_new = struktur_result["saved_to_database"]
        scrape_run.videos_duplicate = struktur_result["duplicate_removed"]
        scrape_run.finished_at = datetime.now(timezone.utc)
        scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()
        await db.commit()

        await log_activity(db, run_id, "pipeline", "done", "Pipeline selesai sukses", details={"result": struktur_result})

        return {
            "status": "success",
            "run_id": str(run_id),
            "platform": "youtube",
            "topic": topic_result["topic"],
            "total_video": struktur_result["total_video"],
            "total_channel": struktur_result["total_channel"],
            "saved_to_database": struktur_result["saved_to_database"],
            "duplicate_removed": struktur_result["duplicate_removed"],
            "failed": struktur_result["failed"],
            "crawl_date": started_at.isoformat(),
        }
    except Exception as exc:
        scrape_run.status = "failed"
        scrape_run.error_message = str(exc)
        scrape_run.finished_at = datetime.now(timezone.utc)
        scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()
        await db.commit()
        await log_activity(db, run_id, "pipeline", "failed", f"Pipeline gagal: {exc}", level="error")

        return {
            "status": "failed",
            "run_id": str(run_id),
            "platform": "youtube",
            "topic": topic,
            "total_video": 0,
            "total_channel": 0,
            "saved_to_database": 0,
            "duplicate_removed": 0,
            "failed": 0,
            "error": str(exc),
            "crawl_date": started_at.isoformat(),
        }


async def get_pipeline_log(db: AsyncSession, run_id: uuid.UUID) -> list[dict]:
    return await get_run_log(db, run_id)
