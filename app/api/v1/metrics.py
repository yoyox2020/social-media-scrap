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
from app.services.metrics.calculator import (
    ADAPTER_REGISTRY,
    KEYWORD_ID_RELIABLE_PLATFORMS,
    SORTABLE_ENGAGEMENT_COMPONENTS,
    compute_metrics,
    fetch_keyword_texts,
    fetch_mention_count,
    fetch_post_detail_page,
    fetch_reach_detail_page,
    fetch_sentiment_detail_page,
    _needs_text_match,
)
from app.services.search_topics.tier_search import _multi_keyword_or_clause
from app.shared.exceptions import NotFoundError, ValidationError
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


def _platforms_label(platforms: list[str]) -> list[str]:
    """`platforms=[]` (default, TANPA filter query) tetap dilaporkan sbg
    daftar platform yg TERDAFTAR di response -- supaya frontend tidak lihat
    array kosong yg ambigu ("kosong = semua" tidak jelas tanpa baca kode)."""
    return platforms if platforms else list(ADAPTER_REGISTRY.keys())


# ── Endpoint 1: Summary Global ────────────────────────────────────────────────

@router.get("/summary", response_model=dict)
async def get_metrics_summary(
    platforms: list[str] = Query(default=[], description="Kosong (default) = SEMUA platform, tanpa filter. Isi eksplisit utk batasi ke platform tertentu."),
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
        "platforms": _platforms_label(platforms),
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "metrics": result,
    })


# ── Endpoint 2: Metrik per Keyword ───────────────────────────────────────────

@router.get("/keyword/{keyword_id}", response_model=dict)
async def get_keyword_metrics(
    keyword_id: uuid.UUID,
    platforms: list[str] = Query(default=[], description="Kosong (default) = SEMUA platform, tanpa filter. Isi eksplisit utk batasi ke platform tertentu."),
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
        "platforms": _platforms_label(platforms),
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "metrics": result,
    })


# ── Endpoint 2b: Drill-down -- data MENTAH di balik satu metrik ──────────────
# Permintaan user 2026-07-18: "user menyorot mention harus jelas sumber
# datanya darimana dan bisa diarahkan ke detail mention" -- endpoint ini
# dipanggil saat user klik salah satu angka (Mentions/Reach/Exposure/
# Engagement/Sentiment) di kartu platform, balikin daftar post/komentar
# MENTAH (bukan cuma angka) yg menyusun angka itu, tiap item bawa `id`
# (+`url` platform asli) yg bisa diklik lanjut oleh frontend.

VALID_DETAIL_METRICS = {"mentions", "exposure", "engagement", "reach", "sentiment"}


@router.get("/keyword/{keyword_id}/detail", response_model=dict)
async def get_keyword_metric_detail(
    keyword_id: uuid.UUID,
    metric: str = Query(..., description=f"Salah satu dari: {sorted(VALID_DETAIL_METRICS)}"),
    platform: str | None = Query(default=None, description="Satu platform (kosong = semua platform digabung)"),
    sentiment_label: str | None = Query(default=None, description="Filter khusus metric='sentiment': positif/negatif/netral"),
    sort_by: str | None = Query(
        default=None,
        description="HANYA berlaku metric=mentions/exposure/engagement. Kosong = published_at terbaru dulu. "
                    f"Isi salah satu dari {sorted(SORTABLE_ENGAGEMENT_COMPONENTS)} utk urut post PALING BANYAK dulu "
                    "(mis. klik segmen 'Likes' di grafik komposisi -> sort_by=likes).",
    ),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Drill-down: daftar data MENTAH (post/komentar) di balik salah satu
    angka metrik keyword ini -- SAMA PERSIS filter keyword+platform+periode
    yg dipakai /metrics/keyword/{id} utk hitung angka summary-nya, jadi
    daftar ini DIJAMIN konsisten dgn angka yg ditampilkan di kartu.

    - metric=mentions|exposure|engagement -> daftar POST (id, url, author,
      published_at, views, engagement breakdown)
    - metric=reach -> daftar AKUN UNIK (author, platform, jumlah post)
    - metric=sentiment -> daftar KOMENTAR (label, konten, post asal)
    """
    if metric not in VALID_DETAIL_METRICS:
        raise ValidationError(f"metric harus salah satu dari {sorted(VALID_DETAIL_METRICS)}")
    if sort_by is not None and sort_by not in SORTABLE_ENGAGEMENT_COMPONENTS:
        raise ValidationError(f"sort_by harus salah satu dari {sorted(SORTABLE_ENGAGEMENT_COMPONENTS)}")

    keyword = await db.scalar(select(Keyword).where(Keyword.id == keyword_id))
    if not keyword:
        raise NotFoundError(f"Keyword {keyword_id} tidak ditemukan")

    if not date_from or not date_to:
        date_from, date_to = _default_period()

    platforms = [platform] if platform else []
    keyword_texts = await fetch_keyword_texts(db, [keyword_id])

    if metric == "reach":
        items, total = await fetch_reach_detail_page(
            db, [keyword_id], platforms, date_from, date_to, page, limit, keyword_texts,
        )
    elif metric == "sentiment":
        items, total = await fetch_sentiment_detail_page(
            db, [keyword_id], platforms, date_from, date_to, page, limit, sentiment_label, keyword_texts,
        )
    else:  # mentions | exposure | engagement
        items, total = await fetch_post_detail_page(
            db, [keyword_id], platforms, date_from, date_to, page, limit, keyword_texts, sort_by,
        )

    return build_success_response({
        "scope": "keyword_detail",
        "keyword": {"id": str(keyword.id), "text": keyword.keyword},
        "metric": metric,
        "sort_by": sort_by or "published_at",
        "platforms": _platforms_label(platforms),
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "pagination": {"page": page, "limit": limit, "total": total, "total_pages": max(1, (total + limit - 1) // limit)},
        "items": items,
    })


# ── Endpoint 3: Metrik per Topik ─────────────────────────────────────────────

@router.get("/topic/{topic_id}", response_model=dict)
async def get_topic_metrics(
    topic_id: uuid.UUID,
    platforms: list[str] = Query(default=[], description="Kosong (default) = SEMUA platform, tanpa filter. Isi eksplisit utk batasi ke platform tertentu."),
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
            "platforms": _platforms_label(platforms),
            "metrics": {},
            "note": "Topik belum punya keyword terhubung",
        })

    if not date_from or not date_to:
        date_from, date_to = _default_period()

    compare_from, compare_to = (_prev_period(date_from, date_to) if include_growth else (None, None))
    all_kw_ids = list((await db.scalars(select(Keyword.id).where(Keyword.is_active == True))).all())

    # Fetch teks keyword SEKALI (gabungan kw_ids topik ini + all_kw_ids
    # global) -- dipakai ULANG di panggilan agregat DAN tiap iterasi
    # breakdown_per_keyword di bawah, bukan di-fetch ulang tiap kali
    # (N+1, ditemukan 2026-07-18 saat audit performa).
    shared_kw_texts = await fetch_keyword_texts(db, list({*kw_ids, *all_kw_ids}))

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
        keyword_texts=shared_kw_texts,
    )

    response: dict = {
        "scope": "topic",
        "topic": {"id": str(topic.id), "name": topic.name},
        "platforms": _platforms_label(platforms),
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
                keyword_texts=shared_kw_texts,
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
    platforms: list[str] = Query(default=[], description="Kosong (default) = SEMUA platform, tanpa filter. Isi eksplisit utk batasi ke platform tertentu."),
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

    # Fetch teks SEMUA keyword SEKALI (dulu: 1 query per keyword di loop
    # bawah + 1 query lagi di dalam _keyword_condition tiap panggilan
    # fetch_mention_count = ~3N+1 query total utk N keyword. Sekarang:
    # 1 query di sini + N query count (tidak bisa dihindari, ILIKE per-
    # keyword genuinely beda pattern) = N+2. Ditemukan 2026-07-18 saat
    # audit performa /metrics/*.)
    kw_texts = await fetch_keyword_texts(db, keyword_ids)

    total_mentions = await fetch_mention_count(db, keyword_ids, platforms, date_from, date_to, kw_texts)

    items = []
    for kw_id in keyword_ids:
        kw_text = kw_texts.get(kw_id)
        if not kw_text:
            continue
        kw_mentions = await fetch_mention_count(db, [kw_id], platforms, date_from, date_to, kw_texts)
        sov_pct = round(kw_mentions / total_mentions * 100, 2) if total_mentions > 0 else 0.0
        items.append({
            "keyword_id": str(kw_id),
            "keyword": kw_text,
            "mentions": kw_mentions,
            "sov_pct": sov_pct,
        })

    # Urutkan dari SOV terbesar
    items.sort(key=lambda x: x["sov_pct"], reverse=True)

    return build_success_response({
        "scope": "sov_comparison",
        "platforms": _platforms_label(platforms),
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "total_mentions": total_mentions,
        "items": items,
    })


# ── Endpoint 5: Time Series Mention (untuk grafik tren) ───────────────────────

@router.get("/trend", response_model=dict)
async def get_mention_trend(
    keyword_ids: list[uuid.UUID] = Query(default=[]),
    topic_id: uuid.UUID | None = Query(default=None),
    platforms: list[str] = Query(default=[], description="Kosong (default) = SEMUA platform, tanpa filter. Isi eksplisit utk batasi ke platform tertentu."),
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
        kwid_placeholders = ", ".join([f":kwid{i}" for i in range(len(keyword_ids))])
        for i, kid in enumerate(keyword_ids):
            params[f"kwid{i}"] = str(kid)
        by_keyword_id = f"posts.keyword_id IN ({kwid_placeholders})"

        if not _needs_text_match(platforms):
            # Cabang IDENTIK dgn kode lama -- semua platform yg diminta
            # reliable (keyword_id terisi), tidak perlu ILIKE tambahan.
            filters_sql.append(by_keyword_id)
        else:
            texts = [k for k in (await db.scalars(
                select(Keyword.keyword).where(Keyword.id.in_(keyword_ids))
            )).all() if k]
            if not texts:
                filters_sql.append(by_keyword_id)
            else:
                # Platform reliable (YouTube) tetap disaring via keyword_id asli;
                # platform lain (keyword_id NULL) disaring via ILIKE teks --
                # pola sama dgn tier_search._multi_keyword_or_clause.
                text_match = _multi_keyword_or_clause("posts.content", texts, params)
                reliable = list(KEYWORD_ID_RELIABLE_PLATFORMS)
                rp_placeholders = ", ".join([f":rp{i}" for i in range(len(reliable))])
                for i, rp in enumerate(reliable):
                    params[f"rp{i}"] = rp
                filters_sql.append(
                    f"((posts.platform IN ({rp_placeholders}) AND {by_keyword_id}) "
                    f"OR (posts.platform NOT IN ({rp_placeholders}) AND {text_match}))"
                )

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
        "platforms": _platforms_label(platforms),
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "series": series,
    })
