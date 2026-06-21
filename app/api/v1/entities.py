import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.repositories.entity_repository import EntityRepository
from app.services.auth.dependencies import get_current_user
from app.shared.utils import build_success_response

router = APIRouter(prefix="/entities", tags=["entities"])


@router.get("/post/{post_id}", response_model=dict)
async def list_entities_by_post(
    post_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List semua entity yang diextract dari satu post."""
    repo = EntityRepository(db)
    entities = await repo.list_by_post_id(post_id)
    return build_success_response([
        {
            "id": str(e.id),
            "text": e.text,
            "entity_type": e.entity_type,
            "start_char": e.start_char,
            "end_char": e.end_char,
            "score": e.score,
        }
        for e in entities
    ])


@router.get("/keyword/{keyword_id}", response_model=dict)
async def list_entities_by_keyword(
    keyword_id: uuid.UUID,
    entity_type: str | None = Query(None, description="Filter by entity type, e.g. PERSON"),
    limit: int = Query(100, le=500),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List entities dari semua post satu keyword, dengan optional filter type."""
    repo = EntityRepository(db)
    entities = await repo.list_by_keyword(keyword_id, entity_type=entity_type, limit=limit)
    return build_success_response([
        {
            "id": str(e.id),
            "post_id": str(e.post_id),
            "text": e.text,
            "entity_type": e.entity_type,
            "score": e.score,
        }
        for e in entities
    ])


@router.get("/top/{keyword_id}", response_model=dict)
async def top_entities(
    keyword_id: uuid.UUID,
    entity_type: str | None = Query(None),
    top_n: int = Query(20, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Top N entity paling sering muncul untuk satu keyword."""
    repo = EntityRepository(db)
    top = await repo.top_entities_by_keyword(keyword_id, entity_type=entity_type, top_n=top_n)
    return build_success_response({
        "keyword_id": str(keyword_id),
        "entity_type_filter": entity_type,
        "top_entities": top,
    })


@router.get("/{entity_id}", response_model=dict)
async def get_entity(
    entity_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ambil detail satu entity."""
    repo = EntityRepository(db)
    entity = await repo.get_by_id(entity_id)
    if not entity:
        from app.shared.exceptions import NotFoundError
        raise NotFoundError("Entity tidak ditemukan")
    return build_success_response({
        "id": str(entity.id),
        "post_id": str(entity.post_id),
        "text": entity.text,
        "entity_type": entity.entity_type,
        "start_char": entity.start_char,
        "end_char": entity.end_char,
        "score": entity.score,
    })
