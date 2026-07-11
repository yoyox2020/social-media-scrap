from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.scrape_runs.models import ScrapeRun
from app.domain.trend_recommendations.models import TrendRecommendation
from app.domain.trend_recommendations.schemas import TrendRecommendationBatchCreate, TrendRecommendationItem
from app.shared.apify_errors import QUOTA_ERROR_PREFIX

MAX_PER_DAY = 20

# Berapa kali percobaan scrape boleh gagal (status='failed' di scrape_runs,
# keyword_text=topic) sebelum topik ditandai 'failed_permanent' -- supaya
# tidak terus buang jatah budget harian utk topik yg akunnya jelas tidak akan
# pernah berhasil (invalid/kosong permanen). Gampang diubah: cuma angka ini.
FAILED_PERMANENT_THRESHOLD = 3

# Smart Search (app/services/search_topics/) dapat jatah TERPROTEKSI dari
# MAX_PER_DAY yang sama -- baris ber-source diawali prefix ini TIDAK bisa
# digusur oleh submission LAIN (AI viral-discovery/pencarian interaktif
# platform) selama jumlahnya masih <= SMART_SEARCH_RESERVED_SLOTS. Ini
# JATAH MINIMUM TERJAMIN, bukan batas maksimum -- baris smart_search_* tetap
# bisa tumbuh lebih banyak kalau skornya cukup tinggi utk menang kompetisi
# normal. TIDAK aktif (tidak berubah perilaku) di hari-hari tanpa aktivitas
# Smart Search sama sekali -- lihat _pick_eviction_candidate().
_RESERVED_SOURCE_PREFIX = "smart_search_"


def _is_reserved_source(source: str) -> bool:
    return source.startswith(_RESERVED_SOURCE_PREFIX)


def _pick_eviction_candidate(active_rows: list[TrendRecommendation], reserved_slots: int) -> TrendRecommendation:
    """Skor terendah yang digusur kalau slot MAX_PER_DAY penuh -- tapi
    hormati jatah reserved Smart Search selama masih di bawah/sama dengan
    reserved_slots (lihat catatan _RESERVED_SOURCE_PREFIX di atas)."""
    if reserved_slots > 0:
        reserved_rows = [r for r in active_rows if _is_reserved_source(r.source)]
        non_reserved_rows = [r for r in active_rows if not _is_reserved_source(r.source)]
        if len(reserved_rows) <= reserved_slots and non_reserved_rows:
            return min(non_reserved_rows, key=lambda r: r.score)
    return min(active_rows, key=lambda r: r.score)


async def submit_recommendations(
    db: AsyncSession,
    body: TrendRecommendationBatchCreate,
) -> dict[str, list[str]]:
    """
    Upsert topik viral untuk satu hari, dibatasi maksimal MAX_PER_DAY baris.

    - Topik yang sudah ada di hari itu -> update score/related_accounts.
    - Topik baru & slot masih tersedia -> insert.
    - Topik baru & slot penuh -> gantikan topik dengan score terendah kalau
      score baru lebih tinggi, kalau tidak -> ditolak. Baris ber-source
      'smart_search_*' dapat jatah terproteksi dari penggusuran ini, lihat
      _pick_eviction_candidate()/_RESERVED_SOURCE_PREFIX di atas.
    """
    from app.shared.config import settings

    reserved_slots = getattr(settings, "smart_search_reserved_slots", 0)
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

        lowest = _pick_eviction_candidate(active_rows, reserved_slots)
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


async def mark_failed_permanent_if_exhausted(db: AsyncSession, topic: TrendRecommendation) -> bool:
    """
    Kalau topik ini sudah gagal discrape >= FAILED_PERMANENT_THRESHOLD kali
    (dihitung dari scrape_runs, keyword_text=topic.topic, status='failed'),
    ubah status jadi 'failed_permanent' supaya tidak terus kepilih di batch
    harian manapun (query `WHERE status='pending'` yang sudah ada otomatis
    tidak lagi mengambil topik ini, tidak perlu ubah query apa pun).

    Dipanggil oleh app/services/facebook/trend_scrape_service.py dan
    app/services/tiktok/trend_scrape_service.py SETELAH satu percobaan gagal
    tercatat. TIDAK dipanggil dari Instagram (run_daily_trend_scrape() masih
    dibekukan, butuh izin terpisah).

    Catatan: hitungan gagal ini LINTAS PLATFORM (kolom `status` di
    trend_recommendations satu untuk seluruh topik, bukan per-platform) --
    kalau topik yang sama punya akun di >1 platform, gagal di satu platform
    ikut menghabiskan jatah topik itu utk platform lain juga. Trade-off yang
    disengaja demi kesederhanaan (skema tabel dibekukan, tidak nambah kolom
    baru per-platform).

    Kegagalan karena KUOTA/RATE-LIMIT APIFY HABIS (error_message ditandai
    QUOTA_ERROR_PREFIX oleh app.shared.apify_errors.tag_if_quota_error(),
    lihat pipeline_service masing-masing platform) TIDAK ikut dihitung --
    itu kegagalan sementara di pihak kita, bukan bukti topik ini genuinely
    tidak bisa discrape. Lihat docs/analisa-gap-facebook.md gap 2.

    Return True kalau topik BARU SAJA ditandai failed_permanent (buat log).
    """
    failed_count = await db.scalar(
        select(func.count()).select_from(ScrapeRun)
        .where(
            ScrapeRun.keyword_text == topic.topic,
            ScrapeRun.status == "failed",
            or_(
                ScrapeRun.error_message.is_(None),
                ~ScrapeRun.error_message.like(f"{QUOTA_ERROR_PREFIX}%"),
            ),
        )
    )
    if (failed_count or 0) >= FAILED_PERMANENT_THRESHOLD:
        topic.status = "failed_permanent"
        return True
    return False
