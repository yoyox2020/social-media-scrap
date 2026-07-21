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
from app.services.agent_registry import rotation
from app.shared.exceptions import NotFoundError, ValidationError
from app.shared.utils import build_success_response
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/agent-registry", tags=["agent-registry"])


def _mask(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


def _pool_key_to_dict(k) -> dict:
    return {
        "id": str(k.id),
        "masked_key": _mask(k.api_key),
        "model": k.model,
        "account_email": k.account_email,
        "priority": k.priority,
        "status": k.status,
        "exhausted_until": k.exhausted_until.isoformat() if k.exhausted_until else None,
        "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        "last_error": k.last_error,
    }


class AddPoolKeyRequest(BaseModel):
    api_key: str = Field(..., min_length=1, max_length=2000)
    model: str | None = Field(default=None, max_length=255)
    account_email: str | None = Field(default=None, max_length=255)
    priority: int = Field(default=0, ge=0, le=100)


class AddCustomAgentRequest(BaseModel):
    agent_name: str = Field(..., min_length=1, max_length=255)
    category: str = Field(default="Umum", max_length=50)
    description: str | None = Field(default=None, max_length=2000)
    key_label: str = Field(default="API Key", max_length=100)
    api_key: str | None = Field(default=None, max_length=2000)
    model: str | None = Field(default=None, max_length=255)
    account_email: str | None = Field(default=None, max_length=255)


class UpdateCustomAgentRequest(BaseModel):
    api_key: str | None = Field(default=None, max_length=2000)
    model: str | None = Field(default=None, max_length=255)
    account_email: str | None = Field(default=None, max_length=255)


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
        body.key_label, body.api_key, body.model, body.account_email,
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
    entry = await service.update_custom_agent_key(db, entry_id, body.api_key, body.model, body.account_email)
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


# ── Pool rotasi API key per agent (2026-07-22) ────────────────────────────────
# Beda dari CRUD di atas (agent_registry = identitas+key "aktif saat ini"):
# ini pool KANDIDAT key, bisa lebih dari 1 per agent, dgn rotasi otomatis
# saat exhausted + reset manual. Lihat app/services/agent_registry/rotation.py.

@router.get("/{agent_name}/pool", response_model=dict)
async def get_agent_pool(
    agent_name: str,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Daftar semua key kandidat di pool rotasi agent ini + status masing2."""
    keys = await rotation.list_pool(db, agent_name)
    active = await rotation.get_active_key(db, agent_name)
    return build_success_response({
        "agent_name": agent_name,
        "active_key_id": str(active.id) if active else None,
        "keys": [_pool_key_to_dict(k) for k in keys],
    })


@router.post("/{agent_name}/pool", response_model=dict)
async def add_agent_pool_key(
    agent_name: str,
    body: AddPoolKeyRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Tambah 1 key kandidat baru ke pool rotasi agent ini."""
    key = await rotation.add_key(db, agent_name, body.api_key, body.model, body.account_email, body.priority)
    return build_success_response({"id": str(key.id), "agent_name": agent_name})


@router.post("/{agent_name}/pool/rotate", response_model=dict)
async def rotate_agent_pool(
    agent_name: str,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Paksa ganti key SEKARANG (mis. mau coba API baru) -- key yg sedang
    aktif ditandai exhausted sementara (5 menit), lalu balikin key
    berikutnya yg tersedia di pool."""
    new_key = await rotation.rotate_now(db, agent_name)
    if not new_key:
        raise NotFoundError(f"Tidak ada key lain yg tersedia di pool utk '{agent_name}'")
    return build_success_response({"active_key_id": str(new_key.id), "masked_key": _mask(new_key.api_key)})


@router.post("/pool-key/{key_id}/reset", response_model=dict)
async def reset_pool_key(
    key_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Kembalikan 1 key (exhausted/disabled) jadi aktif lagi."""
    key = await rotation.reset_key(db, key_id)
    if not key:
        raise NotFoundError(f"Key pool {key_id} tidak ditemukan")
    return build_success_response({"id": str(key.id), "status": key.status})


@router.post("/pool-key/{key_id}/disable", response_model=dict)
async def disable_pool_key(
    key_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Matikan 1 key PERMANEN (tidak auto-pulih spt exhausted biasa)."""
    key = await rotation.mark_disabled(db, key_id, reason="Dimatikan manual oleh user")
    if not key:
        raise NotFoundError(f"Key pool {key_id} tidak ditemukan")
    return build_success_response({"id": str(key.id), "status": key.status})


@router.delete("/pool-key/{key_id}", response_model=dict)
async def delete_pool_key(
    key_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    ok = await rotation.remove_key(db, key_id)
    if not ok:
        raise NotFoundError(f"Key pool {key_id} tidak ditemukan")
    return build_success_response({"deleted": True})
