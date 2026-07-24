"""Trigger pipeline multi-agent News (2026-07-24). Admin-only. Lihat
app/agents/pipeline.py::run_news_pipeline utk alur lengkap."""
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import pipeline
from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.services.auth.dependencies import require_admin
from app.shared.exceptions import NotFoundError
from app.shared.utils import build_success_response

router = APIRouter(prefix="/pipeline/news", tags=["pipeline-news"])


class RunPipelineRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=255)


@router.post("/run", response_model=dict)
async def run_pipeline(
    body: RunPipelineRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Jalankan pipeline SEKARANG (sinkron, tunggu sampai selesai) --
    agent_topic -> agent_search -> agent_news -> agent-struktur-data ->
    simpan DB."""
    result = await pipeline.run_news_pipeline(db, body.topic)
    return build_success_response(result)


@router.get("/log/{run_id}", response_model=dict)
async def get_run_log(
    run_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Lihat log aktivitas langkah-per-langkah 1 run pipeline."""
    log = await pipeline.get_pipeline_log(db, run_id)
    if not log:
        raise NotFoundError(f"Tidak ada log utk run_id {run_id}")
    return build_success_response({"run_id": str(run_id), "log": log})
