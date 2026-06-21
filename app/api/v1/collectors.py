import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.repositories.keyword_repository import KeywordRepository
from app.services.auth.dependencies import get_current_user
from app.services.collector.schemas import CollectRequest, CollectJobResponse, JobStatusResponse
from app.services.collector.service import CollectorService
from app.shared.utils import build_success_response

router = APIRouter(prefix="/collectors", tags=["collectors"])


def _service(db: AsyncSession = Depends(get_db)) -> CollectorService:
    return CollectorService(KeywordRepository(db))


@router.post("/collect", response_model=dict, status_code=202)
async def trigger_collection(
    body: CollectRequest,
    current_user: User = Depends(get_current_user),
    service: CollectorService = Depends(_service),
):
    """
    Trigger pengumpulan data dari social media untuk keyword tertentu.
    Mengembalikan job IDs yang bisa dipakai untuk cek status.
    """
    body.validate_platforms()
    result = await service.trigger_collection(body.keyword_id, body.platforms)
    return build_success_response(result.model_dump())


@router.get("/jobs/{job_id}", response_model=dict)
async def get_job_status(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    """Cek status Celery task berdasarkan job_id."""
    from celery.result import AsyncResult
    from app.workers.celery_app import celery_app

    task_result = AsyncResult(job_id, app=celery_app)

    response = JobStatusResponse(
        job_id=job_id,
        status=task_result.status,
        result=task_result.result if task_result.successful() else None,
    )
    return build_success_response(response.model_dump())


@router.get("/platforms", response_model=dict)
async def list_supported_platforms():
    """List platform yang didukung untuk koleksi data."""
    from app.integrations.ensemble_data.endpoints import SUPPORTED_COLLECTION_PLATFORMS
    return build_success_response({"platforms": SUPPORTED_COLLECTION_PLATFORMS})
