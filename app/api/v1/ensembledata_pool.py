"""
Pool token EnsembleData + auto-rotasi -- 2026-07-20, permintaan user
("ensemble kita buat seperti apify juga"). Pola SAMA PERSIS dgn
/api/v1/apify_pool.py, TANPA tracking dollar (EnsembleData tidak expose
API pemakaian spt Apify) -- cuma status exhausted. Admin-only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.domain.users.models import User
from app.services.auth.dependencies import require_admin
from app.services.ensembledata_pool import config as pool_cfg
from app.shared.exceptions import ValidationError
from app.shared.utils import build_success_response

router = APIRouter(prefix="/ensembledata-pool", tags=["ensembledata-pool"])


class EnsembleDataTokenRequest(BaseModel):
    token: str = Field(..., description="EnsembleData API token")


class EnsembleDataTokenRemoveRequest(BaseModel):
    index: int = Field(..., ge=0, description="Posisi token di pool (lihat GET /ensembledata-pool utk urutannya)")


@router.get("", response_model=dict)
async def list_ensembledata_pool(_admin: User = Depends(require_admin)):
    """Daftar SEMUA token EnsembleData di pool (masked) + status exhausted
    (histori error kita, kuota HARIAN -- reset TTL 20 jam). Kalau pool
    kosong, jatuh ke ENSEMBLE_DATA_API_TOKEN .env (satu token, TANPA rotasi)."""
    status = await pool_cfg.get_pool_status()
    return build_success_response({
        "pool_size": len(status),
        "tokens": status,
        "note": "Pool kosong -> jatuh ke ENSEMBLE_DATA_API_TOKEN .env (satu token, TANPA rotasi)." if not status else None,
    })


@router.post("", response_model=dict)
async def add_ensembledata_token(body: EnsembleDataTokenRequest, _admin: User = Depends(require_admin)):
    """Tambah SATU token ke pool -- efek langsung aktif, tanpa restart."""
    try:
        pool = await pool_cfg.add_token(body.token)
    except ValueError as exc:
        raise ValidationError(str(exc))
    return build_success_response({"pool_size": len(pool)})


@router.post("/remove", response_model=dict)
async def remove_ensembledata_token(body: EnsembleDataTokenRemoveRequest, _admin: User = Depends(require_admin)):
    """Hapus SATU token dari pool by POSISI (index)."""
    try:
        pool = await pool_cfg.remove_token_at_index(body.index)
    except ValueError as exc:
        raise ValidationError(str(exc))
    return build_success_response({"pool_size": len(pool)})


@router.post("/reset", response_model=dict)
async def reset_ensembledata_pool(_admin: User = Depends(require_admin)):
    """Reset SEMUA tanda 'exhausted' sekarang juga (jangan tunggu TTL 20 jam)."""
    reset_count = await pool_cfg.reset_all_exhausted()
    return build_success_response({"reset_count": reset_count})
