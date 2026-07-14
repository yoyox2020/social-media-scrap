"""
Smart Search -- AI-context discovery ("Subsistem A2"): AI dipandu topik+keyword
yang SUDAH disimpan user (SearchTopic, schedule_recurring=true) sebagai
KONTEKS, diminta cari PERKEMBANGAN/SUB-TOPIK BARU terkait tema itu. BEDA dari
dua mekanisme yang sudah ada:
- app/ai/llm/viral_discovery_service.py (Subsistem A) -- sapuan buta, AI
  TIDAK tahu topik yang user simpan sama sekali.
- app/services/search_topics/rescan_service.py -- rescan LITERAL keyword yang
  SAMA persis tiap hari (ILIKE/Apify keyword search), TANPA AI, tidak bisa
  menangkap perkembangan baru yang kata-katanya beda dari keyword tersimpan.

Hasil disubmit ke trend_recommendations yang SAMA (source=_SOURCE_TAG di
bawah), otomatis diambil pipeline scrape harian tiap platform yang SUDAH ADA
(Subsistem B) -- TIDAK ada pipa scraping baru di file ini.

Dipicu Celery Beat harian (workers.search_topics.ai_discovery_daily, lihat
app/workers/search_topics_worker.py, jadwal
settings.smart_search_ai_discovery_schedule_hour/minute, default 08:00 WIB --
SETELAH rescan literal 06:00 & blind sweep 07:00 supaya cek "sudah tercover"
di bawah lihat hasil keduanya, tapi SEBELUM konsumer harian Instagram 09:00
supaya sub-topik yang ditemukan sempat kepilih hari yang sama).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.scrape_runs.models import ScrapeRun
from app.domain.search_topics.models import SearchTopic
from app.domain.trend_recommendations.models import TrendRecommendation

logger = logging.getLogger(__name__)

# Sama seperti viral_discovery_service._SUPPORTED_PLATFORMS -- platform yang
# AI-context discovery ini boleh submit akunnya. Instagram IKUT walau di
# discovery.py (Smart Search literal tier-3) Instagram itu DIRECT_POST_PLATFORM
# -- di sini kode ini bypass discovery.py total, submit langsung ke
# trend_recommendations (sama seperti blind sweep Subsistem A), dan Subsistem
# B Instagram sendiri (instagram_trend_recommendation.daily) memang konsumsi
# baris trend_recommendations APA PUN sumbernya asal ada akun instagram.
TARGET_PLATFORMS = {"instagram", "facebook", "tiktok", "twitter"}

_SOURCE_TAG = "smart_search_ai_discovery"
_SUMMARY_KEYWORD = "search_topics_ai_discovery_run"
_TOPIC_RUN_PLATFORM = "search_topics_ai_discovery_topic"


async def run_daily_search_topic_ai_discovery(db: AsyncSession) -> dict:
    """
    Entry point dipanggil worker Celery. Utk tiap SearchTopic recurring
    (urut last_ai_discovery_at ASC NULLS FIRST -- rotasi adil, yang belum/
    paling lama dipanggil menang duluan): cek platform mana yang BELUM
    tercover hari ini (has_related_account_today, gratis/cuma query DB),
    kalau ada yang uncovered DAN budget belum habis, panggil AI 1x/topik
    (bukan per-platform, lebih hemat) utk semua platform uncovered topik itu
    sekaligus, submit hasil ke trend_recommendations.

    Budget (settings.smart_search_ai_discovery_max_topics_per_run) dihitung
    dari PERCOBAAN panggilan AI (berhasil maupun gagal -- exception tetap
    menghabiskan biaya API), BUKAN dari topik yang di-skip krn sudah tercover
    (skip itu gratis, cuma query DB, tidak boleh ikut menghabiskan budget).
    """
    from app.ai.llm.viral_discovery_service import find_topic_scoped_updates
    from app.domain.trend_recommendations.schemas import TrendRecommendationBatchCreate
    from app.services.search_topics.rescan_service import has_related_account_today
    from app.services.trend_recommendations.service import submit_recommendations
    from app.shared.config import settings

    started_at = datetime.now(timezone.utc)
    summary_run = ScrapeRun(
        keyword_text=_SUMMARY_KEYWORD, platform="search_topics",
        api_source=settings.ai_discovery_provider, status="running",
        triggered_by="celery_beat", started_at=started_at,
    )
    db.add(summary_run)
    await db.flush()

    considered = 0
    called = 0
    skipped_covered = 0
    skipped_no_platform = 0
    errors: list[str] = []

    try:
        topics = (await db.scalars(
            select(SearchTopic)
            .options(selectinload(SearchTopic.topic_keywords))
            .where(
                SearchTopic.is_active == True,  # noqa: E712
                SearchTopic.schedule_recurring == True,  # noqa: E712
                SearchTopic.schedule_expires_at > started_at,
            )
            .order_by(SearchTopic.last_ai_discovery_at.asc().nulls_first())
        )).all()

        for topic in topics:
            considered += 1
            target_platforms = [p for p in topic.platforms if p in TARGET_PLATFORMS]
            keywords = [stk.keyword_text for stk in topic.topic_keywords]
            if not target_platforms or not keywords:
                skipped_no_platform += 1
                continue

            uncovered = [
                p for p in target_platforms
                if not await has_related_account_today(db, keywords, p)
            ]
            if not uncovered:
                skipped_covered += 1
                continue

            if called >= settings.smart_search_ai_discovery_max_topics_per_run:
                break

            topic_started = datetime.now(timezone.utc)
            topic_run = ScrapeRun(
                keyword_text=topic.name, platform=_TOPIC_RUN_PLATFORM,
                api_source=settings.ai_discovery_provider, status="running",
                triggered_by="celery_beat", started_at=topic_started,
            )
            db.add(topic_run)
            await db.flush()

            try:
                items = await find_topic_scoped_updates(
                    topic.name, keywords, uncovered,
                    settings.smart_search_ai_discovery_max_subtopics_per_topic,
                )
                created: list[str] = []
                if items:
                    body = TrendRecommendationBatchCreate(items=items, source=_SOURCE_TAG)
                    result = await submit_recommendations(db, body)
                    created = result.get("created", [])

                topic_run.status = "success" if items else "failed"
                topic_run.videos_fetched = len(items)
                topic_run.videos_new = len(created)
                if not items:
                    topic_run.error_message = "Tidak ada perkembangan baru ditemukan utk topik ini"
                topic.last_ai_discovery_at = topic_started
            except Exception as exc:
                logger.error(
                    "run_daily_search_topic_ai_discovery: gagal utk topik %s: %s", topic.name, exc,
                )
                topic_run.status = "failed"
                topic_run.error_message = str(exc)[:1000]
                errors.append(f"{topic.name}: {exc}")
            finally:
                topic_run.finished_at = datetime.now(timezone.utc)
                topic_run.duration_seconds = (topic_run.finished_at - topic_started).total_seconds()
                await db.commit()

            called += 1

        summary_run.status = "success"
        summary_run.videos_fetched = considered
        summary_run.videos_new = called
        if errors:
            summary_run.error_message = "; ".join(errors)[:1000]
    except Exception as exc:
        logger.error("run_daily_search_topic_ai_discovery error: %s", exc)
        summary_run.status = "failed"
        summary_run.error_message = str(exc)[:1000]
    finally:
        summary_run.finished_at = datetime.now(timezone.utc)
        summary_run.duration_seconds = (summary_run.finished_at - started_at).total_seconds()
        await db.commit()

    result = {
        "topics_considered": considered,
        "topics_called": called,
        "topics_skipped_covered": skipped_covered,
        "topics_skipped_no_platform": skipped_no_platform,
        "errors": errors[:10],
    }
    logger.info("run_daily_search_topic_ai_discovery: %s", result)
    return result


async def get_search_topic_ai_discovery_trace(db: AsyncSession) -> dict:
    """
    Lacak run TERAKHIR AI-context discovery: per topik yang DIPANGGIL (bukan
    yang di-skip krn sudah tercover), tampilkan sub-topik baru yang ditemukan
    + status scrape-nya di Subsistem B -- pola identik get_viral_discovery_trace()
    di app/services/trend_recommendations/viral_discovery_scrape_service.py,
    cuma dikelompokkan per context-topic (bukan satu window flat) karena satu
    run bisa mencakup beberapa SearchTopic sekaligus. Fungsi baca-saja.
    """
    summary_run = (await db.scalars(
        select(ScrapeRun)
        .where(ScrapeRun.platform == "search_topics", ScrapeRun.keyword_text == _SUMMARY_KEYWORD)
        .order_by(ScrapeRun.started_at.desc())
        .limit(1)
    )).first()

    if summary_run is None:
        return {"last_run": None, "topics": []}

    window_end = (summary_run.finished_at or summary_run.started_at) + timedelta(seconds=5)
    topic_runs = (await db.scalars(
        select(ScrapeRun)
        .where(
            ScrapeRun.platform == _TOPIC_RUN_PLATFORM,
            ScrapeRun.started_at >= summary_run.started_at,
            ScrapeRun.started_at <= window_end,
        )
        .order_by(ScrapeRun.started_at)
    )).all()

    topics_traced = []
    for run in topic_runs:
        sub_window_end = (run.finished_at or run.started_at) + timedelta(seconds=5)
        found = (await db.scalars(
            select(TrendRecommendation)
            .where(
                TrendRecommendation.source == _SOURCE_TAG,
                TrendRecommendation.created_at >= run.started_at,
                TrendRecommendation.created_at <= sub_window_end,
            )
            .order_by(TrendRecommendation.score.desc())
        )).all()

        subtopics = []
        for item in found:
            # Lacak apakah Subsistem B (konsumer harian tiap platform) sudah
            # scrape sub-topik ini -- ScrapeRun dgn keyword_text SAMA PERSIS
            # dibuat oleh run_daily_trend_scrape_facebook()/tiktok/twitter/dst.
            b_run = (await db.scalars(
                select(ScrapeRun)
                .where(ScrapeRun.keyword_text == item.topic, ScrapeRun.started_at > run.started_at)
                .order_by(ScrapeRun.started_at.desc())
                .limit(1)
            )).first()
            subtopics.append({
                "subtopic": item.topic,
                "score": item.score,
                "related_accounts": item.related_accounts,
                "current_status": item.status,
                "scrape_attempt": {
                    "status": b_run.status,
                    "api_source": b_run.api_source,
                    "started_at": b_run.started_at.isoformat(),
                    "duration_seconds": round(b_run.duration_seconds, 2) if b_run.duration_seconds is not None else None,
                    "error_message": b_run.error_message,
                } if b_run else None,
            })

        # Best-effort -- topik bisa saja sudah diganti nama/dihapus sejak run ini
        ctx_topic = (await db.scalars(
            select(SearchTopic).where(SearchTopic.name == run.keyword_text).limit(1)
        )).first()

        topics_traced.append({
            "context_topic_id": str(ctx_topic.id) if ctx_topic else None,
            "context_topic_name": run.keyword_text,
            "ai_call_status": run.status,
            "ai_call_started_at": run.started_at.isoformat(),
            "duration_seconds": round(run.duration_seconds, 2) if run.duration_seconds is not None else None,
            "error_message": run.error_message,
            "found_subtopics": subtopics,
        })

    return {
        "last_run": {
            "status": summary_run.status,
            "api_source": summary_run.api_source,
            "started_at": summary_run.started_at.isoformat(),
            "finished_at": summary_run.finished_at.isoformat() if summary_run.finished_at else None,
            "topics_considered": summary_run.videos_fetched,
            "topics_called": summary_run.videos_new,
            "error_message": summary_run.error_message,
        },
        "topics": topics_traced,
    }
