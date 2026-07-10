"""
Twitter/X Trend Discovery — pipeline MANDIRI, bagian dari Multi-Signal Trend
Discovery (lihat app/services/trends/combined_trend_service.py untuk
triangulasi lintas sumber). TIDAK menyentuh app/ai/llm/viral_discovery_service.py
atau kode Twitter/X yang sudah ada (app/services/twitter/) sama sekali —
cuma "menitip" hasil ke trend_recommendations lewat submit_recommendations()
yang SUDAH ADA.

Sumber: Trends BAWAAN X sendiri (app/integrations/apify/twitter_trends.py),
sinyal PALING objektif yang tersedia -- bukan tebakan AI, bukan turunan data
kita sendiri.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.scrape_runs.models import ScrapeRun

logger = logging.getLogger(__name__)


def _score_from_rank(rank: int, max_rank: int) -> float:
    """Rank 1 (paling trending) -> skor tertinggi, menurun linear."""
    if max_rank <= 1:
        return 1.0
    step = 0.6 / (max_rank - 1)
    return round(max(0.3, 1.0 - (rank - 1) * step), 3)


async def run_twitter_trend_discovery(db: AsyncSession) -> dict:
    """
    Ambil Trends X native untuk Indonesia, submit tiap trend sebagai topik
    ke trend_recommendations (source='twitter_native_trend'). `related_accounts`
    SENGAJA kosong (trend cuma nama topik, bukan akun) -- tujuan pipeline ini
    visibilitas "apa yang genuinely trending", bukan otomatis memicu scrape
    akun (beda dari discover_twitter_topic_by_keyword yang sudah ada).
    """
    from app.domain.trend_recommendations.schemas import TrendRecommendationBatchCreate, TrendRecommendationItem
    from app.integrations.apify.twitter_trends import fetch_twitter_trends
    from app.services.trend_recommendations.service import submit_recommendations

    started_at = datetime.now(timezone.utc)
    scrape_run = ScrapeRun(
        keyword_text="twitter_native_trend_discovery", platform="twitter_trends", api_source="apify",
        status="running", triggered_by="celery_beat", started_at=started_at,
    )
    db.add(scrape_run)
    await db.commit()

    raw_trends: list[dict] = []

    try:
        raw_trends = await fetch_twitter_trends()

        items = []
        max_rank = max((t.get("rank", 1) for t in raw_trends), default=1)
        for t in raw_trends:
            name = t.get("name")
            if not name:
                continue
            rank = t.get("rank", max_rank)
            items.append(TrendRecommendationItem(
                topic=name,
                score=_score_from_rank(rank, max_rank),
                related_accounts=[],
            ))

        result = {"created": [], "updated": [], "evicted": [], "rejected": []}
        if items:
            body = TrendRecommendationBatchCreate(items=items, source="twitter_native_trend")
            result = await submit_recommendations(db, body)

        scrape_run.status = "success" if items else "failed"
        scrape_run.videos_fetched = len(raw_trends)
        scrape_run.videos_new = len(result.get("created", []))
        if not items:
            scrape_run.error_message = "Tidak ada trend ditemukan hari ini"
    except Exception as exc:
        logger.error("run_twitter_trend_discovery error: %s", exc)
        scrape_run.status = "failed"
        scrape_run.error_message = str(exc)[:1000]
        result = {"error": str(exc)}
    finally:
        scrape_run.finished_at = datetime.now(timezone.utc)
        scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()
        await db.commit()

    logger.info("run_twitter_trend_discovery: %s", result)
    return {"found": len(raw_trends), "submitted": result}
