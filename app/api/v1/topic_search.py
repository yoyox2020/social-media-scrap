"""
Universal Topic-Based Search API ("Smart Search").

Topik dan keyword-nya disimpan ke DB sehingga bisa ditampilkan di dashboard.
Setiap topik bisa punya banyak keyword, dan satu keyword bisa masuk banyak topik.

**Alur pencarian (3 tingkat), sama persis dengan pola /posts/search yang
sudah ada di Facebook/Instagram/TikTok/Twitter, cuma di sini lintas SEMUA
platform sekaligus per topik:**
1. Tier-1: cari di DB (`posts.content`/`comments.content` ILIKE, lewat
   app/services/search_topics/tier_search.py) -- BUKAN `Post.keyword_id`,
   karena field itu cuma pernah diisi pipeline YouTube (lihat catatan di
   tier_search.py) dan akan diam-diam melewatkan hampir semua konten
   platform lain kalau dipakai.
2. Tier-2: (opsional, dipakai rescan_service.py utk jadwal berkala -- lihat
   file itu) cek trend_recommendations utk akun yang sudah pernah ketemu.
3. Tier-3: search ke third-party (Apify utk Facebook/Instagram/
   TikTok/Twitter, Firecrawl utk News, YouTube Data API/EnsembleData utk
   YouTube) lewat app/services/search_topics/discovery.py -- TANPA AI/LLM,
   reuse fungsi yang SUDAH ADA & terbukti dipakai endpoint /posts/search
   interaktif tiap platform. **OTOMATIS, TANPA KONFIRMASI** -- kalau data
   tidak ada di DB (tier-1 kosong) DAN `auto_crawl=true` (default), keyword
   itu langsung didaftarkan ke antrian tier-3 (status 'queued'), dibatasi
   `limit_per_keyword` per keyword (keputusan eksplisit user -- konfirmasi
   manual dihapus, biar tier-3 selalu jalan otomatis begitu tier-1 kosong).

   **Tier-3 TIDAK jalan sinkron di request ini** -- Apify/Firecrawl bisa
   15-60+ detik per panggilan, kalau ada beberapa keyword sekaligus gampang
   melebihi timeout browser/reverse-proxy. Endpoint cuma DAFTARKAN keyword
   yang perlu dicari ke antrian Celery
   (workers.search_topics.process_confirmed_queue, lihat queue_service.py)
   lalu LANGSUNG balas status 'queued' -- proses sebenarnya jalan di
   background SATU KEYWORD PER SATU KEYWORD berurutan. Cek hasilnya
   belakangan lewat GET /search/topics/{id} (posts baru otomatis kehitung
   begitu tersimpan, tidak perlu endpoint status baru).

**Pencarian berkala (opsional):** `enable_recurring=true` + `schedule_duration_days`
menjadwalkan topik utk di-scan ulang tiap hari (Celery task
workers.search_topics.daily_rescan, lihat rescan_service.py) selama N hari
dari SEKARANG (bukan dari created_at topik) -- bisa diaktifkan/diubah
durasinya kapan saja lewat POST /search/topics/{id}/schedule TANPA perlu
search ulang dari awal.

**Hapus topik TIDAK menghapus data.** DELETE /search/topics/{id} cuma
soft-delete (`is_active=False`) -- keyword & post/comment yang sudah
ditemukan tetap tersimpan permanen, dan otomatis berhenti diambil jadwal
berkala (task harian filter `is_active==True`).

**Platform kosong = SEMUA platform.** Field `platforms` di POST /search/topics
kalau tidak dikirim/kosong otomatis diisi SEMUA platform terdaftar (`_resolve_platforms()`),
BUKAN cuma youtube seperti sebelumnya -- keputusan eksplisit user, cocok
utk form 'buat topik' yang tidak punya selector platform sama sekali.

**Cari ulang topik tersimpan:** POST /search/topics/{id}/search -- utk UI
'pilih topik dari dropdown, klik Search' yang cuma tahu topic_id (tidak
perlu kirim ulang name+keywords+platforms seperti POST /search/topics).
Alur konfirmasi tier-3 SAMA PERSIS.
"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.keywords.models import Keyword
from app.domain.search_topics.models import SearchTopic, SearchTopicKeyword
from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.infrastructure.logging.logger import get_logger
from app.services.auth.dependencies import get_current_user
from app.services.search_topics import discovery, tier_search
from app.shared.utils import build_success_response

router = APIRouter(prefix="/search", tags=["topic-search"])
logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# SCHEMAS
# ─────────────────────────────────────────────────────────────────────────────

class TopicItem(BaseModel):
    name: str = Field(..., description="Nama topik, contoh: 'jawa timur'")
    keywords: list[str] = Field(..., min_length=1, description="Kata kunci terkait topik ini")
    description: str | None = Field(default=None)


class TopicSearchRequest(BaseModel):
    topics: list[TopicItem] = Field(..., min_length=1)
    platforms: list[str] = Field(default_factory=list, description="Platform: youtube, instagram, facebook, tiktok, twitter, news. KOSONG/tidak dikirim = otomatis SEMUA platform terdaftar.")
    limit_per_keyword: int = Field(default=10, ge=1, le=100)
    include_sentiment: bool = Field(default=True)
    include_comments: bool = Field(default=False)
    auto_crawl: bool = Field(default=True, description="Izinkan pencarian ke third-party (tier-3) utk topik ini kalau data belum ada di DB -- begitu tier-1 kosong, langsung diantrekan ke background, TANPA konfirmasi tambahan. Set false kalau cuma mau simpan definisi topik / cari di DB saja.")
    scheduled_hour: int | None = Field(default=None, ge=0, le=23, description="TIDAK DIPAKAI -- field lama, dibiarkan apa adanya. Lihat enable_recurring.")
    save_topic: bool = Field(default=True, description="Simpan konfigurasi topik ke DB untuk dashboard")
    enable_recurring: bool = Field(default=False, description="Jadwalkan pencarian berkala harian utk topik ini")
    schedule_duration_days: int = Field(default=7, ge=1, le=90, description="Berapa hari jadwal berkala berjalan, dihitung dari SEKARANG")


class TopicScheduleRequest(BaseModel):
    enabled: bool = Field(..., description="Aktif/nonaktifkan pencarian berkala")
    duration_days: int | None = Field(default=None, ge=1, le=90, description="Ubah durasi (hari), dihitung ulang dari SEKARANG. Kosong = pakai durasi yang sudah ada / default 7")


class SavedTopicSearchRequest(BaseModel):
    """Body utk POST /search/topics/{topic_id}/search -- cari ulang topik yang
    SUDAH tersimpan pakai keyword/platform yang sudah di-set saat topik
    dibuat, TANPA perlu kirim ulang name+keywords+platforms (beda dengan
    POST /search/topics yang butuh payload penuh). Cocok utk UI dropdown
    'pilih topik tersimpan' + tombol Search."""
    limit_per_keyword: int = Field(default=10, ge=1, le=100)
    include_sentiment: bool = Field(default=True)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

async def _find_keyword(db: AsyncSession, q: str) -> Keyword | None:
    q_clean = q.strip().lower()
    kw = await db.scalar(select(Keyword).where(func.lower(Keyword.keyword) == q_clean).limit(1))
    if kw:
        return kw
    kw = await db.scalar(select(Keyword).where(func.lower(Keyword.keyword).like(f"%{q_clean}%")).limit(1))
    if kw:
        return kw
    words = q_clean.split()
    if len(words) > 1:
        from sqlalchemy import and_
        conditions = [func.lower(Keyword.keyword).contains(w) for w in words]
        kw = await db.scalar(select(Keyword).where(and_(*conditions)).limit(1))
    return kw


async def _get_or_create_keyword(db: AsyncSession, keyword_text: str) -> Keyword | None:
    """`SearchTopicKeyword.keyword_id` wajib diisi (FK, bagian primary key) --
    jadi tetap butuh baris `Keyword` NYATA per topic-keyword, WALAU
    pencarian isinya sendiri sekarang pakai ILIKE (tier_search.py), bukan
    `keyword_id`. Reuse baris yang sudah ada kalau cocok (`_find_keyword`),
    baru bikin baru kalau genuinely belum ada."""
    existing = await _find_keyword(db, keyword_text)
    if existing:
        return existing

    from app.domain.projects.models import Project
    project = await db.scalar(select(Project).limit(1))
    if not project:
        return None

    kw = Keyword(project_id=project.id, keyword=keyword_text, is_active=True)
    db.add(kw)
    await db.flush()
    await db.refresh(kw)
    return kw


def _resolve_schedule_fields(enable_recurring: bool, duration_days: int | None) -> dict:
    """Hitung schedule_started_at/schedule_expires_at SEKALI saat recurring
    di-(re)aktifkan -- durasi dihitung dari SEKARANG, bukan dari created_at
    topik, supaya "aktifkan tracking hari ini utk 7 hari" selalu berarti
    7 hari dari hari ini walau topik-nya sudah lama ada."""
    if not enable_recurring:
        return {
            "schedule_recurring": False,
            "schedule_duration_days": None,
            "schedule_started_at": None,
            "schedule_expires_at": None,
        }
    now = datetime.now(timezone.utc)
    days = duration_days or 7
    return {
        "schedule_recurring": True,
        "schedule_duration_days": days,
        "schedule_started_at": now,
        "schedule_expires_at": now + timedelta(days=days),
    }


def _resolve_platforms(platforms: list[str]) -> list[str]:
    """Kosong/tidak dikirim = otomatis SEMUA platform terdaftar (keputusan
    user eksplisit) -- dulu default cuma youtube, banyak topik lawas kena
    default itu diam-diam padahal maksudnya lintas semua platform."""
    if platforms:
        return platforms
    return sorted(discovery.ALL_SMART_SEARCH_PLATFORMS)


async def _search_keyword_tiered(
    db: AsyncSession,
    kw_text: str,
    platforms: list[str],
    limit_per_keyword: int,
    include_sentiment: bool,
    auto_crawl: bool,
) -> dict:
    """Tier-1 (DB) -> kalau kosong & auto_crawl=true, langsung tandai utk
    antrian tier-3 (TANPA konfirmasi tambahan -- keputusan eksplisit user).
    Dipakai search_by_topics() (topik baru/existing lewat nama) DAN
    search_saved_topic() (topik tersimpan lewat topic_id) -- logic-nya
    identik, cuma beda dari mana kw_text/platforms berasal.

    TIDAK memanggil third-party APAPUN di sini -- cuma menyusun `queue_items`
    (dict siap dipakai Celery task workers.search_topics.process_confirmed_queue),
    dibatasi `limit_per_keyword`. Pemanggil yang gabungkan queue_items dari
    semua keyword lalu dispatch SATU task di background -- supaya request
    HTTP tidak menunggu Apify/Firecrawl (15-60+ detik per panggilan), lihat
    app/services/search_topics/queue_service.py."""
    kw_result: dict = {"keyword": kw_text, "status": "not_found", "total": 0, "posts": [], "queue_items": []}

    posts = await tier_search.find_posts_by_keyword(db, kw_text, platforms, limit_per_keyword)
    total = len(posts)
    kw_result.update({"status": "found" if total > 0 else "empty", "total": total, "posts": posts})

    if include_sentiment and total > 0:
        kw_result["sentiment"] = await tier_search.get_sentiment_summary_by_keyword(db, kw_text, platforms)

    if total == 0 and auto_crawl:
        kw_result["status"] = "queued"
        kw_result["queue_items"] = [
            {
                "keyword_text": kw_text,
                "platform": platform,
                "source_tag": f"smart_search_{platform}" if platform in discovery.ACCOUNT_DISCOVERY_PLATFORMS else None,
                "limit": limit_per_keyword,
            }
            for platform in platforms if platform in discovery.ALL_SMART_SEARCH_PLATFORMS
        ]

    return kw_result


async def _save_topic(
    db: AsyncSession,
    topic_name: str,
    description: str | None,
    keyword_objects: list[tuple[str, Keyword | None]],
    platforms: list[str],
    scheduled_hour: int | None,
    auto_crawl: bool,
    enable_recurring: bool,
    schedule_duration_days: int,
) -> SearchTopic:
    """Simpan atau update topik ke DB. Jika nama sudah ada, update keyword-nya."""
    from sqlalchemy.orm import selectinload
    existing = await db.scalar(
        select(SearchTopic)
        .options(selectinload(SearchTopic.topic_keywords))
        .where(func.lower(SearchTopic.name) == topic_name.strip().lower()).limit(1)
    )

    schedule_fields = _resolve_schedule_fields(enable_recurring, schedule_duration_days)

    if existing:
        existing.platforms = platforms
        existing.scheduled_hour = scheduled_hour
        existing.auto_crawl = auto_crawl
        existing.updated_at = datetime.now(timezone.utc)
        if enable_recurring:
            # Cuma timpa jadwal kalau request ini MEMANG mengaktifkan recurring --
            # kalau enable_recurring=False di request ini, jangan matikan jadwal
            # yang sudah aktif dari request sebelumnya secara tidak sengaja.
            for k, v in schedule_fields.items():
                setattr(existing, k, v)
        topic = existing
    else:
        topic = SearchTopic(
            name=topic_name.strip().title(),
            description=description,
            platforms=platforms,
            scheduled_hour=scheduled_hour,
            auto_crawl=auto_crawl,
            **schedule_fields,
        )
        db.add(topic)
        await db.flush()

    existing_kw_ids = {stk.keyword_id for stk in topic.topic_keywords}
    for kw_text, kw_obj in keyword_objects:
        if kw_obj and kw_obj.id not in existing_kw_ids:
            link = SearchTopicKeyword(topic_id=topic.id, keyword_id=kw_obj.id, keyword_text=kw_text)
            db.add(link)

    return topic


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT: Cari + Simpan Topik
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/topics", response_model=dict)
async def search_by_topics(
    body: TopicSearchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Cari data berdasarkan topik + kata kunci, dikelompokkan per topik.
    Jika `save_topic=true` (default), topik dan keyword-nya disimpan ke DB untuk dashboard.

    **Alur (tier-1 -> tier-3, lihat docstring modul):**
    - Cari setiap keyword di `posts`/`comments` (ILIKE, lintas SEMUA platform diminta)
    - Jika ada data → kembalikan posts + sentimen (status "found")
    - Jika belum ada + auto_crawl=true (default) → keyword LANGSUNG masuk
      ANTRIAN background (Celery, status "queued"), TANPA konfirmasi
      tambahan, dibatasi `limit_per_keyword`. Diproses SATU PER SATU
      berurutan (bukan sinkron di request ini -- Apify/Firecrawl bisa
      15-60+ detik per panggilan, lihat queue_service.py). Cek hasil lewat
      GET /search/topics/{id} atau /list setelah beberapa saat.
    - Topik disimpan ke DB → tampil di `GET /search/topics/list`
    """
    logger.info("topic_search", topics=[t.name for t in body.topics], user=str(current_user.id))

    platforms = _resolve_platforms(body.platforms)
    topic_results = []
    queued_keywords = []

    for topic in body.topics:
        keyword_results = []
        topic_total_posts = 0
        keyword_objects: list[tuple[str, Keyword | None]] = []
        topic_queue_items: list[dict] = []

        for kw_text in topic.keywords:
            keyword = await _get_or_create_keyword(db, kw_text)

            kw_result = await _search_keyword_tiered(
                db, kw_text, platforms, body.limit_per_keyword, body.include_sentiment,
                body.auto_crawl,
            )
            kw_result["keyword_id"] = str(keyword.id) if keyword else None
            topic_total_posts += kw_result["total"]

            if kw_result["status"] == "queued":
                queued_keywords.append(kw_text)
                topic_queue_items.extend(kw_result["queue_items"])

            keyword_objects.append((kw_text, keyword))
            keyword_results.append(kw_result)

        if body.save_topic:
            saved_topic = await _save_topic(
                db=db,
                topic_name=topic.name,
                description=topic.description,
                keyword_objects=keyword_objects,
                platforms=platforms,
                scheduled_hour=body.scheduled_hour,
                auto_crawl=body.auto_crawl,
                enable_recurring=body.enable_recurring,
                schedule_duration_days=body.schedule_duration_days,
            )
            topic_id = str(saved_topic.id)
        else:
            topic_id = None

        if topic_queue_items:
            await db.commit()  # pastikan topik/keyword ke-commit dulu sebelum task background jalan
            from app.workers.search_topics_worker import process_confirmed_search_queue_task
            # TIDAK pakai queue="default" -- bukan nama antrian nyata yang
            # dikonsumsi worker manapun (lihat social_intel_worker/-ai's
            # --queues=..., tidak ada "default" di situ). Biarkan tanpa
            # argumen queue supaya masuk antrian default Celery sendiri
            # ("celery"), yang MEMANG dikonsumsi semua worker container.
            process_confirmed_search_queue_task.apply_async(
                kwargs={"items": topic_queue_items, "topic_id": topic_id},
            )

        topic_results.append({
            "topic_id": topic_id,
            "topic": topic.name.title(),
            "keywords": topic.keywords,
            "total_posts": topic_total_posts,
            "status_per_keyword": {kd["keyword"]: kd["status"] for kd in keyword_results},
            "sentiment_per_keyword": {
                kd["keyword"]: kd.get("sentiment")
                for kd in keyword_results if kd.get("sentiment")
            },
            "results": [p for kd in keyword_results for p in kd.get("posts", [])],
            "queued": [kd["keyword"] for kd in keyword_results if kd["status"] == "queued"],
        })

    await db.commit()

    has_data = any(t["total_posts"] > 0 for t in topic_results)
    if queued_keywords:
        overall = "partial" if has_data else "queued"
    else:
        overall = "ready"

    note = None
    if queued_keywords:
        note = (
            "Keyword dengan status 'queued' sedang dicari ke third-party SATU PER SATU di background "
            "(Apify/Firecrawl/YouTube API). Cek lagi lewat GET /search/topics/{topic_id} setelah beberapa saat."
        )

    return build_success_response({
        "status": overall,
        "platforms": platforms,
        "total_topics": len(topic_results),
        "queued_keywords": queued_keywords,
        "note": note,
        "topics": topic_results,
    })


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT: List Semua Topik (Dashboard)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/topics/list", response_model=dict)
async def list_saved_topics(
    is_active: bool = Query(default=True, description="Filter topik aktif saja"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Daftar semua topik yang tersimpan di DB — untuk ditampilkan di dashboard.
    Setiap topik menampilkan keyword-keyword yang terkait beserta statistik singkat.
    """
    from sqlalchemy.orm import selectinload
    q = select(SearchTopic).options(selectinload(SearchTopic.topic_keywords))
    if is_active:
        q = q.where(SearchTopic.is_active == True)
    q = q.order_by(SearchTopic.created_at.desc()).offset(offset).limit(limit)

    topics = (await db.scalars(q)).all()
    total_count = await db.scalar(select(func.count(SearchTopic.id)).where(SearchTopic.is_active == is_active))

    items = []
    for topic in topics:
        total_posts = 0
        total_comments = 0
        for stk in topic.topic_keywords:
            p, c = await tier_search.count_posts_and_comments_by_keyword(db, stk.keyword_text, topic.platforms)
            total_posts += p
            total_comments += c

        items.append({
            "topic_id": str(topic.id),
            "name": topic.name,
            "description": topic.description,
            "platforms": topic.platforms,
            "keywords": [stk.keyword_text for stk in topic.topic_keywords],
            "total_keywords": len(topic.topic_keywords),
            "total_posts": total_posts,
            "total_comments": total_comments,
            "auto_crawl": topic.auto_crawl,
            "is_active": topic.is_active,
            "schedule_recurring": topic.schedule_recurring,
            "schedule_duration_days": topic.schedule_duration_days,
            "schedule_expires_at": topic.schedule_expires_at.isoformat() if topic.schedule_expires_at else None,
            "last_ai_discovery_at": topic.last_ai_discovery_at.isoformat() if topic.last_ai_discovery_at else None,
            "created_at": topic.created_at.isoformat(),
            "updated_at": topic.updated_at.isoformat(),
        })

    return build_success_response({
        "total": total_count,
        "offset": offset,
        "items": items,
    })


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT: Semua Keyword yang Pernah Dicari (lintas semua topik+platform)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/topics/keywords", response_model=dict)
async def list_all_searched_keywords(
    limit: int = Query(default=50, ge=1, le=200, description="Maks jumlah keyword ditampilkan"),
    offset: int = Query(default=0, ge=0),
    limit_per_keyword: int = Query(default=10, ge=1, le=100, description="Maks sample post per keyword"),
    include_sentiment: bool = Query(default=True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Semua keyword yang PERNAH dimasukkan user lewat topik manapun yang masih
    aktif, digabung jadi SATU daftar rata -- TIDAK perlu topic_id/keyword_id/
    platform apa pun sbg filter. Tiap keyword otomatis dicari lintas SEMUA
    platform sekaligus (`platforms=None` di tier_search.py berarti tanpa
    filter platform, bukan platform kosong berarti tidak ada hasil).

    Kalau keyword yang sama (case-insensitive) dipakai di lebih dari satu
    topik (mis. "korupsi" ada di topik "Riset KPU" dan "Riset Hukum"), cuma
    tampil SEKALI di sini -- field `topics` menunjukkan semua topik yang
    memakainya.
    """
    from sqlalchemy.orm import selectinload
    topics = (await db.scalars(
        select(SearchTopic)
        .options(selectinload(SearchTopic.topic_keywords))
        .where(SearchTopic.is_active == True)
    )).all()

    dedup: dict[str, dict] = {}
    for topic in topics:
        for stk in topic.topic_keywords:
            key = stk.keyword_text.strip().lower()
            entry = dedup.setdefault(key, {"keyword": stk.keyword_text, "topics": [], "last_rescanned_at": None})
            entry["topics"].append(topic.name)
            if stk.last_rescanned_at and (
                entry["last_rescanned_at"] is None or stk.last_rescanned_at > entry["last_rescanned_at"]
            ):
                entry["last_rescanned_at"] = stk.last_rescanned_at

    all_keywords = sorted(dedup.values(), key=lambda e: e["keyword"].lower())
    total_keywords = len(all_keywords)
    page = all_keywords[offset: offset + limit]

    items = []
    for entry in page:
        kw_text = entry["keyword"]
        total_posts, total_comments = await tier_search.count_posts_and_comments_by_keyword(db, kw_text, None)
        posts = await tier_search.find_posts_by_keyword(db, kw_text, None, limit_per_keyword)
        item = {
            "keyword": kw_text,
            "topics": entry["topics"],
            "total_posts": total_posts,
            "total_comments": total_comments,
            "platforms_found": sorted({p["platform"] for p in posts}),
            "results": posts,
            "last_rescanned_at": entry["last_rescanned_at"].isoformat() if entry["last_rescanned_at"] else None,
        }
        if include_sentiment and posts:
            item["sentiment"] = await tier_search.get_sentiment_summary_by_keyword(db, kw_text, None)
        items.append(item)

    return build_success_response({
        "total_keywords": total_keywords,
        "offset": offset,
        "limit": limit,
        "keywords": items,
    })


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT: Status AI-Context Discovery ("Subsistem A2")
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/topics/ai-discovery/status", response_model=dict)
async def get_ai_discovery_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Status run TERAKHIR AI-context discovery -- AI dipandu topik+keyword
    SearchTopic recurring sbg konteks, cari perkembangan/sub-topik BARU
    terkait (lihat app/services/search_topics/ai_discovery_service.py).
    Setara get_viral_discovery_trace() (Subsistem A) tapi per-topik, untuk
    Smart Search sendiri (Subsistem A2).
    """
    from app.services.search_topics.ai_discovery_service import (
        TARGET_PLATFORMS,
        get_search_topic_ai_discovery_trace,
    )
    from app.shared.config import settings

    trace = await get_search_topic_ai_discovery_trace(db)

    return build_success_response({
        "config": {
            "provider": settings.ai_discovery_provider,
            "schedule": (
                f"{settings.smart_search_ai_discovery_schedule_hour:02d}:"
                f"{settings.smart_search_ai_discovery_schedule_minute:02d} WIB otomatis (Celery Beat)"
            ),
            "max_topics_per_run": settings.smart_search_ai_discovery_max_topics_per_run,
            "max_subtopics_per_topic": settings.smart_search_ai_discovery_max_subtopics_per_topic,
            "target_platforms": sorted(TARGET_PLATFORMS),
        },
        **trace,
    })


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT: Detail Satu Topik
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/topics/{topic_id}", response_model=dict)
async def get_topic_detail(
    topic_id: uuid.UUID,
    limit_per_keyword: int = Query(default=10, ge=1, le=100),
    include_sentiment: bool = Query(default=True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Detail satu topik: semua keyword + data posts + sentimen + riwayat
    AI-context discovery (kalau topik ini pernah/sedang dipantau berkesinambungan
    oleh Subsistem A2, lihat app/services/search_topics/ai_discovery_service.py).
    Dipanggil saat user klik topik di dashboard.
    """
    from sqlalchemy.orm import selectinload

    from app.services.search_topics.ai_discovery_service import get_topic_ai_discovery_history

    topic = await db.scalar(
        select(SearchTopic)
        .options(selectinload(SearchTopic.topic_keywords))
        .where(SearchTopic.id == topic_id)
    )
    if not topic:
        from app.shared.exceptions import NotFoundError
        raise NotFoundError(f"Topik {topic_id} tidak ditemukan")

    keyword_details = []
    for stk in topic.topic_keywords:
        posts = await tier_search.find_posts_by_keyword(db, stk.keyword_text, topic.platforms, limit_per_keyword)
        detail: dict = {
            "keyword": stk.keyword_text,
            "keyword_id": str(stk.keyword_id),
            "total_posts": len(posts),
            "posts": posts,
            "last_rescanned_at": stk.last_rescanned_at.isoformat() if stk.last_rescanned_at else None,
        }
        if include_sentiment and posts:
            detail["sentiment"] = await tier_search.get_sentiment_summary_by_keyword(db, stk.keyword_text, topic.platforms)

        keyword_details.append(detail)

    # Tidak digate ke schedule_recurring -- kalau recurring-nya SUDAH dimatikan
    # tapi topik ini PERNAH dipantau AI discovery sebelumnya, riwayatnya tetap
    # relevan ditampilkan (bukan cuma status "sedang aktif" sekarang).
    ai_discovery_history = await get_topic_ai_discovery_history(db, topic.name)

    return build_success_response({
        "topic_id": str(topic.id),
        "name": topic.name,
        "description": topic.description,
        "platforms": topic.platforms,
        "total_keywords": len(keyword_details),
        "total_posts": sum(k["total_posts"] for k in keyword_details),
        "keyword_details": keyword_details,
        "auto_crawl": topic.auto_crawl,
        "schedule_recurring": topic.schedule_recurring,
        "schedule_duration_days": topic.schedule_duration_days,
        "schedule_started_at": topic.schedule_started_at.isoformat() if topic.schedule_started_at else None,
        "schedule_expires_at": topic.schedule_expires_at.isoformat() if topic.schedule_expires_at else None,
        "last_ai_discovery_at": topic.last_ai_discovery_at.isoformat() if topic.last_ai_discovery_at else None,
        "ai_discovery_history": ai_discovery_history,
        "created_at": topic.created_at.isoformat(),
        "updated_at": topic.updated_at.isoformat(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT: Cari Ulang Topik Tersimpan (by ID)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/topics/{topic_id}/search", response_model=dict)
async def search_saved_topic(
    topic_id: uuid.UUID,
    body: SavedTopicSearchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Cari ulang SATU topik yang sudah tersimpan, pakai keyword+platform yang
    sudah di-set saat topik dibuat -- cukup kirim topic_id, TIDAK perlu kirim
    ulang name+keywords+platforms (beda dengan POST /search/topics). Cocok
    utk UI 'pilih topik dari dropdown lalu klik Search'.

    Alur SAMA PERSIS dengan POST /search/topics (tier-1 -> tier-3 otomatis
    kalau kosong, TANPA konfirmasi, lihat docstring modul) -- cuma di sini
    scope-nya SATU topik yang sudah ada, bukan bikin/update topik baru.
    """
    from sqlalchemy.orm import selectinload
    topic = await db.scalar(
        select(SearchTopic)
        .options(selectinload(SearchTopic.topic_keywords))
        .where(SearchTopic.id == topic_id, SearchTopic.is_active == True)
    )
    if not topic:
        from app.shared.exceptions import NotFoundError
        raise NotFoundError(f"Topik {topic_id} tidak ditemukan atau sudah dinonaktifkan")

    platforms = _resolve_platforms(topic.platforms)
    keyword_results = []
    queued_keywords = []
    queue_items: list[dict] = []

    for stk in topic.topic_keywords:
        # SENGAJA tidak pakai topic.auto_crawl di sini (beda dgn search_by_topics())
        # -- itu nilai yang DI-PERSIST saat topik dibuat/disimpan, kalau kebetulan
        # ke-set false (mis. sengaja auto_crawl=false saat 'Simpan Topik' krn
        # cuma mau simpan definisi, bukan cari), topik itu akan PERMANEN tidak
        # pernah bisa ditawari tier-3 lewat endpoint ini lagi -- tidak ada UI
        # utk toggle auto_crawl balik (beda dgn schedule yang punya endpoint
        # sendiri). Endpoint by-id ini selalu izinkan tier-3 otomatis kalau
        # tier-1 kosong, terlepas dari auto_crawl yang tersimpan.
        kw_result = await _search_keyword_tiered(
            db, stk.keyword_text, platforms, body.limit_per_keyword, body.include_sentiment,
            True,
        )
        kw_result["keyword_id"] = str(stk.keyword_id)

        if kw_result["status"] == "queued":
            queued_keywords.append(stk.keyword_text)
            queue_items.extend(kw_result["queue_items"])

        keyword_results.append(kw_result)

    await db.commit()

    if queue_items:
        # last_rescanned_at per keyword di-set di dalam Celery task saat
        # item itu MULAI diproses (lihat queue_service.py), bukan di sini --
        # supaya "sedang diproses" & "selesai diproses" sama2 tercermin walau
        # task masih jalan lama di background.
        from app.workers.search_topics_worker import process_confirmed_search_queue_task
        # TIDAK pakai queue="default" -- lihat catatan di search_by_topics().
        process_confirmed_search_queue_task.apply_async(
            kwargs={"items": queue_items, "topic_id": str(topic.id)},
        )

    total_posts = sum(kd["total"] for kd in keyword_results)
    has_data = total_posts > 0
    if queued_keywords:
        status = "partial" if has_data else "queued"
    else:
        status = "ready"

    note = None
    if queued_keywords:
        note = (
            "Keyword dengan status 'queued' sedang dicari ke third-party SATU PER SATU di background "
            "(Apify/Firecrawl/YouTube API). Cek lagi lewat GET /search/topics/{topic_id} setelah beberapa saat."
        )

    return build_success_response({
        "topic_id": str(topic.id),
        "topic": topic.name,
        "platforms": platforms,
        "status": status,
        "total_posts": total_posts,
        "status_per_keyword": {kd["keyword"]: kd["status"] for kd in keyword_results},
        "sentiment_per_keyword": {
            kd["keyword"]: kd.get("sentiment") for kd in keyword_results if kd.get("sentiment")
        },
        "results": [p for kd in keyword_results for p in kd.get("posts", [])],
        "queued_keywords": queued_keywords,
        "note": note,
    })


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT: Atur Jadwal Pencarian Berkala
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/topics/{topic_id}/schedule", response_model=dict)
async def set_topic_schedule(
    topic_id: uuid.UUID,
    body: TopicScheduleRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Aktifkan/nonaktifkan atau ubah durasi pencarian berkala TANPA perlu
    search ulang dari awal. Durasi selalu dihitung dari SEKARANG (saat
    endpoint ini dipanggil), bukan dari kapan topik pertama kali dibuat.
    """
    topic = await db.scalar(select(SearchTopic).where(SearchTopic.id == topic_id))
    if not topic:
        from app.shared.exceptions import NotFoundError
        raise NotFoundError(f"Topik {topic_id} tidak ditemukan")

    schedule_fields = _resolve_schedule_fields(
        body.enabled,
        body.duration_days or topic.schedule_duration_days,
    )
    for k, v in schedule_fields.items():
        setattr(topic, k, v)
    topic.updated_at = datetime.now(timezone.utc)
    await db.commit()

    return build_success_response({
        "topic_id": str(topic.id),
        "name": topic.name,
        "schedule_recurring": topic.schedule_recurring,
        "schedule_duration_days": topic.schedule_duration_days,
        "schedule_started_at": topic.schedule_started_at.isoformat() if topic.schedule_started_at else None,
        "schedule_expires_at": topic.schedule_expires_at.isoformat() if topic.schedule_expires_at else None,
    })


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT: Hapus / Nonaktifkan Topik
# ─────────────────────────────────────────────────────────────────────────────

@router.delete("/topics/{topic_id}", response_model=dict)
async def delete_topic(
    topic_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Nonaktifkan topik (soft delete — data tidak hilang). Otomatis
    berhenti diambil jadwal pencarian berkala (task harian filter
    is_active==True) -- tidak perlu langkah tambahan apa pun."""
    topic = await db.scalar(
        select(SearchTopic).where(SearchTopic.id == topic_id)
    )
    if not topic:
        from app.shared.exceptions import NotFoundError
        raise NotFoundError(f"Topik {topic_id} tidak ditemukan")

    topic.is_active = False
    topic.updated_at = datetime.now(timezone.utc)
    await db.commit()

    return build_success_response({"message": f"Topik '{topic.name}' dinonaktifkan"})
