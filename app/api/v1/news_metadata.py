"""Data lengkap artikel News utk FRONTEND (2026-07-24) -- PUBLIK (tanpa
login), pola SAMA PERSIS dgn threads_metadata.py. Lihat
app/services/news_metadata/service.py utk detail field."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.connection import get_db
from app.services.news_metadata import service
from app.shared.exceptions import NotFoundError
from app.shared.utils import build_success_response

router = APIRouter(prefix="/news/metadata", tags=["news-metadata"])


@router.get("", response_model=dict)
async def list_news_metadata(
    topic: str | None = Query(default=None, description="Filter by topik pencarian"),
    search: str | None = Query(default=None, description="Cari di judul/isi/nama sumber"),
    sort_by: str = Query(default="published_at", description="trend_score|freshness_score|authority_score|published_at"),
    order: str = Query(default="desc", description="asc|desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Daftar artikel News siap dipakai frontend langsung."""
    result = await service.list_posts(db, topic, search, sort_by, order, page, page_size)
    return build_success_response(result)


@router.get("/topics", response_model=dict)
async def list_topics(db: AsyncSession = Depends(get_db)):
    """Daftar topik yg pernah dicari + jumlah artikel masing2."""
    topics = await service.list_topics(db)
    return build_success_response({"topics": topics})


@router.get("/{post_id}", response_model=dict)
async def get_news_metadata_detail(post_id: str, db: AsyncSession = Depends(get_db)):
    """Detail 1 artikel LENGKAP."""
    data = await service.get_post_detail(db, post_id)
    if not data:
        raise NotFoundError(f"Artikel News '{post_id}' tidak ditemukan di database")
    return build_success_response(data)
