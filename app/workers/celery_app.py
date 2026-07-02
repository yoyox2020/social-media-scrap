from celery import Celery
from celery.schedules import crontab

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
            "options": {"queue": "default"},
        },
        # Viral tracking: resume semua tracker aktif setiap hari jam 12:00 WIB
        "viral-tracking-daily-check-12:00": {
            "task": "workers.viral_tracking.daily_check",
            "schedule": crontab(hour=12, minute=0),
            "options": {"queue": "default"},
        },
        # Auto-retry embedding untuk posts yang belum punya embedding (setiap 6 jam)
        "retry-missing-embeddings-every-6h": {
            "task": "workers.retry_missing_embeddings",
            "schedule": crontab(minute=30, hour="1,7,13,19"),
            "options": {"queue": "default"},
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
            "options": {"queue": "default"},
        },
    },
)
