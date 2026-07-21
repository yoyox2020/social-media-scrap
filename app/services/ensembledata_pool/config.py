"""
Pool token EnsembleData -- 2026-07-20, permintaan user ("mereka dibuatkan
antrian kan baik apify ataupun ensemble, ensemble kita buat seperti apify
juga"): SAMA PERSIS pola dgn app/services/apify_pool/config.py, tapi TANPA
tracking dollar real (EnsembleData tidak expose API pemakaian spt Apify) --
cuma status exhausted (dari histori error kita sendiri).

Kuota EnsembleData TERBUKTI harian (pesan error "Maximum requests limit
reached for today", reset ~tiap hari) -- TTL exhausted 20 jam (bukan 6 jam
spt Firecrawl/Apify yg kuotanya bulanan) supaya tidak coba lagi TERLALU
sering dlm hari yg sama, tapi tetap longgar drpd 24 jam penuh (jaga2 kalau
reset lebih awal dari perkiraan).

Dipakai TRANSPARAN oleh app/integrations/ensemble_data/client.py -- SEMUA
6 titik panggilan EnsembleData di project ini (Threads/Instagram/YouTube
fallback/viral_tracking/collector) OTOMATIS ikut rotasi TANPA perlu ubah
kode masing2, krn semua cuma instantiate `EnsembleDataClient()` polos
(token diambil dari pool di dalam client, bukan di titik panggilan).
"""
from __future__ import annotations

import json

from app.infrastructure.redis.connection import get_redis

_KEY_POOL = "ensembledata_pool:tokens"
_EXHAUSTED_PREFIX = "ensembledata_pool:exhausted:"
_EXHAUSTED_TTL_SECONDS = 20 * 60 * 60  # 20 jam -- lihat catatan kuota HARIAN di atas


async def get_pool() -> list[str]:
    redis = await get_redis()
    raw = await redis.get(_KEY_POOL)
    if not raw:
        return []
    return json.loads(raw)


async def _set_pool(tokens: list[str]) -> None:
    redis = await get_redis()
    await redis.set(_KEY_POOL, json.dumps(tokens))


async def add_token(token: str) -> list[str]:
    token = token.strip()
    if not token:
        raise ValueError("token tidak boleh kosong")
    pool = await get_pool()
    if token not in pool:
        pool.append(token)
        await _set_pool(pool)
    return pool


async def remove_token_at_index(index: int) -> list[str]:
    """Hapus by POSISI -- pola sama dgn apify_pool (dashboard tidak pernah
    dapat nilai token lengkap balik, cuma masked, keamanan)."""
    pool = await get_pool()
    if index < 0 or index >= len(pool):
        raise ValueError(f"Index {index} di luar jangkauan (pool berisi {len(pool)} token)")
    removed = pool.pop(index)
    await _set_pool(pool)
    await unmark_exhausted(removed)
    return pool


async def is_exhausted(token: str) -> bool:
    redis = await get_redis()
    return bool(await redis.get(_EXHAUSTED_PREFIX + token))


async def mark_exhausted(token: str) -> None:
    redis = await get_redis()
    await redis.set(_EXHAUSTED_PREFIX + token, "1", ex=_EXHAUSTED_TTL_SECONDS)


async def unmark_exhausted(token: str) -> None:
    redis = await get_redis()
    await redis.delete(_EXHAUSTED_PREFIX + token)


async def reset_all_exhausted() -> int:
    pool = await get_pool()
    if not pool:
        return 0
    redis = await get_redis()
    keys_to_check = [_EXHAUSTED_PREFIX + t for t in pool]
    existing = [t for t, v in zip(pool, await redis.mget(keys_to_check)) if v]
    if existing:
        await redis.delete(*[_EXHAUSTED_PREFIX + t for t in existing])
    return len(existing)


def mask_token(token: str) -> str:
    if len(token) <= 4:
        return "*" * len(token)
    return "*" * (len(token) - 4) + token[-4:]


async def get_pool_status() -> list[dict]:
    pool = await get_pool()
    return [
        {"masked": mask_token(t), "exhausted": await is_exhausted(t)}
        for t in pool
    ]
