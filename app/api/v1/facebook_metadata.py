"""Data lengkap post Facebook utk FRONTEND (2026-07-24) -- PUBLIK
(tanpa login), pola SAMA PERSIS dgn youtube_metadata.py & tiktok_metadata.py.
Lihat app/services/facebook_metadata/service.py utk detail beda field
dari platform lain (title selalu kosong, scores selalu null, dst)."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.connection import get_db
from app.services.facebook_metadata import service
from app.shared.exceptions import NotFoundError
from app.shared.utils import build_success_response

router = APIRouter(prefix="/facebook/metadata", tags=["facebook-metadata"])


@router.get("", response_model=dict)
async def list_facebook_metadata(
    topic: str | None = Query(default=None, description="Filter by topik pencarian"),
    search: str | None = Query(default=None, description="Cari di isi post/nama akun"),
    sort_by: str = Query(default="published_at", description="trend_score|engagement_score|freshness_score|authority_score|likes|published_at"),
    order: str = Query(default="desc", description="asc|desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Daftar post Facebook (isi, metrics, akun, dll) siap dipakai
    frontend langsung. Catatan: `scores` saat ini selalu null (belum
    ada agent penghitung skor utk Facebook)."""
    result = await service.list_posts(db, topic, search, sort_by, order, page, page_size)
    return build_success_response(result)


@router.get("/topics", response_model=dict)
async def list_topics(db: AsyncSession = Depends(get_db)):
    """Daftar topik yg pernah dicari + jumlah post masing2 -- utk
    dropdown filter di frontend. Kosong sekarang (data lama belum
    lewat pipeline topic), siap terisi begitu ada post baru."""
    topics = await service.list_topics(db)
    return build_success_response({"topics": topics})


@router.get("/{post_id}", response_model=dict)
async def get_facebook_metadata_detail(post_id: str, db: AsyncSession = Depends(get_db)):
    """Detail 1 post LENGKAP + semua komentar tersimpan."""
    data = await service.get_post_detail(db, post_id)
    if not data:
        raise NotFoundError(f"Post Facebook '{post_id}' tidak ditemukan di database")
    return build_success_response(data)
