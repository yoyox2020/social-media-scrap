"""
Katalog API pihak ketiga -- CRUD + hubungkan ke agent (2026-07-22).
Admin-only. Lihat app/services/third_party_apis/service.py.
"""
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.services.auth.dependencies import require_admin
from app.services.third_party_apis import service
from app.shared.exceptions import NotFoundError
from app.shared.utils import build_success_response

router = APIRouter(prefix="/third-party-apis", tags=["third-party-apis"])


class AddApiRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    provider: str = Field(..., min_length=1, max_length=100)
    api_key: str | None = Field(default=None, max_length=2000)
    base_url: str | None = Field(default=None, max_length=500)
    account_email: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    agent_name: str | None = Field(default=None, max_length=255)
    platform_group: str | None = Field(
        default=None, max_length=100,
        description="Tag platform (youtube/tiktok/facebook/instagram/dll) -- key ini CUMA dipakai rotasi utk platform ini, tidak berebut kuota dgn platform lain.",
    )


class UpdateApiRequest(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    provider: str | None = Field(default=None, max_length=100)
    api_key: str | None = Field(default=None, max_length=2000)
    base_url: str | None = Field(default=None, max_length=500)
    account_email: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = Field(default=None)
    platform_group: str | None = Field(default=None, max_length=100)


class LinkAgentRequest(BaseModel):
    agent_name: str = Field(..., min_length=1, max_length=255)


@router.get("", response_model=dict)
async def list_apis(_admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    apis = await service.list_apis(db)
    return build_success_response({"apis": apis})


@router.post("", response_model=dict)
async def add_api(
    body: AddApiRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    entry = await service.add_api(
        db, body.name, body.provider, body.api_key, body.base_url, body.account_email, body.description,
        agent_name=body.agent_name, platform_group=body.platform_group,
    )
    return build_success_response({"id": str(entry.id), "name": entry.name})


@router.patch("/{api_id}", response_model=dict)
async def update_api(
    api_id: uuid.UUID,
    body: UpdateApiRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    entry = await service.update_api(
        db, api_id, body.name, body.provider, body.api_key, body.base_url,
        body.account_email, body.description, body.enabled, body.platform_group,
    )
    if not entry:
        raise NotFoundError(f"Third-party API {api_id} tidak ditemukan")
    return build_success_response({"id": str(entry.id), "updated": True})


@router.delete("/{api_id}", response_model=dict)
async def delete_api(
    api_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    ok = await service.delete_api(db, api_id)
    if not ok:
        raise NotFoundError(f"Third-party API {api_id} tidak ditemukan")
    return build_success_response({"deleted": True})


@router.post("/{api_id}/link", response_model=dict)
async def link_agent(
    api_id: uuid.UUID,
    body: LinkAgentRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Hubungkan API pihak ketiga ini ke 1 agent (idempotent, aman
    dipanggil berkali-kali utk agent yg sama)."""
    link = await service.link_agent(db, api_id, body.agent_name)
    if not link:
        raise NotFoundError(f"Third-party API {api_id} tidak ditemukan")
    return build_success_response({"linked": True, "agent_name": body.agent_name})


@router.post("/{api_id}/unlink", response_model=dict)
async def unlink_agent(
    api_id: uuid.UUID,
    body: LinkAgentRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    ok = await service.unlink_agent(db, api_id, body.agent_name)
    if not ok:
        raise NotFoundError("Link tidak ditemukan")
    return build_success_response({"unlinked": True})
