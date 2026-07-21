"""
Pool token Apify LINTAS PLATFORM (Facebook/Instagram/TikTok/Twitter) + auto-
rotasi -- permintaan user 2026-07-20: "auto switch kalau kuota habis, sy
siapkan 5 akun, pastikan mekanisme rotasi dan kepastian data di-scrape
jelas". Admin-only (credential sensitif). TERPISAH dari /api/v1/credentials
(kelola SATU value) krn ini konsep POOL (banyak token + status per-token),
pola SAMA dgn /api/v1/news firecrawl-keys.

BEDA dari pool Firecrawl: endpoint GET di sini SEKALIGUS tampilkan pemakaian
DOLLAR RIIL per token (langsung dari API Apify, `$X dari $5 (Y%)`) -- bukan
cuma status biner exhausted/tidak -- supaya user bisa "memastikan batas
kuota sebelum kita ganti" (permintaan eksplisit).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.domain.users.models import User
from app.services.apify_pool import config as pool_cfg
from app.services.auth.dependencies import require_admin
from app.shared.exceptions import ValidationError
from app.shared.utils import build_success_response

router = APIRouter(prefix="/apify-pool", tags=["apify-pool"])


class ApifyTokenRequest(BaseModel):
    token: str = Field(..., description="Apify API token")


class ApifyTokenRemoveRequest(BaseModel):
    index: int = Field(..., ge=0, description="Posisi token di pool (lihat GET /apify-pool utk urutannya)")


@router.get("", response_model=dict)
async def list_apify_pool(_admin: User = Depends(require_admin)):
    """Daftar SEMUA token Apify di pool (masked) + status exhausted (histori
    error kita) + pemakaian dollar RIIL bulan ini per token (langsung dari
    API Apify: used_usd/limit_usd/percent_used/cycle_start/cycle_end).
    Kalau pool kosong, SEMUA platform Apify jatuh ke APIFY_API_TOKEN .env
    (satu token, TANPA rotasi) -- lihat /api/v1/credentials utk itu."""
    status = await pool_cfg.get_pool_status()
    return build_success_response({
        "pool_size": len(status),
        "tokens": status,
        "note": "Pool kosong -> semua platform Apify jatuh ke APIFY_API_TOKEN .env (satu token, TANPA rotasi)." if not status else None,
    })


@router.post("", response_model=dict)
async def add_apify_token(body: ApifyTokenRequest, _admin: User = Depends(require_admin)):
    """Tambah SATU token ke pool -- efek langsung aktif (dipakai run
    berikutnya), tanpa restart. Ulangi panggilan ini utk isi sampai 5 token."""
    try:
        pool = await pool_cfg.add_token(body.token)
    except ValueError as exc:
        raise ValidationError(str(exc))
    return build_success_response({"pool_size": len(pool)})


@router.post("/remove", response_model=dict)
async def remove_apify_token(body: ApifyTokenRemoveRequest, _admin: User = Depends(require_admin)):
    """Hapus SATU token dari pool by POSISI (index, lihat urutan di
    GET /apify-pool) -- BUKAN by nilai token, krn dashboard tidak pernah
    dapat balik nilai lengkap (cuma masked, keamanan)."""
    try:
        pool = await pool_cfg.remove_token_at_index(body.index)
    except ValueError as exc:
        raise ValidationError(str(exc))
    return build_success_response({"pool_size": len(pool)})


@router.post("/reset", response_model=dict)
async def reset_apify_pool(_admin: User = Depends(require_admin)):
    """Reset SEMUA tanda 'exhausted' sekarang juga (jangan tunggu TTL 6 jam)
    -- pakai kalau tau quota bulanan token tertentu baru saja reset/upgrade."""
    reset_count = await pool_cfg.reset_all_exhausted()
    return build_success_response({"reset_count": reset_count})
