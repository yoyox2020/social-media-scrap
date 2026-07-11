"""
Smart Search -- pemindaian berkala harian (Celery task
workers.search_topics.daily_rescan, lihat app/workers/search_topics_worker.py).

Utk tiap SearchTopic yang MASIH dalam window jadwal (schedule_recurring=True,
schedule_expires_at > sekarang, is_active=True), scan ulang tiap
keyword x platform:

1. Cek tier-1 dulu (DB, murah) -- kalau SUDAH ada post baru dalam jendela
   cooldown platform itu, LEWATI platform ini hari ini (sudah ada yang
   nyuplai data, tidak perlu keluar biaya lagi). Ini kontrol biaya UTAMA,
   berlaku sama rata semua platform.
2. Kalau basi (tidak ada post baru dalam cooldown):
   - Facebook/TikTok/Twitter (account-discovery model): cek dulu apakah
     trend_recommendations SUDAH punya entri keyword ini dgn akun platform
     terkait HARI INI -- kalau ADA, LEWATI tier-3 (biarkan task harian
     platform itu sendiri, workers.X_trend_recommendation.daily, yang
     scrape -- jangan dobel keluar biaya Apify search utk hal yang sudah
     diketahui). Kalau BELUM ada, baru panggil discover_X_topic_by_keyword()
     (source='smart_search_X', dapat reserved slot di trend_recommendations).
   - Instagram/News (direct-post model, TIDAK ADA jalur "refresh murah" --
     lihat discovery.py): cooldown-gated saja, langsung tier-3 kalau basi.
     Cooldown-nya SENGAJA lebih panjang (settings.search_topic_rescan_cooldown_hours_expensive)
     drpd platform lain, krn tiap panggilan selalu Apify/Firecrawl call nyata.
   - YouTube: cooldown-gated saja, trigger collect_youtube_pipeline_task
     (quota Data API, bukan Apify).
3. Tulis SearchTopicKeyword.last_rescanned_at, catat SATU ScrapeRun
   (platform='search_topics') utk ringkasan seluruh run hari itu.

TIDAK mengubah logic/jadwal budget harian platform manapun yang sudah ada
-- cuma memicu discovery BARU kalau genuinely belum ada apa-apa yang bisa
diandalkan, sisanya diserahkan ke pipeline yang sudah jalan.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.scrape_runs.models import ScrapeRun
from app.domain.search_topics.models import SearchTopic
from app.services.search_topics import discovery, tier_search

logger = logging.getLogger(__name__)


async def _has_related_account_today(db: AsyncSession, keyword_text: str, platform: str) -> bool:
    """Cek apakah trend_recommendations SUDAH punya entri keyword ini dgn
    akun platform ini HARI INI -- kalau ya, jangan panggil tier-3 lagi
    (biarkan task harian platform itu sendiri yang scrape akunnya)."""
    from app.domain.trend_recommendations.models import TrendRecommendation

    row = await db.scalar(
        select(TrendRecommendation).where(
            TrendRecommendation.topic == keyword_text,
            TrendRecommendation.recommendation_date == date.today(),
        )
    )
    if not row:
        return False
    return any(a.get("platform") == platform for a in (row.related_accounts or []))


async def run_daily_search_topic_rescan(db: AsyncSession) -> dict:
    """Entry point dipanggil worker Celery. Return ringkasan run (dipakai
    log + ScrapeRun.videos_fetched/videos_new)."""
    from app.shared.config import settings

    cooldown_default = timedelta(hours=settings.search_topic_rescan_cooldown_hours)
    cooldown_expensive = timedelta(hours=settings.search_topic_rescan_cooldown_hours_expensive)

    started_at = datetime.now(timezone.utc)
    scrape_run = ScrapeRun(
        keyword_text="search_topics_daily_rescan", platform="search_topics", api_source="internal",
        status="running", triggered_by="celery_beat", started_at=started_at,
    )
    db.add(scrape_run)
    await db.commit()

    topics_scanned = 0
    keywords_rescanned = 0
    tier3_triggered = 0
    skipped_fresh = 0
    skipped_pending = 0
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
        )).all()

        for topic in topics:
            topics_scanned += 1
            for stk in topic.topic_keywords:
                for platform in topic.platforms:
                    if platform not in discovery.ALL_SMART_SEARCH_PLATFORMS:
                        continue

                    cooldown = (
                        cooldown_expensive if platform in discovery.DIRECT_POST_PLATFORMS else cooldown_default
                    )
                    since = started_at - cooldown

                    fresh_posts = await tier_search.find_posts_by_keyword(
                        db, stk.keyword_text, [platform], limit=1, since=since,
                    )
                    if fresh_posts:
                        skipped_fresh += 1
                        continue

                    source_tag = None
                    if platform in discovery.ACCOUNT_DISCOVERY_PLATFORMS:
                        if await _has_related_account_today(db, stk.keyword_text, platform):
                            skipped_pending += 1
                            continue
                        source_tag = f"smart_search_{platform}"

                    try:
                        result = await discovery.run_tier3_discovery(
                            db, platform, stk.keyword_text, max_results=10, source_tag=source_tag,
                        )
                        tier3_triggered += 1
                        if result.get("error"):
                            errors.append(f"{topic.name}/{stk.keyword_text}/{platform}: {result['error']}")
                    except Exception as exc:
                        logger.error(
                            "run_daily_search_topic_rescan: gagal utk %s/%s/%s: %s",
                            topic.name, stk.keyword_text, platform, exc,
                        )
                        errors.append(f"{topic.name}/{stk.keyword_text}/{platform}: {exc}")

                stk.last_rescanned_at = started_at
                keywords_rescanned += 1
                await db.commit()

        scrape_run.status = "success"
        scrape_run.videos_fetched = keywords_rescanned
        scrape_run.videos_new = tier3_triggered
        if errors:
            scrape_run.error_message = "; ".join(errors)[:1000]
    except Exception as exc:
        logger.error("run_daily_search_topic_rescan error: %s", exc)
        scrape_run.status = "failed"
        scrape_run.error_message = str(exc)[:1000]
    finally:
        scrape_run.finished_at = datetime.now(timezone.utc)
        scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()
        await db.commit()

    result = {
        "topics_scanned": topics_scanned,
        "keywords_rescanned": keywords_rescanned,
        "tier3_triggered": tier3_triggered,
        "skipped_fresh": skipped_fresh,
        "skipped_pending": skipped_pending,
        "errors": errors[:10],
    }
    logger.info("run_daily_search_topic_rescan: %s", result)
    return result
