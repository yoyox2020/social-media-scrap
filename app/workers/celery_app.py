from celery import Celery

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
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
)
