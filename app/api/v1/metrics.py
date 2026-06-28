"""
Dashboard Metrics API.

Endpoint untuk menampilkan 7 metrik utama di dashboard:
  - Exposure, Reach, Engagement, Engagement Rate,
    Sentiment Score, Share of Voice, Mention Growth.

Setiap endpoint menerima filter: platform, date_from, date_to,
dan compare_* untuk kalkulasi pertumbuhan.
"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.keywords.models import Keyword
from app.domain.posts.models import Post
from app.domain.search_topics.models import SearchTopic, SearchTopicKeyword
from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.services.auth.dependencies import get_current_user
from app.services.metrics.calculator import compute_metrics, fetch_mention_count
from app.shared.exceptions import NotFoundError
from app.shared.utils import build_success_response

router = APIRouter(prefix="/metrics", tags=["metrics"])


# ── Helper ────────────────────────────────────────────────────────────────────

def _default_period() -> tuple[datetime, datetime]:
    """Default: 30 hari terakhir."""
    now = datetime.now(timezone.utc)
    return now - timedelta(days=30), now


def _prev_period(date_from: datetime, date_to: datetime) -> tuple[datetime, datetime]:
    """Periode sebelumnya dengan durasi yang sama (untuk mention growth)."""
    duration = date_to - date_from
    return date_from - duration, date_from


# ── Endpoint 1: Summary Global ────────────────────────────────────────────────

@router.get("/summary", response_model=dict)
async def get_metrics_summary(
    platforms: list[str] = Query(default=["youtube"]),
    date_from: datetime | None = Query(default=None, description="ISO format, default 30 hari lalu"),
    date_to: datetime | None = Query(default=None, description="ISO format, default sekarang"),
    include_growth: bool = Query(default=True, description="Hitung Mention Growth vs periode sebelumnya"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Metrik global seluruh keyword di semua topik.
    Cocok untuk widget ringkasan di halaman utama dashboard.
    """
    if not date_from or not date_to:
        date_from, date_to = _default_period()

    compare_from, compare_to = (_prev_period(date_from, date_to) if include_growth else (None, None))

    all_kw_ids = list((await db.scalars(select(Keyword.id).where(Keyword.is_active == True))).all())

    result = await compute_metrics(
        db=db,
        keyword_ids=all_kw_ids,
        platforms=platforms,
        date_from=date_from,
        date_to=date_to,
        compare_date_from=compare_from,
        compare_date_to=compare_to,
        all_keyword_ids=None,  # SOV tidak relevan untuk summary global
    )

    return build_success_response({
        "scope": "global",
        "platforms": platforms,
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "metrics": result,
    })


# ── Endpoint 2: Metrik per Keyword ───────────────────────────────────────────

@router.get("/keyword/{keyword_id}", response_model=dict)
async def get_keyword_metrics(
    keyword_id: uuid.UUID,
    platforms: list[str] = Query(default=["youtube"]),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    include_growth: bool = Query(default=True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Metrik untuk satu keyword spesifik, termasuk SOV dibandingkan semua keyword.
    """
    keyword = await db.scalar(select(Keyword).where(Keyword.id == keyword_id))
    if not keyword:
        raise NotFoundError(f"Keyword {keyword_id} tidak ditemukan")

    if not date_from or not date_to:
        date_from, date_to = _default_period()

    compare_from, compare_to = (_prev_period(date_from, date_to) if include_growth else (None, None))

    all_kw_ids = list((await db.scalars(select(Keyword.id).where(Keyword.is_active == True))).all())

    result = await compute_metrics(
        db=db,
        keyword_ids=[keyword_id],
        platforms=platforms,
        date_from=date_from,
        date_to=date_to,
        compare_date_from=compare_from,
        compare_date_to=compare_to,
        all_keyword_ids=all_kw_ids,
    )

    return build_success_response({
        "scope": "keyword",
        "keyword": {"id": str(keyword.id), "text": keyword.keyword},
        "platforms": platforms,
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "metrics": result,
    })


# ── Endpoint 3: Metrik per Topik ─────────────────────────────────────────────

@router.get("/topic/{topic_id}", response_model=dict)
async def get_topic_metrics(
    topic_id: uuid.UUID,
    platforms: list[str] = Query(default=["youtube"]),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    include_growth: bool = Query(default=True),
    breakdown_per_keyword: bool = Query(default=False, description="Tampilkan metrik per keyword dalam topik"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Metrik agregat untuk satu topik (semua keyword dalam topik dijumlah).
    Gunakan breakdown_per_keyword=true untuk melihat kontribusi tiap keyword.
    """
    from sqlalchemy.orm import selectinload
    topic = await db.scalar(
        select(SearchTopic)
        .options(selectinload(SearchTopic.topic_keywords))
        .where(SearchTopic.id == topic_id)
    )
    if not topic:
        raise NotFoundError(f"Topik {topic_id} tidak ditemukan")

    kw_ids = [stk.keyword_id for stk in topic.topic_keywords]
    if not kw_ids:
        return build_success_response({
            "scope": "topic",
            "topic": {"id": str(topic.id), "name": topic.name},
            "platforms": platforms,
            "metrics": {},
            "note": "Topik belum punya keyword terhubung",
        })

    if not date_from or not date_to:
        date_from, date_to = _default_period()

    compare_from, compare_to = (_prev_period(date_from, date_to) if include_growth else (None, None))
    all_kw_ids = list((await db.scalars(select(Keyword.id).where(Keyword.is_active == True))).all())

    # Metrik agregat seluruh topik
    result = await compute_metrics(
        db=db,
        keyword_ids=kw_ids,
        platforms=platforms,
        date_from=date_from,
        date_to=date_to,
        compare_date_from=compare_from,
        compare_date_to=compare_to,
        all_keyword_ids=all_kw_ids,
    )

    response: dict = {
        "scope": "topic",
        "topic": {"id": str(topic.id), "name": topic.name},
        "platforms": platforms,
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "total_keywords": len(kw_ids),
        "metrics": result,
    }

    # Opsional: breakdown per keyword
    if breakdown_per_keyword:
        kw_breakdown = []
        for stk in topic.topic_keywords:
            kw_result = await compute_metrics(
                db=db,
                keyword_ids=[stk.keyword_id],
                platforms=platforms,
                date_from=date_from,
                date_to=date_to,
                compare_date_from=compare_from,
                compare_date_to=compare_to,
                all_keyword_ids=all_kw_ids,
            )
            kw_breakdown.append({
                "keyword": stk.keyword_text,
                "keyword_id": str(stk.keyword_id),
                "metrics": kw_result,
            })
        response["keyword_breakdown"] = kw_breakdown

    return build_success_response(response)


# ── Endpoint 4: Perbandingan SOV Antar Keyword ───────────────────────────────

@router.get("/sov", response_model=dict)
async def get_share_of_voice(
    keyword_ids: list[uuid.UUID] = Query(default=[], description="Kosong = semua keyword aktif"),
    platforms: list[str] = Query(default=["youtube"]),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Share of Voice — perbandingan porsi mention antar keyword.
    Berguna untuk chart pie/bar di dashboard: keyword mana yang paling banyak dibicarakan.
    """
    if not date_from or not date_to:
        date_from, date_to = _default_period()

    # Kalau tidak ada filter → ambil semua keyword aktif
    if not keyword_ids:
        keyword_ids = list((await db.scalars(select(Keyword.id).where(Keyword.is_active == True))).all())

    total_mentions = await fetch_mention_count(db, keyword_ids, platforms, date_from, date_to)

    items = []
    for kw_id in keyword_ids:
        kw = await db.scalar(select(Keyword).where(Keyword.id == kw_id))
        if not kw:
            continue
        kw_mentions = await fetch_mention_count(db, [kw_id], platforms, date_from, date_to)
        sov_pct = round(kw_mentions / total_mentions * 100, 2) if total_mentions > 0 else 0.0
        items.append({
            "keyword_id": str(kw.id),
            "keyword": kw.keyword,
            "mentions": kw_mentions,
            "sov_pct": sov_pct,
        })

    # Urutkan dari SOV terbesar
    items.sort(key=lambda x: x["sov_pct"], reverse=True)

    return build_success_response({
        "scope": "sov_comparison",
        "platforms": platforms,
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "total_mentions": total_mentions,
        "items": items,
    })


# ── Endpoint 5: Time Series Mention (untuk grafik tren) ───────────────────────

@router.get("/trend", response_model=dict)
async def get_mention_trend(
    keyword_ids: list[uuid.UUID] = Query(default=[]),
    topic_id: uuid.UUID | None = Query(default=None),
    platforms: list[str] = Query(default=["youtube"]),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    granularity: str = Query(default="day", description="day / week / month"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Time series jumlah mention per hari/minggu/bulan — untuk grafik tren di dashboard.
    Bisa filter per keyword atau per topik.
    """
    if not date_from or not date_to:
        date_from, date_to = _default_period()

    # Resolve keyword_ids dari topic jika diberikan
    if topic_id and not keyword_ids:
        from sqlalchemy.orm import selectinload
        topic = await db.scalar(
            select(SearchTopic)
            .options(selectinload(SearchTopic.topic_keywords))
            .where(SearchTopic.id == topic_id)
        )
        if topic:
            keyword_ids = [stk.keyword_id for stk in topic.topic_keywords]

    gran_map = {"day": "day", "week": "week", "month": "month"}
    trunc = gran_map.get(granularity, "day")

    # Pakai text() agar date_trunc tidak di-parameterize (asyncpg limitation)
    from sqlalchemy import text as sa_text, bindparam
    date_expr = sa_text(f"date_trunc('{trunc}', COALESCE(posts.published_at, posts.collected_at))")

    filters_sql = ["COALESCE(posts.published_at, posts.collected_at) BETWEEN :df AND :dt"]
    params: dict = {"df": date_from, "dt": date_to}

    if keyword_ids:
        kw_placeholders = ", ".join([f":kw{i}" for i in range(len(keyword_ids))])
        filters_sql.append(f"posts.keyword_id IN ({kw_placeholders})")
        for i, kid in enumerate(keyword_ids):
            params[f"kw{i}"] = str(kid)

    if platforms:
        pl_placeholders = ", ".join([f":pl{i}" for i in range(len(platforms))])
        filters_sql.append(f"posts.platform IN ({pl_placeholders})")
        for i, pl in enumerate(platforms):
            params[f"pl{i}"] = pl

    where_clause = " AND ".join(filters_sql)
    raw_sql = sa_text(f"""
        SELECT date_trunc('{trunc}', COALESCE(posts.published_at, posts.collected_at)) AS period,
               COUNT(posts.id) AS mentions
        FROM posts
        WHERE {where_clause}
        GROUP BY date_trunc('{trunc}', COALESCE(posts.published_at, posts.collected_at))
        ORDER BY period
    """)
    rows = await db.execute(raw_sql, params)

    series = [
        {"period": row.period.isoformat() if row.period else None, "mentions": row.mentions}
        for row in rows.all()
    ]

    return build_success_response({
        "scope": "trend",
        "granularity": granularity,
        "platforms": platforms,
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "series": series,
    })
