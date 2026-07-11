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
3. Tier-3: search LANGSUNG ke third-party (Apify utk Facebook/Instagram/
   TikTok/Twitter, Firecrawl utk News, YouTube Data API/EnsembleData utk
   YouTube) lewat app/services/search_topics/discovery.py -- TANPA AI/LLM,
   reuse fungsi yang SUDAH ADA & terbukti dipakai endpoint /posts/search
   interaktif tiap platform. **BUTUH KONFIRMASI EKSPLISIT** (lihat
   `confirm_third_party` di bawah) -- kalau data tidak ada di DB, endpoint
   TIDAK langsung crawl, cuma melapor status 'needs_confirmation' dulu.
   Ini HANYA berlaku utk request interaktif lewat endpoint ini; pemindaian
   berkala (`schedule_recurring=true`, lihat rescan_service.py) TETAP jalan
   otomatis tanpa konfirmasi ulang tiap hari -- user sudah memberi izin di
   muka saat mengaktifkan `enable_recurring=true`.

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
    auto_crawl: bool = Field(default=True, description="Izinkan pencarian ke third-party (tier-3) utk topik ini kalau data belum ada -- tetap butuh confirm_third_party=true di request yang sama utk BENAR-BENAR jalan, lihat confirm_third_party")
    confirm_third_party: bool = Field(default=False, description="WAJIB true baru tier-3 (Apify/Firecrawl/YouTube API) benar-benar dipanggil. Kalau false (default) & data tidak ketemu di DB, endpoint cuma melapor status 'needs_confirmation' TANPA memanggil third-party apa pun -- kirim ulang request yang SAMA (topics+platforms sama persis) dengan confirm_third_party=true setelah user/frontend setuju utk lanjut.")
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
    confirm_third_party: bool = Field(default=False, description="Sama seperti di POST /search/topics -- wajib true baru tier-3 benar-benar dipanggil.")


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
    confirm_third_party: bool,
) -> dict:
    """Tier-1 (DB) -> tier-3 (third-party, HANYA kalau confirm_third_party=true)
    utk SATU keyword. Dipakai search_by_topics() (topik baru/existing lewat
    nama) DAN search_saved_topic() (topik tersimpan lewat topic_id) --
    logic-nya identik, cuma beda dari mana kw_text/platforms berasal."""
    kw_result: dict = {"keyword": kw_text, "status": "not_found", "total": 0, "posts": []}

    posts = await tier_search.find_posts_by_keyword(db, kw_text, platforms, limit_per_keyword)
    total = len(posts)
    kw_result.update({"status": "found" if total > 0 else "empty", "total": total, "posts": posts})

    if include_sentiment and total > 0:
        kw_result["sentiment"] = await tier_search.get_sentiment_summary_by_keyword(db, kw_text, platforms)

    if total == 0 and auto_crawl and not confirm_third_party:
        kw_result["status"] = "needs_confirmation"
        kw_result["confirmation_message"] = (
            f"Data '{kw_text}' tidak ditemukan di database. Cari ke third-party "
            f"({', '.join(platforms)})? Kirim ulang dengan confirm_third_party=true untuk melanjutkan."
        )
    elif total == 0 and auto_crawl and confirm_third_party:
        crawl_results = {}
        for platform in platforms:
            if platform not in discovery.ALL_SMART_SEARCH_PLATFORMS:
                crawl_results[platform] = {"error": f"platform '{platform}' tidak didukung"}
                continue
            source_tag = (
                f"smart_search_{platform}" if platform in discovery.ACCOUNT_DISCOVERY_PLATFORMS else None
            )
            crawl_results[platform] = await discovery.run_tier3_discovery(
                db, platform, kw_text, max_results=limit_per_keyword, source_tag=source_tag,
            )
        kw_result["crawl"] = crawl_results
        kw_result["status"] = "crawling"

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
    - Jika belum ada + auto_crawl=true + confirm_third_party=false (default)
      → status "needs_confirmation", TIDAK memanggil third-party apa pun
    - Jika belum ada + auto_crawl=true + confirm_third_party=true → search
      LANGSUNG ke third-party tiap platform (Apify/Firecrawl/YouTube API,
      TANPA AI/LLM), status "crawling"
    - Topik disimpan ke DB → tampil di `GET /search/topics/list`
    """
    logger.info("topic_search", topics=[t.name for t in body.topics], user=str(current_user.id))

    platforms = _resolve_platforms(body.platforms)
    topic_results = []
    crawling_keywords = []
    needs_confirmation_keywords = []

    for topic in body.topics:
        keyword_results = []
        topic_total_posts = 0
        keyword_objects: list[tuple[str, Keyword | None]] = []

        for kw_text in topic.keywords:
            keyword = await _get_or_create_keyword(db, kw_text)

            kw_result = await _search_keyword_tiered(
                db, kw_text, platforms, body.limit_per_keyword, body.include_sentiment,
                body.auto_crawl, body.confirm_third_party,
            )
            kw_result["keyword_id"] = str(keyword.id) if keyword else None
            topic_total_posts += kw_result["total"]

            if kw_result["status"] == "needs_confirmation":
                needs_confirmation_keywords.append(kw_text)
            elif kw_result["status"] == "crawling":
                crawling_keywords.append(kw_text)

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
            "crawling": [kd["keyword"] for kd in keyword_results if kd["status"] == "crawling"],
            "needs_confirmation": [kd["keyword"] for kd in keyword_results if kd["status"] == "needs_confirmation"],
        })

    await db.commit()

    has_data = any(t["total_posts"] > 0 for t in topic_results)
    if crawling_keywords:
        overall = "partial" if has_data else "crawling"
    elif needs_confirmation_keywords:
        overall = "partial_needs_confirmation" if has_data else "needs_confirmation"
    else:
        overall = "ready"

    note = None
    if crawling_keywords:
        note = "Keyword dengan status 'crawling' baru saja dicari ke third-party (Apify/Firecrawl/YouTube API)."
    elif needs_confirmation_keywords:
        note = (
            "Keyword dengan status 'needs_confirmation' tidak ditemukan di database. "
            "Kirim ulang request yang SAMA dengan confirm_third_party=true untuk mencari ke third-party."
        )

    return build_success_response({
        "status": overall,
        "platforms": platforms,
        "total_topics": len(topic_results),
        "crawling_keywords": crawling_keywords,
        "needs_confirmation_keywords": needs_confirmation_keywords,
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
            "created_at": topic.created_at.isoformat(),
            "updated_at": topic.updated_at.isoformat(),
        })

    return build_success_response({
        "total": total_count,
        "offset": offset,
        "items": items,
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
    Detail satu topik: semua keyword + data posts + sentimen.
    Dipanggil saat user klik topik di dashboard.
    """
    from sqlalchemy.orm import selectinload
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

    Alur SAMA PERSIS dengan POST /search/topics (tier-1 -> butuh
    confirm_third_party=true baru tier-3 jalan, lihat docstring modul) --
    cuma di sini scope-nya SATU topik yang sudah ada, bukan bikin/update
    topik baru.
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
    crawling_keywords = []
    needs_confirmation_keywords = []
    now = datetime.now(timezone.utc)

    for stk in topic.topic_keywords:
        # SENGAJA tidak pakai topic.auto_crawl di sini (beda dgn search_by_topics())
        # -- itu nilai yang DI-PERSIST saat topik dibuat/disimpan, kalau kebetulan
        # ke-set false (mis. sengaja auto_crawl=false saat 'Simpan Topik' krn
        # cuma mau simpan definisi, bukan cari), topik itu akan PERMANEN tidak
        # pernah bisa ditawari tier-3 lewat endpoint ini lagi -- tidak ada UI
        # utk toggle auto_crawl balik (beda dgn schedule yang punya endpoint
        # sendiri). confirm_third_party per-request SUDAH jadi gerbang keamanan
        # yang cukup (persis spirit yang diminta user), jadi endpoint by-id ini
        # selalu izinkan tier-3 kalau confirm_third_party=true, terlepas dari
        # auto_crawl yang tersimpan.
        kw_result = await _search_keyword_tiered(
            db, stk.keyword_text, platforms, body.limit_per_keyword, body.include_sentiment,
            True, body.confirm_third_party,
        )
        kw_result["keyword_id"] = str(stk.keyword_id)

        if kw_result["status"] == "needs_confirmation":
            needs_confirmation_keywords.append(stk.keyword_text)
        elif kw_result["status"] == "crawling":
            crawling_keywords.append(stk.keyword_text)
            # Update last_rescanned_at supaya rescan_service.py (jadwal
            # berkala) tidak langsung ulang tier-3 lagi kalau topik ini
            # JUGA schedule_recurring=true -- pencarian manual barusan
            # sudah menghitung sbg "baru dicek" utk cooldown yang sama.
            stk.last_rescanned_at = now

        keyword_results.append(kw_result)

    await db.commit()

    total_posts = sum(kd["total"] for kd in keyword_results)
    has_data = total_posts > 0
    if crawling_keywords:
        status = "partial" if has_data else "crawling"
    elif needs_confirmation_keywords:
        status = "partial_needs_confirmation" if has_data else "needs_confirmation"
    else:
        status = "ready"

    note = None
    if crawling_keywords:
        note = "Keyword dengan status 'crawling' baru saja dicari ke third-party (Apify/Firecrawl/YouTube API)."
    elif needs_confirmation_keywords:
        note = (
            "Keyword dengan status 'needs_confirmation' tidak ditemukan di database. "
            "Panggil ulang endpoint ini dengan confirm_third_party=true untuk mencari ke third-party."
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
        "crawling_keywords": crawling_keywords,
        "needs_confirmation_keywords": needs_confirmation_keywords,
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
