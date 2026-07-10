"""
Multi-Signal Trend Discovery API — endpoint MANDIRI, TIDAK menggantikan
GET /trend-recommendations (jalur AI-discovery lama) ATAU GET /trends
(fitur lama: volume/sentimen trend per keyword, app/api/v1/trends.py) —
prefix SENGAJA dibuat beda (/trend-discovery) supaya tidak bentrok nama
modul maupun path dengan keduanya. Dibuat sbg modul terpisah supaya tiap
sumber sinyal bisa dipantau/dikelola independen (lihat app/services/trends/
untuk metodologi lengkap).

GET  /trend-discovery/twitter            — topik Trends X native hari ini
GET  /trend-discovery/twitter/status     — riwayat run + jadwal pipeline Twitter
GET  /trend-discovery/tiktok             — topik sapuan TikTok hari ini
GET  /trend-discovery/tiktok/status      — riwayat run + jadwal pipeline TikTok
GET  /trend-discovery/instagram          — topik sapuan Instagram hari ini
GET  /trend-discovery/instagram/status   — riwayat run + jadwal pipeline Instagram
GET  /trend-discovery                    — topik yang lolos TRIANGULASI (>=1
                                            sumber tambahan konfirmasi),
                                            diurutkan confidence_score
GET  /trend-discovery/status             — ringkasan status SEMUA pipeline
                                            (utk dashboard/monitoring cepat)
POST /trend-discovery/run                — trigger manual satu pipeline
                                            (?source=twitter|tiktok|instagram|
                                            combined), buat testing/debug
                                            tanpa nunggu jadwal
GET  /trend-discovery/timeline            — volume topik dari waktu ke waktu
                                            (multi-series). Default AUTO-
                                            DISCOVER topik ter-ramai (dari
                                            entitas NER) di rentang tanggal
                                            yang diminta -- tidak perlu tau
                                            nama topiknya duluan, cuma perlu
                                            date_from/date_to. `keywords`
                                            opsional utk override manual.
                                            TIDAK terikat 5-sumber
                                            triangulasi di atas, independen.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.services.auth.dependencies import get_current_user
from app.shared.utils import build_success_response

router = APIRouter(prefix="/trend-discovery", tags=["trend-discovery"])


def _topic_to_dict(row) -> dict:
    payload = row.raw_payload if isinstance(row.raw_payload, dict) else {}
    return {
        "topic": row.topic,
        "score": row.score,
        "related_accounts": row.related_accounts,
        "status": row.status,
        "confirmed_by": payload.get("confirmed_by"),
        "confidence_score": payload.get("confidence_score"),
        "recommendation_date": row.recommendation_date.isoformat(),
    }


async def _topics_by_source(db: AsyncSession, source: str, target_date: date) -> list[dict]:
    from app.domain.trend_recommendations.models import TrendRecommendation

    rows = (
        await db.execute(
            select(TrendRecommendation)
            .where(TrendRecommendation.source == source, TrendRecommendation.recommendation_date == target_date)
            .order_by(TrendRecommendation.score.desc())
        )
    ).scalars().all()
    return [_topic_to_dict(r) for r in rows]


async def _scrape_run_status(db: AsyncSession, platform: str, recent_limit: int = 10) -> dict:
    from app.domain.scrape_runs.models import ScrapeRun

    runs = (
        await db.execute(
            select(ScrapeRun)
            .where(ScrapeRun.platform == platform)
            .order_by(ScrapeRun.started_at.desc())
            .limit(recent_limit)
        )
    ).scalars().all()

    now = datetime.now(timezone.utc)
    running = [r for r in runs if r.status == "running"]

    return {
        "recent_runs": [
            {
                "status": r.status,
                "triggered_by": r.triggered_by,
                "videos_fetched": r.videos_fetched,
                "videos_new": r.videos_new,
                "duration_seconds": round(r.duration_seconds, 2) if r.duration_seconds is not None else None,
                "error_message": r.error_message,
                "started_at": r.started_at.isoformat(),
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in runs
        ],
        "running_now": [
            {
                "started_at": r.started_at.isoformat(),
                "elapsed_seconds": round((now - r.started_at).total_seconds(), 1),
            }
            for r in running
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Per-platform: topik + status
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/twitter", response_model=dict, summary="Topik Trends X native hari ini")
async def get_twitter_trends(
    target_date: date | None = Query(default=None, alias="date", description="Default: hari ini"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    d = target_date or date.today()
    topics = await _topics_by_source(db, "twitter_native_trend", d)
    return build_success_response({"date": d.isoformat(), "source": "twitter_native_trend", "total": len(topics), "topics": topics})


@router.get("/twitter/status", response_model=dict, summary="Status pipeline Twitter Trends")
async def get_twitter_trends_status(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from app.shared.config import settings

    status = await _scrape_run_status(db, "twitter_trends")
    status["schedule"] = f"{settings.twitter_trends_schedule_hour:02d}:{settings.twitter_trends_schedule_minute:02d} WIB otomatis (Celery Beat)"
    return build_success_response(status)


@router.get("/tiktok", response_model=dict, summary="Topik sapuan TikTok hari ini")
async def get_tiktok_trends(
    target_date: date | None = Query(default=None, alias="date", description="Default: hari ini"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    d = target_date or date.today()
    topics = await _topics_by_source(db, "tiktok_hashtag_sweep", d)
    return build_success_response({"date": d.isoformat(), "source": "tiktok_hashtag_sweep", "total": len(topics), "topics": topics})


@router.get("/tiktok/status", response_model=dict, summary="Status pipeline TikTok sweep")
async def get_tiktok_trends_status(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from app.shared.config import settings

    status = await _scrape_run_status(db, "tiktok_trends")
    status["schedule"] = f"{settings.tiktok_trends_schedule_hour:02d}:{settings.tiktok_trends_schedule_minute:02d} WIB otomatis (Celery Beat)"
    return build_success_response(status)


@router.get("/instagram", response_model=dict, summary="Topik sapuan Instagram hari ini")
async def get_instagram_trends(
    target_date: date | None = Query(default=None, alias="date", description="Default: hari ini"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    d = target_date or date.today()
    topics = await _topics_by_source(db, "instagram_hashtag_sweep", d)
    return build_success_response({"date": d.isoformat(), "source": "instagram_hashtag_sweep", "total": len(topics), "topics": topics})


@router.get("/instagram/status", response_model=dict, summary="Status pipeline Instagram sweep")
async def get_instagram_trends_status(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from app.shared.config import settings

    status = await _scrape_run_status(db, "instagram_trends")
    status["schedule"] = f"{settings.instagram_trends_schedule_hour:02d}:{settings.instagram_trends_schedule_minute:02d} WIB otomatis (Celery Beat)"
    return build_success_response(status)


# ─────────────────────────────────────────────────────────────────────────────
# Gabungan (triangulasi)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("", response_model=dict, summary="Topik yang lolos triangulasi lintas sumber")
async def get_combined_trends(
    target_date: date | None = Query(default=None, alias="date", description="Default: hari ini"),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0, description="Filter minimal confidence_score"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Topik trending yang SUDAH divalidasi lintas >=1 sumber independen
    (Twitter Trends native, sapuan TikTok/Instagram, Google Trends, YouTube
    TrendingTopic) — lihat app/services/trends/combined_trend_service.py
    untuk metodologi confidence_score. Diurutkan dari confidence tertinggi.

    Termasuk baris yang confidence_score-nya belum dihitung (pipeline
    gabungan belum jalan hari ini) -- confidence_score akan null.
    """
    from app.domain.trend_recommendations.models import TrendRecommendation

    d = target_date or date.today()
    rows = (
        await db.execute(
            select(TrendRecommendation)
            .where(TrendRecommendation.recommendation_date == d)
        )
    ).scalars().all()

    topics = [_topic_to_dict(r) for r in rows]
    topics = [t for t in topics if (t["confidence_score"] or 0.0) >= min_confidence]
    topics.sort(key=lambda t: (t["confidence_score"] or 0.0, t["score"]), reverse=True)

    return build_success_response({"date": d.isoformat(), "total": len(topics), "topics": topics})


@router.get("/status", response_model=dict, summary="Ringkasan status semua pipeline Trend Discovery")
async def get_trends_status(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from app.shared.config import settings

    platforms = {
        "twitter": ("twitter_trends", settings.twitter_trends_schedule_hour, settings.twitter_trends_schedule_minute),
        "tiktok": ("tiktok_trends", settings.tiktok_trends_schedule_hour, settings.tiktok_trends_schedule_minute),
        "instagram": ("instagram_trends", settings.instagram_trends_schedule_hour, settings.instagram_trends_schedule_minute),
        "combined": ("trends_combined", settings.trends_combined_schedule_hour, settings.trends_combined_schedule_minute),
    }

    summary = {}
    for name, (platform, hour, minute) in platforms.items():
        status = await _scrape_run_status(db, platform, recent_limit=3)
        latest = status["recent_runs"][0] if status["recent_runs"] else None
        summary[name] = {
            "schedule": f"{hour:02d}:{minute:02d} WIB",
            "latest_run": latest,
            "running_now": bool(status["running_now"]),
        }

    return build_success_response(summary)


# ─────────────────────────────────────────────────────────────────────────────
# Trigger manual (testing/debug)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/run", response_model=dict, summary="Trigger manual satu pipeline Trend Discovery")
async def trigger_trend_discovery(
    source: str = Query(..., description="twitter | tiktok | instagram | combined"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Jalankan satu pipeline SEKARANG (sinkron, tunggu hasil) — buat testing
    tanpa nunggu jadwal Celery Beat. Sama persis dengan logic yang dipanggil
    task terjadwal, cuma dipicu manual."""
    if source == "twitter":
        from app.services.trends.twitter_trend_service import run_twitter_trend_discovery
        result = await run_twitter_trend_discovery(db)
    elif source == "tiktok":
        from app.services.trends.tiktok_trend_service import run_tiktok_trend_discovery
        result = await run_tiktok_trend_discovery(db)
    elif source == "instagram":
        from app.services.trends.instagram_trend_service import run_instagram_trend_discovery
        result = await run_instagram_trend_discovery(db)
    elif source == "combined":
        from app.services.trends.combined_trend_service import run_combined_trend_discovery
        result = await run_combined_trend_discovery(db)
    else:
        raise HTTPException(status_code=422, detail="source harus salah satu: twitter, tiktok, instagram, combined")

    return build_success_response({"source": source, "result": result})


# ─────────────────────────────────────────────────────────────────────────────
# Timeline (volume mention per keyword dari waktu ke waktu)
# ─────────────────────────────────────────────────────────────────────────────

_ALL_PLATFORMS = ["instagram", "facebook", "tiktok", "twitter", "youtube", "news"]
_VALID_PLATFORMS = set(_ALL_PLATFORMS)
_MAX_BUCKETS = 1000


# NOT (huruf kecil semua DAN >=2 tanda hubung) -- buang noise fragmen URL
# slug yang kadang ikut ter-NER dari teks navigasi/link situs News (lihat
# docs analisis /news/analysis/summary, filter yang sama dipakai di sini).
_ENTITY_NOISE_FILTER = "AND NOT (e.text = lower(e.text) AND (length(e.text) - length(replace(e.text, '-', ''))) >= 2)"


@router.get("/timeline", response_model=dict, summary="Volume topik dari waktu ke waktu (auto-discover ATAU keyword manual)")
async def get_trend_timeline(
    keywords: str | None = Query(default=None, description="Daftar keyword/frasa dipisah koma. KOSONG (default): auto-pilih `top_n` topik paling banyak disebut di rentang tanggal ini -- tidak perlu tau nama topiknya duluan."),
    top_n: int = Query(default=6, ge=1, le=15, description="Jumlah topik auto-pilih kalau `keywords` kosong"),
    date_from: date | None = Query(default=None, description="Filter dari tanggal (YYYY-MM-DD). Kosong: date_to - 7 hari (atau dari `hours` kalau date_to juga kosong)"),
    date_to: date | None = Query(default=None, description="Filter sampai tanggal (YYYY-MM-DD), inklusif. Kosong: hari ini"),
    hours: int = Query(default=24, ge=1, le=168, description="Dipakai HANYA kalau date_from & date_to keduanya kosong — rentang jam ke belakang dari sekarang, maks 168 (7 hari)"),
    interval: str = Query(default="hour", pattern="^(hour|day)$", description="Granularitas bucket: hour atau day"),
    platform: str | None = Query(default=None, description="Filter opsional ke satu platform saja. Kosong (default): semua platform digabung"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Volume topik dari waktu ke waktu (multi-series time-series) — dipakai
    utk chart timeline / deteksi lonjakan pembicaraan (burst). Sumbu utama
    filter-nya TANGGAL, bukan platform — platform cuma filter opsional.

    **Dua mode pemilihan topik:**
    1. `keywords` KOSONG (default) — AUTO-DISCOVER: `top_n` topik yang
       paling banyak disebut (dari tabel `entities`, hasil NER) di rentang
       tanggal ini otomatis dipilih dan di-chart, TANPA perlu tau nama
       topiknya duluan. Ini yang bikin chart bisa langsung tampil banyak
       garis begitu buka halaman (mirip tool social listening pada
       umumnya) -- match PERSIS ke entitas (bukan substring).
    2. `keywords` diisi — MANUAL: pakai persis keyword yang dikasih (ILIKE
       ke `posts.content`, cocok frasa apa saja termasuk yang bukan
       entitas bersih).

    TIDAK terikat ke 5-sumber triangulasi endpoint lain di modul ini — baca
    langsung dari `posts`/`entities`, lintas SEMUA platform termasuk News.

    **Dibucket dari `published_at`** (waktu ASLI post/artikel dibuat, BUKAN
    `collected_at`/waktu kita scrape) — lihat docs/trend-discovery-api.md
    soal kelengkapan data per platform. Post/artikel yang `published_at`-nya
    NULL otomatis tidak ikut terhitung.

    **Filter waktu:** `date_from`/`date_to` (tanggal kalender, PALING
    diprioritaskan) — kalau kosong, fallback ke `hours` (rentang N jam ke
    belakang dari sekarang).

    Bucket KOSONG tetap muncul dengan `count: 0` (tidak di-skip) supaya
    chart line tidak berlubang.
    """
    if platform and platform not in _VALID_PLATFORMS:
        raise HTTPException(status_code=422, detail=f"platform harus salah satu: {', '.join(sorted(_VALID_PLATFORMS))}")

    trunc_unit = "hour" if interval == "hour" else "day"
    step = timedelta(hours=1) if interval == "hour" else timedelta(days=1)

    now = datetime.now(timezone.utc)
    if date_from or date_to:
        resolved_date_to = date_to or now.date()
        resolved_date_from = date_from or (resolved_date_to - timedelta(days=7))
        since_aligned = datetime.combine(resolved_date_from, time.min, tzinfo=timezone.utc)
        until = datetime.combine(resolved_date_to, time.min, tzinfo=timezone.utc) + timedelta(days=1)
    else:
        until = now
        since_aligned = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=hours)
        if interval == "day":
            since_aligned = since_aligned.replace(hour=0)

    bucket_count = int((until - since_aligned) / step) + 1
    if bucket_count > _MAX_BUCKETS:
        raise HTTPException(
            status_code=422,
            detail=f"Rentang waktu terlalu lebar ({bucket_count} bucket) — perlebar interval ke 'day' atau perkecil rentang tanggal (maks {_MAX_BUCKETS} bucket)",
        )

    all_buckets: list[datetime] = []
    cursor = since_aligned
    while cursor < until:
        all_buckets.append(cursor)
        cursor += step

    platform_clause_posts = "AND platform = :platform" if platform else ""
    platform_clause_joined = "AND p.platform = :platform" if platform else ""
    platform_param = {"platform": platform} if platform else {}
    platforms_to_show = [platform] if platform else _ALL_PLATFORMS

    auto_mode = not keywords
    if auto_mode:
        top_rows = (await db.execute(text(f"""
            SELECT e.text, count(DISTINCT e.post_id) AS mentions
            FROM entities e
            JOIN posts p ON p.id = e.post_id
            WHERE p.published_at >= :since AND p.published_at < :until AND p.published_at IS NOT NULL
              {_ENTITY_NOISE_FILTER}
              {platform_clause_joined}
            GROUP BY e.text
            ORDER BY mentions DESC
            LIMIT :top_n
        """), {"since": since_aligned, "until": until, "top_n": top_n, **platform_param})).mappings().all()
        kw_list = [r["text"] for r in top_rows]
    else:
        kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
        if not kw_list:
            raise HTTPException(status_code=422, detail="keywords tidak boleh kosong string")
        if len(kw_list) > 10:
            raise HTTPException(status_code=422, detail="maks 10 keyword per request")

    series: dict[str, dict] = {}
    for kw in kw_list:
        if auto_mode:
            rows = (await db.execute(text(f"""
                SELECT date_trunc(:trunc_unit, p.published_at) AS bucket, p.platform, count(DISTINCT p.id) AS cnt
                FROM entities e
                JOIN posts p ON p.id = e.post_id
                WHERE e.text = :entity_text
                  AND p.published_at >= :since AND p.published_at < :until AND p.published_at IS NOT NULL
                  {platform_clause_joined}
                GROUP BY bucket, p.platform
            """), {
                "trunc_unit": trunc_unit, "entity_text": kw,
                "since": since_aligned, "until": until, **platform_param,
            })).mappings().all()
        else:
            rows = (await db.execute(text(f"""
                SELECT date_trunc(:trunc_unit, published_at) AS bucket, platform, count(*) AS cnt
                FROM posts
                WHERE published_at >= :since AND published_at < :until AND published_at IS NOT NULL
                  AND content ILIKE :pattern
                  {platform_clause_posts}
                GROUP BY bucket, platform
            """), {
                "trunc_unit": trunc_unit, "pattern": f"%{kw}%",
                "since": since_aligned, "until": until, **platform_param,
            })).mappings().all()

        counts_by_bucket_platform: dict[tuple, int] = {}
        totals_by_bucket: dict[datetime, int] = {}
        for r in rows:
            counts_by_bucket_platform[(r["bucket"], r["platform"])] = r["cnt"]
            totals_by_bucket[r["bucket"]] = totals_by_bucket.get(r["bucket"], 0) + r["cnt"]

        series[kw] = {
            "total": [
                {"bucket": b.isoformat(), "count": totals_by_bucket.get(b, 0)}
                for b in all_buckets
            ],
            "by_platform": {
                p: [
                    {"bucket": b.isoformat(), "count": counts_by_bucket_platform.get((b, p), 0)}
                    for b in all_buckets
                ]
                for p in platforms_to_show
            },
        }

    return build_success_response({
        "mode":       "auto_discover" if auto_mode else "manual_keywords",
        "date_from":  since_aligned.date().isoformat(),
        "date_to":    (until - timedelta(seconds=1)).date().isoformat(),
        "since":      since_aligned.isoformat(),
        "until":      until.isoformat(),
        "interval":   interval,
        "platform":   platform or "all",
        "keywords":   kw_list,
        "series":     series,
    })
