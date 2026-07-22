"""Bank key rotasi otomatis lintas agent (2026-07-22). Admin-only.
Lihat app/services/rotation_key_bank/service.py."""
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.services.auth.dependencies import require_admin
from app.services.rotation_key_bank import service
from app.shared.exceptions import NotFoundError
from app.shared.utils import build_success_response

router = APIRouter(prefix="/rotation-bank", tags=["rotation-key-bank"])


class AddBankKeyRequest(BaseModel):
    provider: str = Field(..., min_length=1, max_length=100)
    api_key: str = Field(..., min_length=1, max_length=2000)
    model: str | None = Field(default=None, max_length=255)
    account_email: str | None = Field(default=None, max_length=255)


@router.get("", response_model=dict)
async def list_bank_keys(_admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    keys = await service.list_bank_keys(db)
    return build_success_response({"keys": keys})


@router.post("", response_model=dict)
async def add_bank_key(
    body: AddBankKeyRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    entry = await service.add_bank_key(db, body.provider, body.api_key, body.model, body.account_email)
    return build_success_response({"id": str(entry.id), "provider": entry.provider})


@router.post("/{key_id}/disable", response_model=dict)
async def disable_bank_key(
    key_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    entry = await service.disable_bank_key(db, key_id)
    if not entry:
        raise NotFoundError(f"Key bank {key_id} tidak ditemukan")
    return build_success_response({"id": str(entry.id), "status": entry.status})


@router.delete("/{key_id}", response_model=dict)
async def delete_bank_key(
    key_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    ok = await service.delete_bank_key(db, key_id)
    if not ok:
        raise NotFoundError(f"Key bank {key_id} tidak ditemukan")
    return build_success_response({"deleted": True})
