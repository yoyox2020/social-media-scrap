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
from app.agents.facebook import coordinator as facebook_coordinator
from app.agents.facebook import struktur_data as facebook_struktur_data
from app.agents.instagram import coordinator as instagram_coordinator
from app.agents.instagram import struktur_data as instagram_struktur_data
from app.agents.news import coordinator as news_coordinator
from app.agents.news import struktur_data as news_struktur_data
from app.agents.threads import coordinator as threads_coordinator
from app.agents.threads import struktur_data as threads_struktur_data
from app.agents.tiktok import coordinator as tiktok_coordinator
from app.agents.tiktok import struktur_data as tiktok_struktur_data
from app.agents.twitter import coordinator as twitter_coordinator
from app.agents.twitter import struktur_data as twitter_struktur_data
from app.agents.youtube import coordinator
from app.domain.scrape_runs.models import ScrapeRun


async def run_youtube_pipeline(
    db: AsyncSession, topic: str, max_results: int = 15, triggered_by: str = "manual_api",
) -> dict:
    run_id = uuid.uuid4()
    started_at = datetime.now(timezone.utc)

    scrape_run = ScrapeRun(
        keyword_text=topic, platform="youtube", api_source="youtube_data_api",
        status="running", triggered_by=triggered_by, started_at=started_at,
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


async def run_tiktok_pipeline(
    db: AsyncSession, topic: str, triggered_by: str = "manual_api",
) -> dict:
    """Alur: agent_topic -> agent_search -> agent_tiktok (coordinator)
    -> N child aktif (curl/Apify) paralel -> agent-struktur-data TikTok
    (merge/normalize/score/AI/save DB). Pola SAMA PERSIS dgn
    run_youtube_pipeline, TIDAK ada max_results krn Apify resultsPerPage
    sudah ditentukan di curl target itu sendiri (bukan per-request)."""
    run_id = uuid.uuid4()
    started_at = datetime.now(timezone.utc)

    scrape_run = ScrapeRun(
        keyword_text=topic, platform="tiktok", api_source="apify",
        status="running", triggered_by=triggered_by, started_at=started_at,
    )
    db.add(scrape_run)
    await db.commit()

    await log_activity(db, run_id, "pipeline", "start", f"Pipeline TikTok dimulai utk topik '{topic}'")

    try:
        topic_result = await agent_topic.determine_topic(db, run_id, topic, platform="tiktok")
        search_result = await agent_search.build_keywords(db, run_id, topic_result["topic"], platform="tiktok")
        children_result = await tiktok_coordinator.run_children(db, run_id, search_result["keywords"])
        struktur_result = await tiktok_struktur_data.process_and_save(
            db, run_id, topic_result["topic"], children_result["videos"],
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
            "platform": "tiktok",
            "topic": topic_result["topic"],
            "total_video": struktur_result["total_video"],
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
            "platform": "tiktok",
            "topic": topic,
            "total_video": 0,
            "saved_to_database": 0,
            "duplicate_removed": 0,
            "failed": 0,
            "error": str(exc),
            "crawl_date": started_at.isoformat(),
        }


async def run_facebook_pipeline(
    db: AsyncSession, topic: str, triggered_by: str = "manual_api",
) -> dict:
    """Alur: agent_topic -> agent_search -> agent_facebook (coordinator)
    -> N child aktif (curl/Apify) paralel -> agent-struktur-data
    Facebook (merge/normalize/score/AI/save DB). Pola SAMA PERSIS dgn
    run_tiktok_pipeline. BELUM live-tested end-to-end (lihat docstring
    app/agents/facebook/crawler_client.py -- semua token Apify exhausted
    saat dibangun)."""
    run_id = uuid.uuid4()
    started_at = datetime.now(timezone.utc)

    scrape_run = ScrapeRun(
        keyword_text=topic, platform="facebook", api_source="apify",
        status="running", triggered_by=triggered_by, started_at=started_at,
    )
    db.add(scrape_run)
    await db.commit()

    await log_activity(db, run_id, "pipeline", "start", f"Pipeline Facebook dimulai utk topik '{topic}'")

    try:
        topic_result = await agent_topic.determine_topic(db, run_id, topic, platform="facebook")
        search_result = await agent_search.build_keywords(db, run_id, topic_result["topic"], platform="facebook")
        children_result = await facebook_coordinator.run_children(db, run_id, search_result["keywords"])
        struktur_result = await facebook_struktur_data.process_and_save(
            db, run_id, topic_result["topic"], children_result["posts"],
        )

        scrape_run.status = "success"
        scrape_run.videos_fetched = struktur_result["total_post"]
        scrape_run.videos_new = struktur_result["saved_to_database"]
        scrape_run.videos_duplicate = struktur_result["duplicate_removed"]
        scrape_run.finished_at = datetime.now(timezone.utc)
        scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()
        await db.commit()

        await log_activity(db, run_id, "pipeline", "done", "Pipeline selesai sukses", details={"result": struktur_result})

        return {
            "status": "success",
            "run_id": str(run_id),
            "platform": "facebook",
            "topic": topic_result["topic"],
            "total_post": struktur_result["total_post"],
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
            "platform": "facebook",
            "topic": topic,
            "total_post": 0,
            "saved_to_database": 0,
            "duplicate_removed": 0,
            "failed": 0,
            "error": str(exc),
            "crawl_date": started_at.isoformat(),
        }


async def run_instagram_pipeline(
    db: AsyncSession, topic: str, triggered_by: str = "manual_api",
) -> dict:
    """Alur: agent_topic -> agent_search -> agent_instagram (coordinator)
    -> agent-struktur-data Instagram -> simpan DB. agent_search TETAP
    dipanggil (konsisten dgn arsitektur semua platform, "agent topik dan
    agent search harus membaca dari tabel topik") TAPI hasil keyword
    variannya ("topik terbaru"/"topik trending") TIDAK dipakai --
    Instagram scrape PER-USERNAME (lihat crawler_client.py), jadi yg
    dipakai cuma `topic_result["topic"]` asli utk resolve related_accounts."""
    run_id = uuid.uuid4()
    started_at = datetime.now(timezone.utc)

    scrape_run = ScrapeRun(
        keyword_text=topic, platform="instagram", api_source="apify",
        status="running", triggered_by=triggered_by, started_at=started_at,
    )
    db.add(scrape_run)
    await db.commit()

    await log_activity(db, run_id, "pipeline", "start", f"Pipeline Instagram dimulai utk topik '{topic}'")

    try:
        topic_result = await agent_topic.determine_topic(db, run_id, topic, platform="instagram")
        await agent_search.build_keywords(db, run_id, topic_result["topic"], platform="instagram")
        children_result = await instagram_coordinator.run_children(db, run_id, topic_result["topic"])
        struktur_result = await instagram_struktur_data.process_and_save(
            db, run_id, topic_result["topic"], children_result["posts"],
        )

        scrape_run.status = "success"
        scrape_run.videos_fetched = struktur_result["total_post"]
        scrape_run.videos_new = struktur_result["saved_to_database"]
        scrape_run.videos_duplicate = struktur_result["duplicate_removed"]
        scrape_run.finished_at = datetime.now(timezone.utc)
        scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()
        await db.commit()

        await log_activity(db, run_id, "pipeline", "done", "Pipeline selesai sukses", details={"result": struktur_result})

        return {
            "status": "success",
            "run_id": str(run_id),
            "platform": "instagram",
            "topic": topic_result["topic"],
            "total_post": struktur_result["total_post"],
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
            "platform": "instagram",
            "topic": topic,
            "total_post": 0,
            "saved_to_database": 0,
            "duplicate_removed": 0,
            "failed": 0,
            "error": str(exc),
            "crawl_date": started_at.isoformat(),
        }


async def run_threads_pipeline(
    db: AsyncSession, topic: str, triggered_by: str = "manual_api",
) -> dict:
    """Alur: agent_topic -> agent_search -> agent_threads (coordinator,
    distribusi keyword spt TikTok/Facebook) -> agent-struktur-data
    Threads -> simpan DB."""
    run_id = uuid.uuid4()
    started_at = datetime.now(timezone.utc)

    scrape_run = ScrapeRun(
        keyword_text=topic, platform="threads", api_source="ensembledata",
        status="running", triggered_by=triggered_by, started_at=started_at,
    )
    db.add(scrape_run)
    await db.commit()

    await log_activity(db, run_id, "pipeline", "start", f"Pipeline Threads dimulai utk topik '{topic}'")

    try:
        topic_result = await agent_topic.determine_topic(db, run_id, topic, platform="threads")
        search_result = await agent_search.build_keywords(db, run_id, topic_result["topic"], platform="threads")
        children_result = await threads_coordinator.run_children(db, run_id, search_result["keywords"])
        struktur_result = await threads_struktur_data.process_and_save(
            db, run_id, topic_result["topic"], children_result["posts"],
        )

        scrape_run.status = "success"
        scrape_run.videos_fetched = struktur_result["total_post"]
        scrape_run.videos_new = struktur_result["saved_to_database"]
        scrape_run.videos_duplicate = struktur_result["duplicate_removed"]
        scrape_run.finished_at = datetime.now(timezone.utc)
        scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()
        await db.commit()

        await log_activity(db, run_id, "pipeline", "done", "Pipeline selesai sukses", details={"result": struktur_result})

        return {
            "status": "success",
            "run_id": str(run_id),
            "platform": "threads",
            "topic": topic_result["topic"],
            "total_post": struktur_result["total_post"],
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
            "platform": "threads",
            "topic": topic,
            "total_post": 0,
            "saved_to_database": 0,
            "duplicate_removed": 0,
            "failed": 0,
            "error": str(exc),
            "crawl_date": started_at.isoformat(),
        }


async def run_twitter_pipeline(
    db: AsyncSession, topic: str, triggered_by: str = "manual_api",
) -> dict:
    """Alur: agent_topic -> agent_search -> agent_twitter (coordinator,
    distribusi keyword spt TikTok/Facebook/Threads) -> agent-struktur-data
    Twitter -> simpan DB."""
    run_id = uuid.uuid4()
    started_at = datetime.now(timezone.utc)

    scrape_run = ScrapeRun(
        keyword_text=topic, platform="twitter", api_source="apify",
        status="running", triggered_by=triggered_by, started_at=started_at,
    )
    db.add(scrape_run)
    await db.commit()

    await log_activity(db, run_id, "pipeline", "start", f"Pipeline Twitter dimulai utk topik '{topic}'")

    try:
        topic_result = await agent_topic.determine_topic(db, run_id, topic, platform="twitter")
        search_result = await agent_search.build_keywords(db, run_id, topic_result["topic"], platform="twitter")
        children_result = await twitter_coordinator.run_children(db, run_id, search_result["keywords"])
        struktur_result = await twitter_struktur_data.process_and_save(
            db, run_id, topic_result["topic"], children_result["posts"],
        )

        scrape_run.status = "success"
        scrape_run.videos_fetched = struktur_result["total_post"]
        scrape_run.videos_new = struktur_result["saved_to_database"]
        scrape_run.videos_duplicate = struktur_result["duplicate_removed"]
        scrape_run.finished_at = datetime.now(timezone.utc)
        scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()
        await db.commit()

        await log_activity(db, run_id, "pipeline", "done", "Pipeline selesai sukses", details={"result": struktur_result})

        return {
            "status": "success",
            "run_id": str(run_id),
            "platform": "twitter",
            "topic": topic_result["topic"],
            "total_post": struktur_result["total_post"],
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
            "platform": "twitter",
            "topic": topic,
            "total_post": 0,
            "saved_to_database": 0,
            "duplicate_removed": 0,
            "failed": 0,
            "error": str(exc),
            "crawl_date": started_at.isoformat(),
        }


async def run_news_pipeline(
    db: AsyncSession, topic: str, triggered_by: str = "manual_api",
) -> dict:
    """Alur: agent_topic -> agent_search -> agent_news (coordinator,
    distribusi keyword ke child, cari+scrape artikel via Firecrawl) ->
    agent-struktur-data News -> simpan DB."""
    run_id = uuid.uuid4()
    started_at = datetime.now(timezone.utc)

    scrape_run = ScrapeRun(
        keyword_text=topic, platform="news", api_source="firecrawl",
        status="running", triggered_by=triggered_by, started_at=started_at,
    )
    db.add(scrape_run)
    await db.commit()

    await log_activity(db, run_id, "pipeline", "start", f"Pipeline News dimulai utk topik '{topic}'")

    try:
        topic_result = await agent_topic.determine_topic(db, run_id, topic, platform="news")
        search_result = await agent_search.build_keywords(db, run_id, topic_result["topic"], platform="news")
        children_result = await news_coordinator.run_children(db, run_id, search_result["keywords"])
        struktur_result = await news_struktur_data.process_and_save(
            db, run_id, topic_result["topic"], children_result["posts"],
        )

        scrape_run.status = "success"
        scrape_run.videos_fetched = struktur_result["total_post"]
        scrape_run.videos_new = struktur_result["saved_to_database"]
        scrape_run.videos_duplicate = struktur_result["duplicate_removed"]
        scrape_run.finished_at = datetime.now(timezone.utc)
        scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()
        await db.commit()

        await log_activity(db, run_id, "pipeline", "done", "Pipeline selesai sukses", details={"result": struktur_result})

        return {
            "status": "success",
            "run_id": str(run_id),
            "platform": "news",
            "topic": topic_result["topic"],
            "total_post": struktur_result["total_post"],
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
            "platform": "news",
            "topic": topic,
            "total_post": 0,
            "saved_to_database": 0,
            "duplicate_removed": 0,
            "failed": 0,
            "error": str(exc),
            "crawl_date": started_at.isoformat(),
        }


async def get_pipeline_log(db: AsyncSession, run_id: uuid.UUID) -> list[dict]:
    return await get_run_log(db, run_id)
