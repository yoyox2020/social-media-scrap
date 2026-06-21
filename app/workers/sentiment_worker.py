"""Sentiment worker — delegate ke ai_worker.analyze_post_task."""
from app.workers.ai_worker import analyze_post_task
from app.workers.celery_app import celery_app


@celery_app.task(name="workers.analyze_sentiment")
def analyze_sentiment(post_id: str) -> dict:
    """Legacy task name — delegate ke analyze_post_task."""
    return analyze_post_task(post_id, run_sentiment=True, run_ner=False, run_embedding=False)
