"""
Pool token Apify LINTAS PLATFORM (Facebook/Instagram/TikTok/Twitter) --
permintaan user 2026-07-20: "auto switch kalau kuota habis, sy siapkan 5
akun". Pola SAMA PERSIS dengan pool Firecrawl News
(app/services/news/config.py) yang sudah terbukti jalan -- pool disimpan
sbg JSON list di SATU Redis key, token exhausted ditandai via key terpisah
BER-TTL (auto-pulih tanpa reset manual, tapi tidak dihajar ulang tiap
panggilan dlm window itu).

BEDA dari pool Firecrawl: Apify punya API resmi
(`GET /v2/users/me/usage/monthly`) yang kasih pemakaian DOLLAR RIIL per
akun -- dipakai `get_usage()` di bawah supaya dashboard bisa tampilkan
"$X dari $5 (Y%)" per token, BUKAN cuma status biner exhausted/tidak
(permintaan eksplisit user: "memastikan batas kuota sebelum kita ganti").

Kalau pool KOSONG SAMA SEKALI: fallback ke `settings.apify_api_token`
(satu token, TANPA rotasi) -- backward-compatible, TIDAK breaking server
yang belum sempat isi pool (lihat app/integrations/apify/rotation.py).
"""
from __future__ import annotations

import json

from app.infrastructure.redis.connection import get_redis

_KEY_POOL = "apify_pool:tokens"
_EXHAUSTED_PREFIX = "apify_pool:exhausted:"
_EXHAUSTED_TTL_SECONDS = 6 * 60 * 60  # 6 jam -- sama alasan dgn pool Firecrawl (quota Apify riil resetnya bulanan, TTL ini cuma "jangan coba lagi terlalu sering")


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


async def remove_token(token: str) -> list[str]:
    token = token.strip()
    pool = [t for t in await get_pool() if t != token]
    await _set_pool(pool)
    await unmark_exhausted(token)
    return pool


async def remove_token_at_index(index: int) -> list[str]:
    """Hapus by POSISI di pool -- dashboard TIDAK PERNAH punya nilai token
    lengkap balik (cuma masked, keamanan), jadi hapus by index jauh lebih
    simpel drpd minta user tempel ulang token lengkap tiap kali mau hapus."""
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
    """Admin manual reset -- dipanggil dari dashboard begitu tau kuota
    bulanan Apify baru reset, tidak perlu nunggu TTL 6 jam."""
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


async def get_usage(token: str) -> dict:
    """Cek pemakaian DOLLAR RIIL bulan ini utk SATU token, langsung dari API
    resmi Apify -- supaya dashboard bisa tampilkan seberapa dekat token ini
    ke batas $5/bulan (plan FREE) SEBELUM benar2 exhausted, bukan cuma
    tebak dari histori error. Gagal panggil (network/token invalid) TIDAK
    raise -- return dict dgn `checked=False` supaya 1 token bermasalah tidak
    bikin seluruh dashboard pool gagal tampil."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            user_resp = await client.get(
                "https://api.apify.com/v2/users/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            user_resp.raise_for_status()
            plan = user_resp.json()["data"].get("plan", {})

            usage_resp = await client.get(
                "https://api.apify.com/v2/users/me/usage/monthly",
                headers={"Authorization": f"Bearer {token}"},
            )
            usage_resp.raise_for_status()
            usage_data = usage_resp.json()["data"]
    except Exception as exc:
        return {"checked": False, "message": f"Gagal cek pemakaian: {exc}"}

    total_usd = sum(v.get("amountAfterVolumeDiscountUsd", 0) for v in usage_data.get("monthlyServiceUsage", {}).values())
    limit_usd = plan.get("maxMonthlyUsageUsd")
    return {
        "checked": True,
        "plan": plan.get("id"),
        "used_usd": round(total_usd, 4),
        "limit_usd": limit_usd,
        "percent_used": round(total_usd / limit_usd * 100, 1) if limit_usd else None,
        "cycle_start": usage_data.get("usageCycle", {}).get("startAt"),
        "cycle_end": usage_data.get("usageCycle", {}).get("endAt"),
    }


async def get_pool_status() -> list[dict]:
    """Status LENGKAP tiap token di pool -- masked value + exhausted flag
    (dari histori error kita sendiri) + pemakaian real (langsung dari API
    Apify). Dipanggil dashboard, BUKAN hot path scraping -- boleh agak
    lambat (N panggilan API Apify sekaligus, N = jumlah token di pool)."""
    pool = await get_pool()
    result = []
    for token in pool:
        usage = await get_usage(token)
        result.append({
            "masked": mask_token(token),
            "exhausted_flag": await is_exhausted(token),
            "usage": usage,
        })
    return result
