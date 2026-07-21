"""
Celery task untuk Smart Search — pemindaian berkala harian + antrian
pencarian yang di-konfirmasi user secara langsung + AI-context discovery +
notifikasi viral per jam.

Beat schedule (di celery_app.py):
  search-topic-rescan-daily      → search_topics_daily_rescan_task
  search-topic-ai-discovery-daily → search_topics_ai_discovery_daily_task
  search-topic-notifications-hourly → search_topics_hourly_notifications_task

Dipicu on-demand (dari app/api/v1/topic_search.py saat tier-1 kosong & auto_crawl=true):
  workers.search_topics.process_confirmed_queue → process_confirmed_search_queue_task
"""
import asyncio
import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="workers.search_topics.daily_rescan",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def search_topics_daily_rescan_task(self):
    """
    Task harian: scan ulang semua SearchTopic yang `schedule_recurring=True`
    dan masih dalam window `schedule_expires_at`, lihat
    app/services/search_topics/rescan_service.py utk detail logic
    cooldown/cost-control per platform.
    """
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.services.search_topics.rescan_service import run_daily_search_topic_rescan

    async def _run():
        async with AsyncSessionLocal() as db:
            return await run_daily_search_topic_rescan(db)

    try:
        result = asyncio.run(_run())
        logger.info("search_topics_daily_rescan done: %s", result)
        return result
    except Exception as exc:
        logger.error("search_topics_daily_rescan error: %s", exc)
        raise self.retry(exc=exc)


@celery_app.task(
    name="workers.search_topics.ai_discovery_daily",
    bind=True,
    max_retries=1,
    default_retry_delay=300,
)
def search_topics_ai_discovery_daily_task(self):
    """
    Task harian: AI dipandu topik+keyword SearchTopic recurring sbg konteks,
    cari perkembangan/sub-topik BARU terkait, lihat
    app/services/search_topics/ai_discovery_service.py. `max_retries=1`
    (bukan 2 seperti rescan literal) krn task ini panggil AI berbayar --
    retry penuh akan mengulang biaya semua topik yang sudah dicoba.
    """
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.services.search_topics.ai_discovery_service import run_daily_search_topic_ai_discovery

    async def _run():
        async with AsyncSessionLocal() as db:
            return await run_daily_search_topic_ai_discovery(db)

    try:
        result = asyncio.run(_run())
        logger.info("search_topics_ai_discovery_daily done: %s", result)
        return result
    except Exception as exc:
        logger.error("search_topics_ai_discovery_daily error: %s", exc)
        raise self.retry(exc=exc)


@celery_app.task(
    name="workers.search_topics.hourly_viral_notifications",
    bind=True,
    max_retries=1,
    default_retry_delay=180,
)
def search_topics_hourly_notifications_task(self):
    """
    Task per jam: cek semua SearchTopic aktif, per platform+keyword, cari
    post yang lewat ambang batas viral (disimpan di Redis, lihat
    app/services/search_topics/notification_service.py get_threshold()) DAN
    belum pernah dinotifikasi -- simpan sbg TopicNotification baru.
    """
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.infrastructure.redis.connection import reset_redis_client
    from app.services.search_topics.notification_service import run_hourly_topic_notifications

    async def _run():
        # WAJIB paling awal -- lihat docstring reset_redis_client(). Tanpa ini,
        # client Redis yg di-cache global masih terikat ke event loop task
        # Celery SEBELUMNYA yg sudah ditutup -> RuntimeError: Event loop is
        # closed (ditemukan 2026-07-19: 307x error dlm 48 jam, 0 notifikasi
        # baru sejak 2026-07-17 -- task ini crash TIAP KALI persis di
        # get_lookback_days(), baris pertama yg sentuh Redis).
        await reset_redis_client()
        async with AsyncSessionLocal() as db:
            return await run_hourly_topic_notifications(db)

    try:
        result = asyncio.run(_run())
        logger.info("search_topics_hourly_notifications done: %s", result)
        return result
    except Exception as exc:
        logger.error("search_topics_hourly_notifications error: %s", exc)
        raise self.retry(exc=exc)


@celery_app.task(
    name="workers.search_topics.process_confirmed_queue",
    bind=True,
    max_retries=1,
    default_retry_delay=60,
)
def process_confirmed_search_queue_task(self, items: list[dict], topic_id: str | None = None):
    """
    Dipicu SAAT ITU JUGA (bukan jadwal) saat POST /search/topics atau
    POST /search/topics/{id}/search menemukan tier-1 kosong -- proses
    `items` (keyword+platform yang perlu dicari ke third-party) SATU PER
    SATU berurutan, lihat app/services/search_topics/queue_service.py.
    Endpoint pemanggil TIDAK menunggu task ini -- langsung balas "queued"
    supaya request HTTP tidak timeout menunggu Apify/Firecrawl (bisa
    15-60+ detik per panggilan, apalagi kalau item-nya banyak).
    """
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.services.search_topics.queue_service import run_confirmed_search_queue

    async def _run():
        async with AsyncSessionLocal() as db:
            return await run_confirmed_search_queue(db, items, topic_id)

    try:
        result = asyncio.run(_run())
        logger.info("process_confirmed_search_queue done: %s", result)
        return result
    except Exception as exc:
        logger.error("process_confirmed_search_queue error: %s", exc)
        raise self.retry(exc=exc)
