"""
Pool key Firecrawl KHUSUS News (permintaan user 2026-07-19: "ganti key
firecrawl untuk news + auto switch kalau kuota habis, minimal 5 key
firecrawl bisa dipakai"). SENGAJA TERPISAH dari `settings.firecrawl_api_key`
(dipakai bersama AI viral discovery provider Ollama, lihat
app/ai/llm/viral_discovery_service.py::_web_search()) -- supaya rotasi
key News TIDAK mempengaruhi/dipengaruhi pemakaian Firecrawl di tempat lain.

Desain:
- Pool disimpan sbg JSON list di SATU Redis key (bukan Redis LIST native)
  -- gampang re-baca/ubah utuh, jumlah key kecil (<=~10) jadi tidak masalah.
- Key yg kena quota/rate-limit (429/402) ditandai "exhausted" via Redis key
  TERPISAH per-key DENGAN TTL (bukan dihapus dari pool) -- otomatis "pulih"
  sendiri setelah beberapa jam TANPA perlu admin reset manual tiap kali,
  tapi juga TIDAK menghajar ulang key yg BARU SAJA diketahui habis di
  setiap panggilan berikutnya dalam window itu (asumsi konservatif, quota
  Firecrawl real resetnya per-bulan/billing-cycle, bukan per-menit -- TTL
  di sini cuma soal "jangan coba lagi TERLALU sering", bukan tebakan pasti
  kapan quota beneran reset).
- Kalau pool KOSONG SAMA SEKALI: fallback ke `settings.firecrawl_api_key`
  (satu key, TANPA rotasi) di app/integrations/firecrawl/news.py --
  backward-compatible, tidak breaking server yg belum sempat isi pool.
"""
from __future__ import annotations

import json

from app.infrastructure.redis.connection import get_redis

_KEY_POOL = "news_firecrawl:keys"
_EXHAUSTED_PREFIX = "news_firecrawl:exhausted:"
_EXHAUSTED_TTL_SECONDS = 6 * 60 * 60  # 6 jam -- lihat catatan desain di atas


async def get_pool() -> list[str]:
    redis = await get_redis()
    raw = await redis.get(_KEY_POOL)
    if not raw:
        return []
    return json.loads(raw)


async def _set_pool(keys: list[str]) -> None:
    redis = await get_redis()
    await redis.set(_KEY_POOL, json.dumps(keys))


async def add_key(key: str) -> list[str]:
    key = key.strip()
    if not key:
        raise ValueError("key tidak boleh kosong")
    pool = await get_pool()
    if key not in pool:
        pool.append(key)
        await _set_pool(pool)
    return pool


async def remove_key(key: str) -> list[str]:
    key = key.strip()
    pool = [k for k in await get_pool() if k != key]
    await _set_pool(pool)
    await unmark_exhausted(key)
    return pool


async def is_exhausted(key: str) -> bool:
    redis = await get_redis()
    return bool(await redis.get(_EXHAUSTED_PREFIX + key))


async def mark_exhausted(key: str) -> None:
    redis = await get_redis()
    await redis.set(_EXHAUSTED_PREFIX + key, "1", ex=_EXHAUSTED_TTL_SECONDS)


async def unmark_exhausted(key: str) -> None:
    redis = await get_redis()
    await redis.delete(_EXHAUSTED_PREFIX + key)


async def reset_all_exhausted() -> int:
    """Admin manual reset -- hapus SEMUA tanda exhausted SEKARANG (dipanggil
    dari dashboard, mis. begitu tau quota bulanan Firecrawl baru reset,
    tidak perlu nunggu TTL 6 jam). Return jumlah key yg di-reset."""
    pool = await get_pool()
    if not pool:
        return 0
    redis = await get_redis()
    keys_to_check = [_EXHAUSTED_PREFIX + k for k in pool]
    existing = [k for k, v in zip(pool, await redis.mget(keys_to_check)) if v]
    if existing:
        await redis.delete(*[_EXHAUSTED_PREFIX + k for k in existing])
    return len(existing)


def mask_key(key: str) -> str:
    if len(key) <= 4:
        return "*" * len(key)
    return "*" * (len(key) - 4) + key[-4:]


async def get_pool_status() -> list[dict]:
    pool = await get_pool()
    return [
        {"masked": mask_key(k), "exhausted": await is_exhausted(k)}
        for k in pool
    ]
