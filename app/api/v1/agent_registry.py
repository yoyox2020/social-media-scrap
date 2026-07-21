"""
"Kelola Agent" -- katalog terpusat semua agent AI (2026-07-22). Admin-only
(sama level dgn /api/v1/credentials, karena bisa menyimpan API key custom).
Lihat app/services/agent_registry/service.py utk desain lengkap.
"""
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.services.auth.dependencies import require_admin
from app.services.agent_registry import service
from app.shared.exceptions import NotFoundError, ValidationError
from app.shared.utils import build_success_response
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/agent-registry", tags=["agent-registry"])


class AddCustomAgentRequest(BaseModel):
    agent_name: str = Field(..., min_length=1, max_length=255)
    category: str = Field(default="Umum", max_length=50)
    description: str | None = Field(default=None, max_length=2000)
    key_label: str = Field(default="API Key", max_length=100)
    api_key: str | None = Field(default=None, max_length=2000)
    model: str | None = Field(default=None, max_length=255)


class UpdateCustomAgentRequest(BaseModel):
    api_key: str | None = Field(default=None, max_length=2000)
    model: str | None = Field(default=None, max_length=255)


@router.get("", response_model=dict)
async def list_agents(_admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Daftar SEMUA agent (dikelompokkan), masing2 key sudah masked."""
    agents = await service.list_agents(db)
    return build_success_response({"agents": agents})


@router.post("", response_model=dict)
async def add_custom_agent(
    body: AddCustomAgentRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Registrasi agent BARU lewat form -- CATATAN: ini murni menyimpan
    metadata (nama/key/model), TIDAK otomatis membuat kode scraping baru.
    Agent baru genuinely aktif tetap butuh kode (lihat pola 6-lapis)."""
    entry = await service.add_custom_agent(
        db, body.agent_name, body.category, body.description,
        body.key_label, body.api_key, body.model,
    )
    return build_success_response({"id": str(entry.id), "agent_name": entry.agent_name})


@router.patch("/{entry_id}", response_model=dict)
async def update_custom_agent(
    entry_id: uuid.UUID,
    body: UpdateCustomAgentRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Ganti key/model baris CUSTOM saja (linked_credential_id NULL) --
    baris yang linked ke credential existing HARUS diganti lewat
    /api/v1/credentials/{id}, endpoint ini akan menolak (404)."""
    entry = await service.update_custom_agent_key(db, entry_id, body.api_key, body.model)
    if not entry:
        raise NotFoundError("Agent registry entry tidak ditemukan atau bukan entry custom (pakai /api/v1/credentials utk yang linked)")
    return build_success_response({"id": str(entry.id), "updated": True})


@router.delete("/{entry_id}", response_model=dict)
async def delete_agent(
    entry_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    ok = await service.delete_agent_entry(db, entry_id)
    if not ok:
        raise NotFoundError(f"Agent registry entry {entry_id} tidak ditemukan")
    return build_success_response({"deleted": True})
