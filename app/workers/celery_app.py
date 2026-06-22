from celery import Celery
from celery.schedules import crontab

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
    },
)
