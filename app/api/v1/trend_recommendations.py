"""
Endpoint rekomendasi topik viral dari AI eksternal.

POST /trend-recommendations — publik, tanpa auth, supaya AI eksternal bisa
langsung submit hasil analisisnya (topik + akun-akun yang viral).
GET  /trend-recommendations — baca hasil tersimpan (butuh login), dipakai
sebagai patokan tahap pencarian/scraping berikutnya.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trend_recommendations.models import TrendRecommendation
from app.domain.trend_recommendations.schemas import (
    TrendRecommendationBatchCreate,
    TrendRecommendationResponse,
)
from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.services.auth.dependencies import get_current_user
from app.services.trend_recommendations.service import submit_recommendations
from app.shared.utils import build_success_response

router = APIRouter(prefix="/trend-recommendations", tags=["trend-recommendations"])


@router.post("", response_model=dict, status_code=201, summary="Submit rekomendasi topik viral (AI eksternal, publik)")
async def create_trend_recommendations(
    body: TrendRecommendationBatchCreate,
    db: AsyncSession = Depends(get_db),
):
    result = await submit_recommendations(db, body)
    return build_success_response(result)


@router.get("", response_model=dict, summary="Lihat/cari topik viral tersimpan")
async def list_trend_recommendations(
    recommendation_date: date | None = Query(default=None, description="Filter tanggal tertentu. Kosong + topic kosong = default hari ini."),
    platform: str | None = Query(default=None, description="Filter topik yang punya related_accounts di platform ini"),
    topic: str | None = Query(default=None, description="Cari topik (partial match) -- LINTAS SEMUA TANGGAL kalau recommendation_date tidak diisi"),
    limit: int = Query(default=20, ge=1, le=20),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Dua mode pencarian:
    - `recommendation_date` DIISI -> topik pada tanggal itu saja (opsional
      dipersempit `topic`/`platform`).
    - `recommendation_date` KOSONG + `topic` DIISI -> search topik LINTAS
      SEMUA TANGGAL (tidak dibatasi hari ini) -- supaya topik yang cuma
      ditemukan di hari-hari sebelumnya tetap ketemu.
    - Keduanya kosong -> default browse topik HARI INI (perilaku lama).
    """
    stmt = select(TrendRecommendation)

    if recommendation_date:
        stmt = stmt.where(TrendRecommendation.recommendation_date == recommendation_date)
    elif not topic:
        stmt = stmt.where(TrendRecommendation.recommendation_date == date.today())
    # else: topic diisi tanpa recommendation_date -> sengaja TIDAK difilter
    # tanggal, search lintas semua tanggal.

    if topic:
        stmt = stmt.where(TrendRecommendation.topic.ilike(f"%{topic}%"))
    if platform:
        stmt = stmt.where(TrendRecommendation.related_accounts.op("@>")([{"platform": platform}]))

    stmt = stmt.order_by(TrendRecommendation.recommendation_date.desc(), TrendRecommendation.score.desc()).limit(limit)

    rows = (await db.execute(stmt)).scalars().all()
    return build_success_response(
        [TrendRecommendationResponse.model_validate(r).model_dump(mode="json") for r in rows]
    )
