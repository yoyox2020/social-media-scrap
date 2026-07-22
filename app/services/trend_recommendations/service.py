"""Input topik MANUAL oleh user (2026-07-22, permintaan user "user juga
bisa input data topic") -- pakai tabel `trend_recommendations` yg SUDAH
ADA (dipakai bareng platform lain via score), BUKAN tabel terpisah.
Ditandai `source="manual_user"` supaya beda dari submission AI eksternal
(`source="external_ai"`), tapi ranking (score) diperlakukan SAMA --
topik manual dgn score tinggi otomatis ikut masuk TOP 20 yg dibaca
worker auto-crawl (lihat app/workers/youtube_auto_crawl_worker.py).

Aturan max 20/topik per hari (recommendation_date) SUDAH didokumentasikan
di model TrendRecommendation -- kalau penuh, topik baru MENGGANTIKAN yg
score-nya paling rendah HANYA jika score baru lebih tinggi (bukan asal
timpa)."""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trend_recommendations.models import TrendRecommendation

MAX_TOPICS_PER_DAY = 20


async def submit_manual_topic(db: AsyncSession, topic: str, score: float = 1.0) -> dict:
    topic = topic.strip()
    today = date.today()

    existing_result = await db.execute(
        select(TrendRecommendation).where(
            TrendRecommendation.topic == topic,
            TrendRecommendation.recommendation_date == today,
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        existing.score = max(existing.score, score)
        await db.commit()
        return {"action": "updated", "topic": topic, "score": existing.score, "recommendation_date": str(today)}

    count_result = await db.execute(
        select(func.count()).select_from(TrendRecommendation)
        .where(TrendRecommendation.recommendation_date == today)
    )
    count = count_result.scalar_one()

    if count >= MAX_TOPICS_PER_DAY:
        lowest_result = await db.execute(
            select(TrendRecommendation)
            .where(TrendRecommendation.recommendation_date == today)
            .order_by(TrendRecommendation.score.asc())
            .limit(1)
        )
        lowest = lowest_result.scalar_one_or_none()
        if not lowest or lowest.score >= score:
            return {
                "action": "rejected", "topic": topic,
                "reason": f"Kuota {MAX_TOPICS_PER_DAY} topik/hari penuh & score {score} tidak lebih tinggi dari topik terendah ({lowest.score if lowest else 'n/a'})",
            }
        evicted_topic = lowest.topic
        await db.delete(lowest)
        await db.flush()
    else:
        evicted_topic = None

    new_row = TrendRecommendation(
        topic=topic, score=score, source="manual_user",
        recommendation_date=today, status="pending",
    )
    db.add(new_row)
    await db.commit()
    return {
        "action": "created", "topic": topic, "score": score,
        "recommendation_date": str(today), "evicted": evicted_topic,
    }


async def list_top_topics(db: AsyncSession, limit: int = MAX_TOPICS_PER_DAY) -> list[dict]:
    result = await db.execute(
        select(TrendRecommendation)
        .order_by(TrendRecommendation.recommendation_date.desc(), TrendRecommendation.score.desc())
        .limit(limit * 5)
    )
    seen: set[str] = set()
    topics: list[dict] = []
    for row in result.scalars().all():
        if row.topic in seen:
            continue
        seen.add(row.topic)
        topics.append({
            "topic": row.topic, "score": row.score, "source": row.source,
            "recommendation_date": str(row.recommendation_date), "status": row.status,
        })
        if len(topics) >= limit:
            break
    return topics
