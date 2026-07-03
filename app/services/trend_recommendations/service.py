from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trend_recommendations.models import TrendRecommendation
from app.domain.trend_recommendations.schemas import TrendRecommendationBatchCreate, TrendRecommendationItem

MAX_PER_DAY = 20


async def submit_recommendations(
    db: AsyncSession,
    body: TrendRecommendationBatchCreate,
) -> dict[str, list[str]]:
    """
    Upsert topik viral untuk satu hari, dibatasi maksimal MAX_PER_DAY baris.

    - Topik yang sudah ada di hari itu -> update score/related_accounts.
    - Topik baru & slot masih tersedia -> insert.
    - Topik baru & slot penuh -> gantikan topik dengan score terendah kalau
      score baru lebih tinggi, kalau tidak -> ditolak.
    """
    reco_date = body.recommendation_date or date.today()

    existing_rows = (
        await db.execute(
            select(TrendRecommendation).where(TrendRecommendation.recommendation_date == reco_date)
        )
    ).scalars().all()
    existing_by_topic = {row.topic: row for row in existing_rows}
    active_rows = list(existing_rows)

    created: list[str] = []
    updated: list[str] = []
    evicted: list[str] = []
    rejected: list[str] = []

    # Dedupe dalam satu payload — kalau ada topik sama, ambil skor tertinggi.
    incoming_items: dict[str, TrendRecommendationItem] = {}
    for item in body.items:
        prev = incoming_items.get(item.topic)
        if prev is None or item.score > prev.score:
            incoming_items[item.topic] = item

    for item in incoming_items.values():
        related_accounts = [ra.model_dump() for ra in item.related_accounts]

        existing = existing_by_topic.get(item.topic)
        if existing is not None:
            existing.score = item.score
            existing.related_accounts = related_accounts
            existing.source = body.source
            updated.append(item.topic)
            continue

        if len(active_rows) < MAX_PER_DAY:
            new_row = TrendRecommendation(
                topic=item.topic,
                score=item.score,
                related_accounts=related_accounts,
                source=body.source,
                recommendation_date=reco_date,
                raw_payload=item.model_dump(),
            )
            db.add(new_row)
            active_rows.append(new_row)
            existing_by_topic[item.topic] = new_row
            created.append(item.topic)
            continue

        lowest = min(active_rows, key=lambda r: r.score)
        if item.score > lowest.score:
            active_rows.remove(lowest)
            existing_by_topic.pop(lowest.topic, None)
            evicted.append(lowest.topic)
            await db.delete(lowest)

            new_row = TrendRecommendation(
                topic=item.topic,
                score=item.score,
                related_accounts=related_accounts,
                source=body.source,
                recommendation_date=reco_date,
                raw_payload=item.model_dump(),
            )
            db.add(new_row)
            active_rows.append(new_row)
            existing_by_topic[item.topic] = new_row
            created.append(item.topic)
        else:
            rejected.append(item.topic)

    await db.commit()

    return {
        "created": created,
        "updated": updated,
        "evicted": evicted,
        "rejected": rejected,
    }
