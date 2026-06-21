import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.services.auth.dependencies import get_current_user
from app.services.processing.schemas import ProcessRequest, ProcessJobResponse
from app.shared.utils import build_success_response

router = APIRouter(prefix="/processing", tags=["processing"])


@router.post("/trigger", response_model=dict, status_code=202)
async def trigger_processing(
    body: ProcessRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Trigger processing pipeline untuk semua post satu keyword.
    Return job_id untuk tracking status via /collectors/jobs/{job_id}.
    """
    from app.workers.processing_worker import process_posts_task

    task = process_posts_task.delay(str(body.keyword_id), body.force_reprocess)
    response = ProcessJobResponse(
        keyword_id=body.keyword_id,
        job_id=task.id,
    )
    return build_success_response(response.model_dump())


@router.post("/trigger-sync", response_model=dict)
async def trigger_processing_sync(
    body: ProcessRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Jalankan processing secara sinkron (tanpa Celery) — untuk testing/debugging.
    Gunakan /trigger untuk production.
    """
    from app.services.processing.service import ProcessingService

    service = ProcessingService(db)
    result = await service.process_keyword(body.keyword_id, body.force_reprocess)
    return build_success_response(result.to_dict())


@router.get("/stats/{keyword_id}", response_model=dict)
async def get_processing_stats(
    keyword_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Statistik processing untuk satu keyword."""
    from sqlalchemy import func, select
    from app.domain.posts.models import Post

    result = await db.execute(
        select(
            func.count().label("total"),
            func.count().filter(Post.is_processed == True).label("processed"),     # noqa: E712
            func.count().filter(Post.is_near_duplicate == True).label("duplicates"), # noqa: E712
            func.count().filter(Post.language == "id").label("lang_id"),
            func.count().filter(Post.language == "en").label("lang_en"),
        ).where(Post.keyword_id == keyword_id)
    )
    row = result.one()
    return build_success_response({
        "keyword_id": str(keyword_id),
        "total_posts": row.total,
        "processed": row.processed,
        "near_duplicates": row.duplicates,
        "language_breakdown": {"id": row.lang_id, "en": row.lang_en},
    })
