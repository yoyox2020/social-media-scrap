"""
Target curl utk crawling per agent (2026-07-22). Admin-only. 1 agent
bisa punya BANYAK target -- lihat app/services/agent_curl_targets/service.py.
"""
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.services.auth.dependencies import require_admin
from app.services.agent_curl_targets import service
from app.shared.exceptions import NotFoundError
from app.shared.utils import build_success_response

router = APIRouter(prefix="/agent-curl-targets", tags=["agent-curl-targets"])


class AddCurlTargetRequest(BaseModel):
    agent_name: str = Field(..., min_length=1, max_length=255)
    name: str = Field(..., min_length=1, max_length=255)
    url: str = Field(..., min_length=1)
    method: str = Field(default="GET", max_length=10)
    headers: str | None = Field(default=None)
    body: str | None = Field(default=None)
    description: str | None = Field(default=None, max_length=2000)


class UpdateCurlTargetRequest(BaseModel):
    agent_name: str | None = Field(default=None, max_length=255)
    name: str | None = Field(default=None, max_length=255)
    url: str | None = Field(default=None)
    method: str | None = Field(default=None, max_length=10)
    headers: str | None = Field(default=None)
    body: str | None = Field(default=None)
    description: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = Field(default=None)


@router.get("", response_model=dict)
async def list_curl_targets(_admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    targets = await service.list_targets(db)
    return build_success_response({"targets": targets})


@router.post("", response_model=dict)
async def add_curl_target(
    body: AddCurlTargetRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    entry = await service.add_target(
        db, body.agent_name, body.name, body.url, body.method, body.headers, body.body, body.description,
    )
    return build_success_response({"id": str(entry.id), "name": entry.name})


@router.patch("/{target_id}", response_model=dict)
async def update_curl_target(
    target_id: uuid.UUID,
    body: UpdateCurlTargetRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    entry = await service.update_target(
        db, target_id, body.agent_name, body.name, body.url, body.method,
        body.headers, body.body, body.description, body.enabled,
    )
    if not entry:
        raise NotFoundError(f"Target curl {target_id} tidak ditemukan")
    return build_success_response({"id": str(entry.id), "updated": True})


@router.delete("/{target_id}", response_model=dict)
async def delete_curl_target(
    target_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    ok = await service.delete_target(db, target_id)
    if not ok:
        raise NotFoundError(f"Target curl {target_id} tidak ditemukan")
    return build_success_response({"deleted": True})


@router.post("/{target_id}/execute", response_model=dict)
async def execute_curl_target(
    target_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Jalankan target curl ini SUNGGUHAN sekarang (test manual) --
    placeholder {{NOW}}/{{NOW-Nh}}/dst di-resolve dulu jadi timestamp
    beneran (Python, bukan JS dashboard), baru request HTTP asli
    dikirim. Balikin status code + preview response asli."""
    result = await service.execute_target(db, target_id)
    if not result:
        raise NotFoundError(f"Target curl {target_id} tidak ditemukan")
    return build_success_response(result)
