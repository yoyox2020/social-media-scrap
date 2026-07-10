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
                                            DISCOVER topik ter-ramai (word
                                            count di posts.content) di
                                            rentang tanggal yang diminta --
                                            tidak perlu tau nama topiknya
                                            duluan, cuma perlu date_from/
                                            date_to. `keywords` opsional utk
                                            override manual.
                                            `include_topic_clusters=true`
                                            (opsional, SATU response yang
                                            sama) -- kata yang sering muncul
                                            BARENGAN otomatis digabung jadi
                                            satu "topik gabungan" (mis.
                                            "prabowo bank indonesia
                                            danantara"), tanpa perlu
                                            definisi filter manual. TIDAK
                                            terikat 5-sumber triangulasi di
                                            atas, independen.
GET  /trend-discovery/geo-distribution    — distribusi nama tempat (negara/
                                            kota) yang DISEBUT di post+
                                            komentar (BUKAN geotag/lokasi
                                            asli poster -- data itu tidak
                                            ada di platform manapun yang
                                            kita scrape, sudah diverifikasi).
                                            Sama metodologi tanggal/platform
                                            dgn /timeline, `from_posts` bisa
                                            dicocokkan langsung ke angka di
                                            /timeline (posts-only), independen.
"""
from __future__ import annotations

import itertools
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


# Auto-discover BERDASARKAN WORD COUNT (frekuensi kata mentah di
# posts.content), BUKAN entitas NER -- diverifikasi 2026-07-10: NER penuh
# (PERSON/ORGANIZATION/dst) SENGAJA cuma jalan utk News (run_ner=False utk
# medsos, hemat compute worker-ai), jadi kalau auto-discover pakai entitas,
# hasilnya cuma hashtag selama volume medsos > volume News. Word count TIDAK
# punya keterbatasan itu -- jalan sama rata di SEMUA platform (posts.content
# selalu ada, tidak tergantung pipeline NER opsional).

# Kata umum (Indonesia+Inggris) yang dibuang dari hasil word-count karena
# terlalu generik utk jadi sinyal "topik" (function words, bukan konten) --
# plus hashtag generik non-topik (dipakai di hampir semua post terlepas
# isinya). Daftar ini BUKAN daftar lengkap, gampang ditambah.
_STOPWORDS = {
    "yang", "dan", "atau", "di", "ke", "dari", "ini", "itu", "untuk", "dengan",
    "pada", "adalah", "akan", "juga", "tidak", "ada", "saat", "hari", "lagi",
    "saya", "kita", "kami", "anda", "dia", "mereka", "tak", "gak", "nggak",
    "ga", "sih", "deh", "dong", "kok", "aja", "saja", "banget", "bgt", "nya",
    "karena", "kalau", "kalo", "jadi", "bisa", "harus", "sudah", "sdh", "udah",
    "belum", "blm", "lebih", "kurang", "sangat", "oleh", "seperti", "tersebut",
    "tanpa", "hingga", "sampai", "antara", "bagi", "atas", "bawah", "dalam",
    "luar", "kata", "orang", "waktu", "tahun", "bulan", "para",
    "the", "a", "an", "of", "in", "on", "for", "and", "or", "is", "to", "are",
    "was", "were", "be", "been", "this", "that", "these", "those", "with",
    "as", "at", "by", "from", "it", "its",
    "fyp", "fypage", "foryou", "foryoupage", "viral", "trending", "trend",
    "viralvideo", "viraltiktok", "explore", "explorepage", "capcut", "cut",
    "video", "reels", "reel", "shorts", "share", "like", "follow", "followme",
    "xyzbca", "tiktok", "instagram", "instagood", "photooftheday",
    # Sisa HTML entity yang kadang tidak ke-strip bersih dari markdown
    # Firecrawl (News) -- "&amp;" dst kalau lolos jadi kata "amp" sendirian.
    "amp", "nbsp", "quot", "lt", "gt", "apos",
}

# Ambang co-occurrence utk clustering topik (include_topic_clusters=true):
# dua kata digabung jadi satu cluster kalau jumlah post yang mengandung
# KEDUA kata itu >= rasio ini dari total mention kata yang LEBIH JARANG di
# antara pasangan itu (mirip Jaccard, bukan hitungan mentah -- supaya adil
# lintas kata dgn frekuensi beda jauh). Sama filosofinya dgn threshold
# overlap-kata 0.5 yang dipakai combined_trend_service.py utk triangulasi
# lintas-sumber -- di sini dipilih lebih rendah (0.3) krn co-occurrence
# dalam TEKS (butuh 2 kata di post yang SAMA) secara alami lebih jarang
# terjadi drpd overlap kata ANTAR topik string.
_COOCCUR_RATIO_THRESHOLD = 0.3


@router.get("/timeline", response_model=dict, summary="Volume topik dari waktu ke waktu (auto-discover ATAU keyword manual)")
async def get_trend_timeline(
    keywords: str | None = Query(default=None, description="Daftar keyword/frasa dipisah koma. KOSONG (default): auto-pilih `top_n` topik paling banyak disebut di rentang tanggal ini -- tidak perlu tau nama topiknya duluan."),
    top_n: int = Query(default=6, ge=1, le=15, description="Jumlah topik auto-pilih kalau `keywords` kosong"),
    date_from: date | None = Query(default=None, description="Filter dari tanggal (YYYY-MM-DD). Kosong: date_to - 7 hari (atau dari `hours` kalau date_to juga kosong)"),
    date_to: date | None = Query(default=None, description="Filter sampai tanggal (YYYY-MM-DD), inklusif. Kosong: hari ini"),
    hours: int = Query(default=24, ge=1, le=168, description="Dipakai HANYA kalau date_from & date_to keduanya kosong — rentang jam ke belakang dari sekarang, maks 168 (7 hari)"),
    interval: str = Query(default="hour", pattern="^(hour|day)$", description="Granularitas bucket: hour atau day"),
    platform: str | None = Query(default=None, description="Filter opsional ke satu platform saja. Kosong (default): semua platform digabung"),
    include_platform_breakdown: bool = Query(default=False, description="True: tiap topik juga dapat breakdown per platform (respons jauh lebih besar). Default False: cuma `total` gabungan per topik, respons ringkas"),
    include_topic_clusters: bool = Query(default=False, description="True: kata yang sering muncul BARENGAN di post yang sama otomatis digabung jadi satu 'topik gabungan' (field topic_clusters di respons) -- utk chart 'Number per search/filter', TANPA definisi filter manual"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Volume topik dari waktu ke waktu (multi-series time-series) — dipakai
    utk chart timeline / deteksi lonjakan pembicaraan (burst). Sumbu utama
    filter-nya TANGGAL, bukan platform — platform cuma filter opsional.

    **Dua mode pemilihan topik:**
    1. `keywords` KOSONG (default) — AUTO-DISCOVER berdasarkan WORD COUNT:
       `top_n` KATA yang paling sering muncul di `posts.content` (SEMUA
       platform, TERMASUK News) pada rentang tanggal ini otomatis dipilih
       dan di-chart, TANPA perlu tau kata apa yang lagi ramai duluan. Kata
       umum/function word (yang, dan, untuk, dst) dan hashtag generik non-
       topik (fyp, capcut, dst) dibuang duluan (lihat `_STOPWORDS`, daftar
       tidak lengkap, gampang ditambah) supaya sinyalnya lebih bermakna.
       BUKAN entitas NER -- word count jalan SAMA RATA di semua platform,
       tidak tergantung NER (yang sengaja cuma aktif utk News, lihat
       app/services/news/pipeline_service.py).
    2. `keywords` diisi — MANUAL: pakai persis keyword/frasa yang dikasih.

    Kedua mode SAMA PERSIS cara pencarian mention-nya: ILIKE ke
    `posts.content` (cocok substring, bukan exact-word) — konsisten,
    auto-discover cuma beda di cara MEMILIH kata awal saja.

    TIDAK terikat ke 5-sumber triangulasi endpoint lain di modul ini.

    **Dibucket dari `published_at`** (waktu ASLI post/artikel dibuat, BUKAN
    `collected_at`/waktu kita scrape) — lihat docs/trend-discovery-api.md
    soal kelengkapan data per platform. Post/artikel yang `published_at`-nya
    NULL otomatis tidak ikut terhitung.

    **Filter waktu:** `date_from`/`date_to` (tanggal kalender, PALING
    diprioritaskan) — kalau kosong, fallback ke `hours` (rentang N jam ke
    belakang dari sekarang).

    **Volume data saat ini masih tipis** (diverifikasi live: topik
    ter-ramai pun biasanya cuma 2-3 mention per 10 hari) — banyak bucket
    `count: 0` di respons adalah REALITA data, bukan bug. Bucket KOSONG
    tetap muncul dengan `count: 0` (tidak di-skip) supaya chart line tidak
    berlubang.

    Default respons CUMA `total` gabungan per topik (ringkas). Set
    `include_platform_breakdown=true` kalau butuh breakdown per platform
    juga (respons jadi ~6x lebih besar).

    **Topik gabungan (opsional):** set `include_topic_clusters=true` utk
    dapat field `topic_clusters` tambahan -- kata-kata di `keywords` yang
    SERING MUNCUL BARENGAN di post yang sama (co-occurrence, bukan definisi
    filter manual) otomatis digabung jadi satu "topik" (mis. kata "prabowo",
    "bank", "indonesia", "danantara" yang sering nyambung di post yang sama
    -> jadi satu cluster "prabowo bank indonesia danantara"). Tiap cluster
    dapat `total_mentions` + `total` (bucket per hari/jam) yang SAMA
    strukturnya dengan per-kata di `series` -- cocok utk chart ranking
    "Number per search/filter" ATAU timeline per topik gabungan.
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
    platform_param = {"platform": platform} if platform else {}
    platforms_to_show = [platform] if platform else _ALL_PLATFORMS

    auto_mode = not keywords
    if auto_mode:
        # Tokenisasi + hitung frekuensi kata LANGSUNG di SQL (buang tanda
        # baca, split whitespace, lowercase) -- dilakukan di DB, bukan tarik
        # semua content ke Python, supaya efisien (artikel News bisa sampai
        # 20rb karakter/baris).
        top_rows = (await db.execute(text(f"""
            SELECT word, count(*) AS mentions
            FROM (
                SELECT lower(unnest(regexp_split_to_array(
                    regexp_replace(content, '[^\\w\\s]', ' ', 'g'), '\\s+'
                ))) AS word
                FROM posts
                WHERE published_at >= :since AND published_at < :until AND published_at IS NOT NULL
                  {platform_clause_posts}
            ) w
            WHERE length(word) > 2 AND word != ALL(:stopwords)
            GROUP BY word
            ORDER BY mentions DESC
            LIMIT :top_n
        """), {
            "since": since_aligned, "until": until, "top_n": top_n,
            "stopwords": list(_STOPWORDS), **platform_param,
        })).mappings().all()
        kw_list = [r["word"] for r in top_rows]
    else:
        kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
        if not kw_list:
            raise HTTPException(status_code=422, detail="keywords tidak boleh kosong string")
        if len(kw_list) > 10:
            raise HTTPException(status_code=422, detail="maks 10 keyword per request")

    series: dict[str, dict] = {}
    mentions_by_word: dict[str, int] = {}
    for kw in kw_list:
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

        total_mentions = sum(totals_by_bucket.values())
        mentions_by_word[kw] = total_mentions
        kw_series = {
            "total_mentions": total_mentions,
            "total": [
                {"bucket": b.isoformat(), "count": totals_by_bucket.get(b, 0)}
                for b in all_buckets
            ],
        }
        if include_platform_breakdown:
            kw_series["by_platform"] = {
                p: [
                    {"bucket": b.isoformat(), "count": counts_by_bucket_platform.get((b, p), 0)}
                    for b in all_buckets
                ]
                for p in platforms_to_show
            }
        series[kw] = kw_series

    response_body = {
        "mode":       "auto_discover" if auto_mode else "manual_keywords",
        "date_from":  since_aligned.date().isoformat(),
        "date_to":    (until - timedelta(seconds=1)).date().isoformat(),
        "since":      since_aligned.isoformat(),
        "until":      until.isoformat(),
        "interval":   interval,
        "platform":   platform or "all",
        "keywords":   kw_list,
        "series":     series,
    }

    if include_topic_clusters and len(kw_list) >= 2:
        response_body["topic_clusters"] = await _build_topic_clusters(
            db, kw_list, mentions_by_word, since_aligned, until, trunc_unit,
            all_buckets, platform_clause_posts, platform_param,
        )
    elif include_topic_clusters:
        # < 2 kata -- tidak ada pasangan utk dicek co-occurrence, tiap kata
        # jadi cluster sendiri (kalau ada 1 kata) atau kosong (0 kata).
        response_body["topic_clusters"] = [
            {"label": kw, "words": [kw], **series[kw]} for kw in kw_list
        ]

    return build_success_response(response_body)


async def _build_topic_clusters(
    db: AsyncSession,
    words: list[str],
    mentions_by_word: dict[str, int],
    since_aligned: datetime,
    until: datetime,
    trunc_unit: str,
    all_buckets: list[datetime],
    platform_clause_posts: str,
    platform_param: dict,
) -> list[dict]:
    """
    Cluster kata-kata di `words` yang SERING MUNCUL BARENGAN di post yang
    sama (co-occurrence), TANPA definisi filter manual -- lihat catatan
    metodologi _COOCCUR_RATIO_THRESHOLD. Union-find sederhana (bukan
    embedding/ML), sengaja mulai simpel dulu sama seperti pendekatan
    triangulasi di combined_trend_service.py.

    CATATAN soal union-find: sifatnya TRANSITIF ("chaining") -- kalau A-B
    lolos ambang DAN B-C lolos ambang, A/B/C digabung jadi 1 cluster walau
    A-C sendiri TIDAK pernah dicek langsung lolos ambang atau tidak. Ini
    perilaku NORMAL utk union-find, bukan bug -- kalau hasilnya kelihatan
    "kegabung semua jadi 1 cluster besar" utk suatu rentang tanggal, itu
    tandanya topik-topik itu memang saling nyambung erat di data (mis. semua
    bagian dari 1 peristiwa besar) -- solusinya naikkan _COOCCUR_RATIO_THRESHOLD,
    bukan ganti algoritma dulu (upgrade ke clustering yang lebih ketat/
    embedding kalau cara sederhana ini terbukti tidak cukup, sesuai prinsip
    "mulai sederhana dulu" yang sama dipakai combined_trend_service.py).
    """
    n = len(words)
    pairs = list(itertools.combinations(range(n), 2))

    cooccur_params: dict = {"since": since_aligned, "until": until, **platform_param}
    for i, w in enumerate(words):
        cooccur_params[f"w{i}"] = f"%{w}%"

    select_clauses = [
        f"count(*) FILTER (WHERE content ILIKE :w{i} AND content ILIKE :w{j}) AS c_{i}_{j}"
        for i, j in pairs
    ]
    cooccur_row = (await db.execute(text(f"""
        SELECT {', '.join(select_clauses)}
        FROM posts
        WHERE published_at >= :since AND published_at < :until AND published_at IS NOT NULL
          {platform_clause_posts}
    """), cooccur_params)).mappings().first()

    # Union-find: gabungkan indeks kata yang co-occurrence-nya lolos ambang.
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for i, j in pairs:
        cooccur = cooccur_row[f"c_{i}_{j}"] or 0
        denom = min(mentions_by_word[words[i]], mentions_by_word[words[j]])
        if denom > 0 and (cooccur / denom) >= _COOCCUR_RATIO_THRESHOLD:
            union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    clusters = []
    for indices in groups.values():
        cluster_words = [words[i] for i in indices]
        or_clause = " OR ".join(f"content ILIKE :cw{k}" for k in range(len(indices)))
        cluster_params = {f"cw{k}": f"%{cluster_words[k]}%" for k in range(len(indices))}

        rows = (await db.execute(text(f"""
            SELECT date_trunc(:trunc_unit, published_at) AS bucket, count(*) AS cnt
            FROM posts
            WHERE published_at >= :since AND published_at < :until AND published_at IS NOT NULL
              AND ({or_clause})
              {platform_clause_posts}
            GROUP BY bucket
        """), {
            "trunc_unit": trunc_unit, "since": since_aligned, "until": until,
            **cluster_params, **platform_param,
        })).mappings().all()

        totals_by_bucket = {r["bucket"]: r["cnt"] for r in rows}
        clusters.append({
            "label": " ".join(cluster_words),
            "words": cluster_words,
            "total_mentions": sum(totals_by_bucket.values()),
            "total": [
                {"bucket": b.isoformat(), "count": totals_by_bucket.get(b, 0)}
                for b in all_buckets
            ],
        })

    clusters.sort(key=lambda c: c["total_mentions"], reverse=True)
    return clusters


# ─────────────────────────────────────────────────────────────────────────────
# Geo distribution (nama tempat yang DISEBUT di teks -- BUKAN geotag/lokasi
# asli poster, lihat catatan metodologi di docstring endpoint)
# ─────────────────────────────────────────────────────────────────────────────

# Diverifikasi 2026-07-10: TIDAK ADA field lokasi/geotag di metadata/raw_data
# platform manapun yang sudah kita scrape (Instagram/Facebook/TikTok/Twitter/
# YouTube/News semua dicek langsung ke database, kosong semua) -- jadi
# pendekatan di sini BUKAN "lokasi asli si poster", tapi "nama tempat yang
# DISEBUT di teks post/komentar" (mirip cara kerja tool media-monitoring pada
# umumnya utk topik ekonomi/politik -- peta menunjukkan negara/kota mana yang
# jadi SUBJEK pembicaraan, bukan geolocation audiens).
#
# Daftar BUKAN lengkap, gampang ditambah -- beberapa nama kota Indonesia yang
# JUGA kata umum Bahasa Indonesia (mis. "Malang"=sial, "Medan"=arena/lapangan,
# "Solo"=sendirian dlm konteks tertentu) SENGAJA DIKELUARKAN/diganti nama
# alternatif (mis. "Surakarta" bukan "Solo") supaya tidak banyak
# false-positive. "Padang" tetap dimasukkan meski kadang muncul dlm konteks
# kuliner ("nasi padang") -- risiko diterima, masih cukup terkait geografis.
_GEO_GAZETTEER: dict[str, tuple[float, float]] = {
    # Kota/wilayah Indonesia
    "Jakarta": (-6.2088, 106.8456),
    "Surabaya": (-7.2575, 112.7521),
    "Bandung": (-6.9175, 107.6191),
    "Semarang": (-6.9932, 110.4203),
    "Makassar": (-5.1477, 119.4327),
    "Palembang": (-2.9761, 104.7754),
    "Yogyakarta": (-7.7956, 110.3695),
    "Denpasar": (-8.6705, 115.2126),
    "Bali": (-8.4095, 115.1889),
    "Batam": (1.0456, 104.0305),
    "Bogor": (-6.5971, 106.8060),
    "Bekasi": (-6.2383, 106.9756),
    "Depok": (-6.4025, 106.7942),
    "Tangerang": (-6.1783, 106.6319),
    "Balikpapan": (-1.2379, 116.8529),
    "Manado": (1.4748, 124.8421),
    "Padang": (-0.9471, 100.4172),
    "Pekanbaru": (0.5333, 101.4500),
    "Banjarmasin": (-3.3186, 114.5944),
    "Jayapura": (-2.5337, 140.7181),
    "Pontianak": (-0.0263, 109.3425),
    "Samarinda": (-0.5022, 117.1536),
    "Surakarta": (-7.5755, 110.8243),
    "Aceh": (4.6951, 96.7494),
    "Lampung": (-4.5586, 105.4068),
    "Indonesia": (-2.5, 118.0),
    # Negara/region dunia (relevan utk topik internasional, mis. Piala Dunia)
    "Amerika Serikat": (37.0902, -95.7129),
    "Inggris": (55.3781, -3.4360),
    "Argentina": (-38.4161, -63.6167),
    "Brasil": (-14.2350, -51.9253),
    "Prancis": (46.6034, 1.8883),
    "Belgia": (50.5039, 4.4699),
    "Maroko": (31.7917, -7.0926),
    "Jerman": (51.1657, 10.4515),
    "China": (35.8617, 104.1954),
    "Jepang": (36.2048, 138.2529),
    "Australia": (-25.2744, 133.7751),
    "India": (20.5937, 78.9629),
    "Arab Saudi": (23.8859, 45.0792),
    "Singapura": (1.3521, 103.8198),
    "Malaysia": (4.2105, 101.9758),
    "Qatar": (25.3548, 51.1839),
    "Belanda": (52.1326, 5.2913),
    "Spanyol": (40.4637, -3.7492),
    "Italia": (41.8719, 12.5674),
    "Portugal": (39.3999, -8.2245),
    "Korea Selatan": (35.9078, 127.7669),
    "Rusia": (61.5240, 105.3188),
    "Mesir": (26.8206, 30.8025),
}


@router.get("/geo-distribution", response_model=dict, summary="Distribusi nama tempat yang disebut di post+komentar")
async def get_geo_distribution(
    date_from: date | None = Query(default=None, description="Filter dari tanggal (YYYY-MM-DD). Kosong: date_to - 7 hari (atau dari `hours` kalau date_to juga kosong)"),
    date_to: date | None = Query(default=None, description="Filter sampai tanggal (YYYY-MM-DD), inklusif. Kosong: hari ini"),
    hours: int = Query(default=24, ge=1, le=168, description="Dipakai HANYA kalau date_from & date_to keduanya kosong — rentang jam ke belakang dari sekarang, maks 168 (7 hari)"),
    platform: str | None = Query(default=None, description="Filter opsional ke satu platform saja. Kosong (default): semua platform digabung"),
    min_mentions: int = Query(default=1, ge=1, description="Buang tempat dengan mention di bawah angka ini dari hasil"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Distribusi geografis berdasarkan NAMA TEMPAT yang DISEBUT di teks post
    DAN komentar (dihubungkan lewat `comments.post_id = posts.id`) -- lihat
    catatan metodologi penting di komentar kode `_GEO_GAZETTEER` di atas:
    ini BUKAN geotag/lokasi asli poster (data itu TIDAK ADA di platform
    manapun yang kita scrape, sudah diverifikasi langsung ke database), tapi
    "tempat mana yang jadi SUBJEK pembicaraan" -- cocok utk topik
    ekonomi/politik/berita yang menyebut negara/kota spesifik.

    Independen TOTAL dari endpoint lain di modul ini (tidak reuse
    auto-discover kata/cluster) -- daftar tempat SUDAH ditentukan
    (`_GEO_GAZETTEER`), bukan hasil auto-discovery.

    Return list tempat yang match (mention > 0, difilter `min_mentions`),
    tiap item ada `lat`/`lng` siap dipakai taruh marker di peta.
    """
    if platform and platform not in _VALID_PLATFORMS:
        raise HTTPException(status_code=422, detail=f"platform harus salah satu: {', '.join(sorted(_VALID_PLATFORMS))}")

    now = datetime.now(timezone.utc)
    if date_from or date_to:
        resolved_date_to = date_to or now.date()
        resolved_date_from = date_from or (resolved_date_to - timedelta(days=7))
        since_aligned = datetime.combine(resolved_date_from, time.min, tzinfo=timezone.utc)
        until = datetime.combine(resolved_date_to, time.min, tzinfo=timezone.utc) + timedelta(days=1)
    else:
        until = now
        since_aligned = now - timedelta(hours=hours)

    places = list(_GEO_GAZETTEER.keys())
    params: dict = {"since": since_aligned, "until": until}
    for i, place in enumerate(places):
        params[f"g{i}"] = f"%{place}%"

    select_clauses = [f"count(*) FILTER (WHERE content ILIKE :g{i}) AS c{i}" for i in range(len(places))]

    post_platform_clause = "AND platform = :platform" if platform else ""
    post_params = dict(params)
    if platform:
        post_params["platform"] = platform
    posts_row = (await db.execute(text(f"""
        SELECT {', '.join(select_clauses)}
        FROM posts
        WHERE published_at >= :since AND published_at < :until AND published_at IS NOT NULL
          {post_platform_clause}
    """), post_params)).mappings().first()

    comment_platform_clause = "AND p.platform = :platform" if platform else ""
    comment_select_clauses = [f"count(*) FILTER (WHERE c.content ILIKE :g{i}) AS c{i}" for i in range(len(places))]
    comment_params = dict(params)
    if platform:
        comment_params["platform"] = platform
    comments_row = (await db.execute(text(f"""
        SELECT {', '.join(comment_select_clauses)}
        FROM comments c
        JOIN posts p ON p.id = c.post_id
        WHERE c.published_at >= :since AND c.published_at < :until AND c.published_at IS NOT NULL
          {comment_platform_clause}
    """), comment_params)).mappings().first()

    results = []
    for i, place in enumerate(places):
        post_count = posts_row[f"c{i}"] or 0
        comment_count = comments_row[f"c{i}"] or 0
        total = post_count + comment_count
        if total >= min_mentions:
            lat, lng = _GEO_GAZETTEER[place]
            results.append({
                "place": place,
                "lat": lat,
                "lng": lng,
                "total_mentions": total,
                "from_posts": post_count,
                "from_comments": comment_count,
            })

    results.sort(key=lambda r: r["total_mentions"], reverse=True)

    return build_success_response({
        "date_from": since_aligned.date().isoformat(),
        "date_to": (until - timedelta(seconds=1)).date().isoformat(),
        "platform": platform or "all",
        "total_places_checked": len(places),
        "total_places_matched": len(results),
        "places": results,
    })
