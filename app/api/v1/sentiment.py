import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.repositories.sentiment_repository import SentimentRepository
from app.services.ai.schemas import AnalyzeJobResponse, AnalyzeRequest
from app.services.auth.dependencies import get_current_user
from app.shared.utils import build_success_response

router = APIRouter(prefix="/sentiment", tags=["sentiment"])


@router.post("/analyze", response_model=dict, status_code=202)
async def analyze_sentiment(
    body: AnalyzeRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Trigger analisis sentimen (+ NER + embedding) untuk semua post satu keyword.
    Task dijalankan async via Celery di worker-ai container.
    """
    from app.workers.ai_worker import analyze_keyword_task

    task = analyze_keyword_task.delay(
        str(body.keyword_id),
        body.force_reanalyze,
        body.run_sentiment,
        body.run_ner,
        body.run_embedding,
    )
    response = AnalyzeJobResponse(keyword_id=body.keyword_id, job_id=task.id)
    return build_success_response(response.model_dump())


@router.post("/analyze-sync", response_model=dict)
async def analyze_sentiment_sync(
    body: AnalyzeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Jalankan analisis sinkron tanpa Celery — untuk dev/debug."""
    from app.services.ai.service import AIService

    service = AIService(db)
    stats = await service.analyze_keyword(body)
    return build_success_response(stats.to_dict())


@router.get("/results/{post_id}", response_model=dict)
async def get_sentiment_result(
    post_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ambil hasil sentimen untuk satu post."""
    repo = SentimentRepository(db)
    sentiment = await repo.get_by_post_id(post_id)
    if not sentiment:
        return build_success_response(None)
    return build_success_response({
        "post_id": str(post_id),
        "label": sentiment.label,
        "score": sentiment.score,
        "model_version": sentiment.model_version,
        "analyzed_at": sentiment.created_at.isoformat() if sentiment.created_at else None,
    })


@router.get("/summary/{keyword_id}", response_model=dict)
async def get_sentiment_summary(
    keyword_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Distribusi sentimen (positive/negative/neutral) untuk satu keyword. Cache 5 menit."""
    from app.infrastructure.cache.redis_cache import cache_get, cache_set

    cache_key = f"sentiment:summary:{keyword_id}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return build_success_response(cached)

    repo = SentimentRepository(db)
    distribution = await repo.count_by_label_for_keyword(keyword_id)
    total = sum(distribution.values())
    data = {
        "keyword_id": str(keyword_id),
        "total_analyzed": total,
        "distribution": distribution,
        "percentages": {
            label: round(count / total * 100, 1) if total > 0 else 0
            for label, count in distribution.items()
        },
    }
    await cache_set(cache_key, data, ex=300)
    return build_success_response(data)
