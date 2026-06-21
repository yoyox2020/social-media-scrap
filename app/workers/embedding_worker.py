"""Embedding worker — delegate ke ai_worker.analyze_post_task."""
from app.workers.ai_worker import analyze_post_task
from app.workers.celery_app import celery_app


@celery_app.task(name="workers.generate_embedding")
def generate_embedding(post_id: str) -> dict:
    """Legacy task name — delegate ke analyze_post_task."""
    return analyze_post_task(post_id, run_sentiment=False, run_ner=False, run_embedding=True)
