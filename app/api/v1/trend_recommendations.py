"""Input topik manual utk auto-crawl YouTube (2026-07-22, permintaan
user). Admin-only -- beda dari desain lama (docstring lawas di model
TrendRecommendation menyebut endpoint publik utk AI eksternal), krn di
sini yg input adalah USER lewat dashboard, bukan sistem AI pihak
ketiga."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.services.auth.dependencies import require_admin
from app.services.trend_recommendations import service
from app.shared.utils import build_success_response

router = APIRouter(prefix="/trend-recommendations", tags=["trend-recommendations"])


class ManualTopicCreate(BaseModel):
    topic: str = Field(..., min_length=1, max_length=255)
    score: float = Field(default=1.0, ge=0.0, le=1.0)


@router.post("/manual", response_model=dict)
async def submit_manual_topic(
    body: ManualTopicCreate,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Tambah/naikkan score topik -- masuk pool yg dibaca auto-crawl
    YouTube tiap jam (top 20 by score)."""
    result = await service.submit_manual_topic(db, body.topic, body.score)
    return build_success_response(result)


@router.get("", response_model=dict)
async def list_top_topics(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Lihat topik yg SAAT INI akan dipakai auto-crawl (top 20 by score,
    dedup per topik) -- utk dashboard, supaya user bisa cek sebelum jam
    berikutnya."""
    topics = await service.list_top_topics(db)
    return build_success_response({"topics": topics, "count": len(topics)})
