"""Data lengkap post TikTok utk FRONTEND (2026-07-23) -- PUBLIK (tanpa
login), pola SAMA PERSIS dgn app/api/v1/youtube_metadata.py. Lihat
app/services/tiktok_metadata/service.py."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.connection import get_db
from app.services.tiktok_metadata import service
from app.shared.exceptions import NotFoundError
from app.shared.utils import build_success_response

router = APIRouter(prefix="/tiktok/metadata", tags=["tiktok-metadata"])


@router.get("", response_model=dict)
async def list_tiktok_metadata(
    topic: str | None = Query(default=None, description="Filter by topik pencarian (mis. 'jampidsus')"),
    search: str | None = Query(default=None, description="Cari di judul/nama akun/isi caption"),
    sort_by: str = Query(default="trend_score", description="trend_score|engagement_score|freshness_score|authority_score|views|published_at"),
    order: str = Query(default="desc", description="asc|desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Daftar post TikTok (judul, metrics, skor, AI summary, dll) --
    hasil pipeline multi-agent, siap dipakai frontend langsung."""
    result = await service.list_posts(db, topic, search, sort_by, order, page, page_size)
    return build_success_response(result)


@router.get("/topics", response_model=dict)
async def list_topics(db: AsyncSession = Depends(get_db)):
    """Daftar topik yg pernah dicari + jumlah video masing2 -- utk
    dropdown filter di frontend."""
    topics = await service.list_topics(db)
    return build_success_response({"topics": topics})


@router.get("/{video_id}", response_model=dict)
async def get_tiktok_metadata_detail(video_id: str, db: AsyncSession = Depends(get_db)):
    """Detail 1 video LENGKAP + semua komentar tersimpan."""
    data = await service.get_post_detail(db, video_id)
    if not data:
        raise NotFoundError(f"Video TikTok '{video_id}' tidak ditemukan di database")
    return build_success_response(data)
