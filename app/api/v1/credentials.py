"""
Endpoint terpusat "Kelola API Key" (permintaan user 2026-07-18) -- satu
halaman utk lihat+ganti SEMUA credential third-party yg dipakai project ini,
gantikan kebiasaan lama cari-cari tiap agent py tab terpisah. Admin-only
(credential sensitif, BEDA dari endpoint status/monitor publik agent
lain) -- lihat app/services/credentials/registry.py utk detail per-entry.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.domain.users.models import User
from app.services.auth.dependencies import require_admin
from app.services.credentials.registry import (
    ALL_ENTRIES,
    list_credentials,
    set_credential_value,
)
from app.shared.exceptions import NotFoundError, ValidationError
from app.shared.utils import build_success_response

router = APIRouter(prefix="/credentials", tags=["credentials"])


class CredentialUpdateRequest(BaseModel):
    value: str = Field(..., description="Nilai credential baru (API key/token)")


@router.get("", response_model=dict)
async def get_all_credentials(_admin: User = Depends(require_admin)):
    """Daftar SEMUA credential third-party (masked) + siapa yg memakainya +
    apakah efeknya langsung aktif tanpa restart."""
    items = await list_credentials()
    return build_success_response({"items": items})


@router.patch("/{credential_id}", response_model=dict)
async def update_credential(
    credential_id: str,
    body: CredentialUpdateRequest,
    _admin: User = Depends(require_admin),
):
    """Ganti SATU credential by id (lihat GET /credentials utk daftar id
    yg valid) -- efeknya langsung aktif di panggilan berikutnya, TIDAK
    perlu restart server."""
    entry = next((e for e in ALL_ENTRIES if e.id == credential_id), None)
    if not entry:
        raise NotFoundError(f"Credential id tidak ditemukan: {credential_id}")

    try:
        await set_credential_value(entry, body.value)
    except ValueError as exc:
        raise ValidationError(str(exc))

    from app.services.credentials.registry import get_credential_value, mask_value

    new_value = await get_credential_value(entry)
    return build_success_response({
        "id": entry.id,
        "label": entry.label,
        "masked_value": mask_value(new_value),
    })
