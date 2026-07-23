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

from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trend_recommendations.keyword_models import TrendRecommendationKeyword
from app.domain.trend_recommendations.models import TrendRecommendation

MAX_TOPICS_PER_DAY = 20


async def add_keywords_for_topic(db: AsyncSession, trend_recommendation_id, keywords: list[str]) -> list[str]:
    """1 topik -> BEBERAPA keyword kustom (permintaan user 2026-07-24).
    Duplikat (keyword yg sama utk topik yg sama) di-skip diam2, bukan
    error -- boleh dipanggil berkali2 utk nambah keyword baru ke topik
    yg sudah ada."""
    now = datetime.now(timezone.utc)
    added: list[str] = []
    for kw in keywords:
        kw = kw.strip()
        if not kw:
            continue
        existing = await db.scalar(
            select(TrendRecommendationKeyword).where(
                TrendRecommendationKeyword.trend_recommendation_id == trend_recommendation_id,
                TrendRecommendationKeyword.keyword_text == kw,
            )
        )
        if existing:
            continue
        db.add(TrendRecommendationKeyword(trend_recommendation_id=trend_recommendation_id, keyword_text=kw, created_at=now))
        added.append(kw)
    await db.commit()
    return added


async def get_keywords_for_topic(db: AsyncSession, trend_recommendation_id) -> list[str]:
    result = await db.execute(
        select(TrendRecommendationKeyword.keyword_text)
        .where(TrendRecommendationKeyword.trend_recommendation_id == trend_recommendation_id)
        .order_by(TrendRecommendationKeyword.created_at)
    )
    return [row[0] for row in result.all()]


async def get_keywords_for_topic_text(db: AsyncSession, topic: str) -> list[str]:
    """Dipanggil agent_search.build_keywords() -- cari SEMUA baris
    trend_recommendations utk topik ini (bisa >1, beda tanggal), gabung
    keyword kustomnya jadi 1 list unik. Balikin [] kalau topik ini
    tidak py keyword kustom sama sekali (caller fallback ke 3-varian
    auto)."""
    reco_ids_result = await db.execute(
        select(TrendRecommendation.id).where(TrendRecommendation.topic == topic)
    )
    reco_ids = [row[0] for row in reco_ids_result.all()]
    if not reco_ids:
        return []
    result = await db.execute(
        select(TrendRecommendationKeyword.keyword_text)
        .where(TrendRecommendationKeyword.trend_recommendation_id.in_(reco_ids))
        .order_by(TrendRecommendationKeyword.created_at)
    )
    seen: set[str] = set()
    keywords: list[str] = []
    for row in result.all():
        if row[0] not in seen:
            seen.add(row[0])
            keywords.append(row[0])
    return keywords


async def submit_manual_topic(db: AsyncSession, topic: str, score: float = 1.0, keywords: list[str] | None = None) -> dict:
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
        added_keywords = await add_keywords_for_topic(db, existing.id, keywords) if keywords else []
        return {
            "action": "updated", "topic": topic, "score": existing.score, "recommendation_date": str(today),
            "keywords_added": added_keywords,
        }

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
    await db.refresh(new_row)
    added_keywords = await add_keywords_for_topic(db, new_row.id, keywords) if keywords else []
    return {
        "action": "created", "topic": topic, "score": score,
        "recommendation_date": str(today), "evicted": evicted_topic,
        "keywords_added": added_keywords,
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
            "id": str(row.id), "topic": row.topic, "score": row.score, "source": row.source,
            "recommendation_date": str(row.recommendation_date), "status": row.status,
            "keywords": await get_keywords_for_topic(db, row.id),
        })
        if len(topics) >= limit:
            break
    return topics
