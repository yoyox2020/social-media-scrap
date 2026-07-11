from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init

# Import semua domain models agar SQLAlchemy mapper bisa resolve relationship
import app.domain.users.models  # noqa: F401
import app.domain.projects.models  # noqa: F401
import app.domain.keywords.models  # noqa: F401
import app.domain.posts.models  # noqa: F401
import app.domain.comments.models  # noqa: F401
import app.domain.sentiments.models  # noqa: F401
import app.domain.entities.models  # noqa: F401
import app.domain.topics.models  # noqa: F401
import app.domain.trends.models  # noqa: F401
import app.domain.reports.models  # noqa: F401
import app.domain.trending.models  # noqa: F401
import app.domain.youtube_analysis.models  # noqa: F401
import app.domain.viral_tracking.models  # noqa: F401
import app.domain.instagram_trending.models  # noqa: F401

from app.shared.config import settings

celery_app = Celery(
    "social_intelligence",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.workers.collector_worker",
        "app.workers.processing_worker",
        "app.workers.ai_worker",
        "app.workers.sentiment_worker",
        "app.workers.topic_worker",
        "app.workers.embedding_worker",
        "app.workers.report_worker",
        "app.workers.scheduled_tasks",
        "app.workers.youtube_worker",
        "app.workers.viral_tracking_worker",
        "app.workers.instagram_trending_worker",
        "app.workers.viral_discovery_worker",
        "app.workers.facebook_trending_worker",
        "app.workers.tiktok_trending_worker",
        "app.workers.twitter_trending_worker",
        "app.workers.news_worker",
        "app.workers.trends_worker",
        "app.workers.search_topics_worker",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Jakarta",
    enable_utc=True,
    task_track_started=True,
    # Beat schedule — periodic tasks
    beat_schedule={
        "daily-reports-08:00": {
            "task": "workers.generate_scheduled_reports",
            "schedule": crontab(hour=8, minute=0),
            "options": {"queue": "reports"},
        },
        "weekly-reports-monday-09:00": {
            "task": "workers.generate_scheduled_reports",
            "schedule": crontab(hour=9, minute=0, day_of_week=1),
            "kwargs": {"period": "week"},
            "options": {"queue": "reports"},
        },
        # Viral tracking: deteksi post >=1M views setiap 6 jam
        "viral-tracking-detect-every-6h": {
            "task": "workers.viral_tracking.detect_viral_posts",
            "schedule": crontab(minute=0, hour="0,6,12,18"),
        },
        # Viral tracking: resume semua tracker aktif setiap hari jam 12:00 WIB
        "viral-tracking-daily-check-12:00": {
            "task": "workers.viral_tracking.daily_check",
            "schedule": crontab(hour=12, minute=0),
        },
        # Auto-retry embedding untuk posts yang belum punya embedding (setiap 6 jam)
        "retry-missing-embeddings-every-6h": {
            "task": "workers.retry_missing_embeddings",
            "schedule": crontab(minute=30, hour="1,7,13,19"),
        },
        # Smart Search: scan ulang SearchTopic yang schedule_recurring=True
        # (lihat app/services/search_topics/rescan_service.py). Jalan
        # PALING PAGI (sebelum viral-discovery-daily 07:00 & konsumer harian
        # tiap platform) supaya akun/post yang baru ditemukan hari ini
        # (Facebook/TikTok/Twitter, via trend_recommendations) sempat
        # kepilih task harian platform itu sendiri di hari yang sama.
        "search-topic-rescan-daily": {
            "task": "workers.search_topics.daily_rescan",
            "schedule": crontab(
                hour=settings.search_topic_rescan_schedule_hour,
                minute=settings.search_topic_rescan_schedule_minute,
            ),
        },
        # Viral discovery: Claude (web_search) cari topik+akun Instagram viral
        # hari ini, submit ke trend_recommendations. Jalan 2 jam SEBELUM
        # instagram-trend-recommendation-daily-09:00 supaya topik yang
        # ditemukan punya kesempatan discrape di hari yang sama.
        "viral-discovery-daily": {
            "task": "workers.viral_discovery.daily_scan",
            "schedule": crontab(
                hour=settings.viral_discovery_schedule_hour,
                minute=settings.viral_discovery_schedule_minute,
            ),
        },
        # Instagram: scrape topik trend_recommendations (via Apify), jadwal via .env
        # Maks settings.instagram_trend_daily_budget topik/hari, lihat docs/trend-recommendations.md
        "instagram-trend-recommendation-daily": {
            "task": "workers.instagram_trend_recommendation.daily",
            "schedule": crontab(
                hour=settings.instagram_trend_scrape_schedule_hour,
                minute=settings.instagram_trend_scrape_schedule_minute,
            ),
        },
        # Facebook: scrape topik trend_recommendations (via Apify), jadwal via .env
        # Subsistem B terpisah dari Instagram, lihat docs/flow scrape/flow-scrap-facebook.md
        "facebook-trend-recommendation-daily": {
            "task": "workers.facebook_trend_recommendation.daily",
            "schedule": crontab(
                hour=settings.facebook_trend_scrape_schedule_hour,
                minute=settings.facebook_trend_scrape_schedule_minute,
            ),
        },
        # TikTok: scrape topik trend_recommendations (via Apify), jadwal via .env
        # Subsistem B terpisah dari Instagram/Facebook, 1 jam setelah Facebook
        "tiktok-trend-recommendation-daily": {
            "task": "workers.tiktok_trend_recommendation.daily",
            "schedule": crontab(
                hour=settings.tiktok_trend_scrape_schedule_hour,
                minute=settings.tiktok_trend_scrape_schedule_minute,
            ),
        },
        # Twitter/X: scrape topik trend_recommendations (via Apify), jadwal via .env
        # Subsistem B terpisah dari Instagram/Facebook/TikTok, 1 jam setelah TikTok
        "twitter-trend-recommendation-daily": {
            "task": "workers.twitter_trend_recommendation.daily",
            "schedule": crontab(
                hour=settings.twitter_trend_scrape_schedule_hour,
                minute=settings.twitter_trend_scrape_schedule_minute,
            ),
        },
        # News: search+scrape artikel trending via Firecrawl (TANPA LLM,
        # pipeline mandiri, TIDAK menyentuh viral_discovery_service.py/
        # Instagram/Facebook/TikTok/Twitter sama sekali), jadwal via .env
        "news-discovery-daily": {
            "task": "workers.news.daily_discovery",
            "schedule": crontab(
                hour=settings.news_discovery_schedule_hour,
                minute=settings.news_discovery_schedule_minute,
            ),
        },
        # Multi-Signal Trend Discovery — pipeline MANDIRI (app/services/trends/),
        # TIDAK menyentuh viral_discovery_service.py/kode platform manapun yang
        # sudah ada. Urutan jadwal SENGAJA: Twitter dulu (sumber paling objektif),
        # baru TikTok/Instagram (pakai topik Twitter hari ini sbg query
        # pencarian), gabungan/triangulasi PALING TERAKHIR (butuh ketiganya +
        # Google Trends + YouTube TrendingTopic baca-saja).
        "twitter-trends-daily": {
            "task": "workers.trends.twitter_discovery",
            "schedule": crontab(
                hour=settings.twitter_trends_schedule_hour,
                minute=settings.twitter_trends_schedule_minute,
            ),
        },
        "tiktok-trends-daily": {
            "task": "workers.trends.tiktok_discovery",
            "schedule": crontab(
                hour=settings.tiktok_trends_schedule_hour,
                minute=settings.tiktok_trends_schedule_minute,
            ),
        },
        "instagram-trends-daily": {
            "task": "workers.trends.instagram_discovery",
            "schedule": crontab(
                hour=settings.instagram_trends_schedule_hour,
                minute=settings.instagram_trends_schedule_minute,
            ),
        },
        "trends-combined-daily": {
            "task": "workers.trends.combined_discovery",
            "schedule": crontab(
                hour=settings.trends_combined_schedule_hour,
                minute=settings.trends_combined_schedule_minute,
            ),
        },
        # YouTube: fetch trending Indonesia setiap hari jam 12.00 WIB
        # project_id kosong → task otomatis pilih project pertama dari DB
        "youtube-trending-daily-12:00": {
            "task": "workers.youtube.fetch_trending",
            "schedule": crontab(hour=12, minute=0),
            "kwargs": {
                "project_id": "",    # kosong = auto-detect dari DB
                "geo": "ID",
                "period": "24h",
                "limit": 10,
                "max_pages_per_keyword": 2,
            },
        },
    },
)


@worker_process_init.connect
def configure_worker_db(**kwargs):
    """Reconfigure DB engine with NullPool setelah fork.

    asyncpg connections terikat ke event loop yang menciptakannya.
    Ketika Celery mem-fork worker, connections dari parent process diwarisi
    oleh child processes → InterfaceError saat asyncio.run() membuat event loop baru.
    Solusi: recreate engine dengan NullPool di tiap worker process sehingga
    setiap asyncio.run() selalu membuat fresh connection tanpa pool lama.
    """
    from sqlalchemy.pool import NullPool
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    import app.infrastructure.database.connection as db_module
    from app.shared.config import settings

    db_module.engine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
        echo=settings.app_debug,
    )
    db_module.AsyncSessionLocal = async_sessionmaker(
        bind=db_module.engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
