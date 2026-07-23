"""Tracking topik yg SUDAH dicoba per-platform (2026-07-23, permintaan
user "scraping berdasarkan topic dulu, kalau semua topic sudah
discraping lanjut pencarian global viral, habis itu balik lagi update
topic, begitu terus") -- pakai tabel `trend_recommendation_platform_usage`
yg SUDAH ADA (dibangun 2026-07-21 utk Threads, TIDAK PERNAH dipakai
platform lain sampai sekarang), BUKAN tabel baru."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trend_recommendations.models import TrendRecommendation
from app.domain.trend_recommendations.platform_usage_models import TrendRecommendationPlatformUsage


async def get_unused_topics_for_platform(db: AsyncSession, platform: str, limit: int) -> list[tuple[uuid.UUID, str]]:
    """[(trend_recommendation_id, topic)] yg BELUM pernah ditandai used
    utk platform ini, urut score desc, dedup per topik (topik yg sama
    bisa py >1 baris di tanggal berbeda)."""
    used_subq = select(TrendRecommendationPlatformUsage.trend_recommendation_id).where(
        TrendRecommendationPlatformUsage.platform == platform
    )
    result = await db.execute(
        select(TrendRecommendation.id, TrendRecommendation.topic)
        .where(TrendRecommendation.id.notin_(used_subq))
        .order_by(TrendRecommendation.recommendation_date.desc(), TrendRecommendation.score.desc())
        .limit(limit * 5)
    )
    seen: set[str] = set()
    out: list[tuple[uuid.UUID, str]] = []
    for reco_id, topic in result.all():
        if topic in seen:
            continue
        seen.add(topic)
        out.append((reco_id, topic))
        if len(out) >= limit:
            break
    return out


async def mark_topics_used(db: AsyncSession, platform: str, trend_recommendation_ids: list[uuid.UUID]) -> None:
    now = datetime.now(timezone.utc)
    for reco_id in trend_recommendation_ids:
        existing = await db.scalar(
            select(TrendRecommendationPlatformUsage).where(
                TrendRecommendationPlatformUsage.trend_recommendation_id == reco_id,
                TrendRecommendationPlatformUsage.platform == platform,
            )
        )
        if existing:
            continue
        db.add(TrendRecommendationPlatformUsage(trend_recommendation_id=reco_id, platform=platform, used_at=now))
    await db.commit()


async def reset_platform_usage(db: AsyncSession, platform: str) -> int:
    """Hapus SEMUA tanda 'used' platform ini -- siklus topik mulai
    ulang dari awal (dipanggil SEKALI setiap selesai 1 putaran global
    viral, lihat auto_crawl_common.py)."""
    result = await db.execute(
        delete(TrendRecommendationPlatformUsage).where(TrendRecommendationPlatformUsage.platform == platform)
    )
    await db.commit()
    return result.rowcount
