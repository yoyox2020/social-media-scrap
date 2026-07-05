"""
Orkestrasi viral discovery harian — file TERPISAH dari trend_scrape_service.py
(yang dibekukan, jangan disentuh) supaya frozen file itu tidak pernah perlu
diubah untuk fitur ini.

Alur: Claude (web_search) cari topik+akun Instagram viral hari ini
(app/ai/llm/viral_discovery_service.py) → submit ke trend_recommendations via
submit_recommendations() yang SUDAH ADA (dipanggil apa adanya, bukan
dimodifikasi) → catat satu ScrapeRun sebagai "bukti status pencarian" hari itu.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.scrape_runs.models import ScrapeRun
from app.domain.trend_recommendations.schemas import TrendRecommendationBatchCreate

logger = logging.getLogger(__name__)


async def run_daily_viral_discovery(db: AsyncSession) -> dict:
    """
    Jalankan satu putaran viral discovery: cari topik viral hari ini via AI,
    submit ke trend_recommendations (fungsi frozen, dipanggil apa adanya),
    catat hasilnya sebagai satu baris scrape_runs.
    """
    from app.ai.llm.viral_discovery_service import find_daily_viral_topics
    from app.services.trend_recommendations.service import submit_recommendations

    started_at = datetime.now(timezone.utc)
    scrape_run = ScrapeRun(
        keyword_text="ai_viral_discovery",
        platform="instagram",
        api_source="anthropic_web_search",
        status="running",
        triggered_by="celery_beat",
        started_at=started_at,
    )
    db.add(scrape_run)
    await db.flush()

    items: list[dict] = []
    result: dict = {"created": [], "updated": [], "evicted": [], "rejected": []}

    try:
        items = await find_daily_viral_topics()
        if items:
            body = TrendRecommendationBatchCreate(items=items, source="ai_viral_discovery")
            result = await submit_recommendations(db, body)

        scrape_run.status = "success" if items else "failed"
        scrape_run.videos_fetched = len(items)
        scrape_run.videos_new = len(result.get("created", []))
        if not items:
            scrape_run.error_message = "Tidak ada topik viral ditemukan hari ini"
    except Exception as exc:
        logger.error("run_daily_viral_discovery error: %s", exc)
        scrape_run.status = "failed"
        scrape_run.error_message = str(exc)[:1000]
        result = {"error": str(exc)}
    finally:
        scrape_run.finished_at = datetime.now(timezone.utc)
        scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()
        await db.commit()

    logger.info("run_daily_viral_discovery: found=%d submitted=%s", len(items), result)
    return {"found": len(items), "submitted": result}
