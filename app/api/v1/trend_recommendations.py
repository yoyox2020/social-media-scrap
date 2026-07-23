"""Input topik manual utk auto-crawl (2026-07-22). Admin-only -- beda
dari desain lama (docstring lawas di model TrendRecommendation
menyebut endpoint publik utk AI eksternal), krn di sini yg input
adalah USER lewat dashboard, bukan sistem AI pihak ketiga.

Keyword kustom per topik (2026-07-24, permintaan user "1 topik bisa
create beberapa keyword") -- lihat app/services/trend_recommendations/
service.py utk detail alur "kalau ada keyword kustom, agent_search
pakai itu; kalau tidak, fallback ke 3-varian auto"."""
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.services.auth.dependencies import require_admin
from app.services.trend_recommendations import service
from app.shared.exceptions import NotFoundError
from app.shared.utils import build_success_response

router = APIRouter(prefix="/trend-recommendations", tags=["trend-recommendations"])


class ManualTopicCreate(BaseModel):
    topic: str = Field(..., min_length=1, max_length=255)
    score: float = Field(default=1.0, ge=0.0, le=1.0)
    keywords: list[str] | None = Field(
        default=None, description="Keyword kustom utk topik ini (opsional) -- kalau diisi, dipakai LANGSUNG "
        "sbg keyword pencarian (bukan 3-varian auto \"topik/topik terbaru/topik trending\")",
    )


class AddKeywordsRequest(BaseModel):
    keywords: list[str] = Field(..., min_length=1)


@router.post("/manual", response_model=dict)
async def submit_manual_topic(
    body: ManualTopicCreate,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Tambah/naikkan score topik -- masuk pool yg dibaca auto-crawl
    tiap jam (top 20 by score). Bisa sekalian isi `keywords` (1 topik
    -> banyak keyword) dlm 1 panggilan."""
    result = await service.submit_manual_topic(db, body.topic, body.score, body.keywords)
    return build_success_response(result)


@router.post("/{trend_recommendation_id}/keywords", response_model=dict)
async def add_keywords(
    trend_recommendation_id: uuid.UUID,
    body: AddKeywordsRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Tambah keyword ke topik yg SUDAH ADA (dari `id` di GET /trend-recommendations)."""
    added = await service.add_keywords_for_topic(db, trend_recommendation_id, body.keywords)
    if not added and body.keywords:
        raise NotFoundError(f"Topik dgn id '{trend_recommendation_id}' tidak ditemukan, atau semua keyword itu sudah ada")
    return build_success_response({"trend_recommendation_id": str(trend_recommendation_id), "keywords_added": added})


@router.get("/{trend_recommendation_id}/keywords", response_model=dict)
async def get_keywords(
    trend_recommendation_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    keywords = await service.get_keywords_for_topic(db, trend_recommendation_id)
    return build_success_response({"trend_recommendation_id": str(trend_recommendation_id), "keywords": keywords})


@router.get("", response_model=dict)
async def list_top_topics(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Lihat topik yg SAAT INI akan dipakai auto-crawl (top 20 by score,
    dedup per topik) + keyword kustomnya (kalau ada) -- utk dashboard,
    supaya user bisa cek sebelum jam berikutnya."""
    topics = await service.list_top_topics(db)
    return build_success_response({"topics": topics, "count": len(topics)})
