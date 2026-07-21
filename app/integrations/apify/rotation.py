"""
Caller Apify TERPUSAT dengan ROTASI OTOMATIS lintas token -- 2026-07-20,
permintaan user "auto switch kalau kuota habis, sy siapkan 5 akun". Pola
SAMA PERSIS dgn `_call_firecrawl_with_rotation()`
(app/integrations/firecrawl/news.py) yg sudah terbukti jalan utk News.

SATU tempat ini dipakai SEMUA integrasi Apify (Facebook/Instagram/TikTok/
Twitter, apa pun actor-nya) -- supaya rotasi token TIDAK perlu diimplementasi
ulang di tiap file integrasi. File integrasi platform TETAP yang tahu actor
ID + bentuk run_input masing2 (tidak berubah), cuma bagian "panggil Apify
+ tangani token"-nya yang dipusatkan di sini.

Deteksi kuota REUSE `app.shared.apify_errors.is_quota_error()` yang SUDAH
ada (dipakai `mark_failed_permanent_if_exhausted()` sebelumnya) -- SATU
sumber kebenaran definisi "ini kuota habis atau bukan", tidak duplikasi
daftar kata kunci di tempat lain.

Kalau pool KOSONG (belum pernah diisi via dashboard): fallback ke
`settings.apify_api_token` (satu token, TANPA rotasi) -- backward-compatible,
TIDAK breaking server yang belum sempat isi pool.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from apify_client import ApifyClient

from app.services.apify_pool import config as pool_cfg
from app.shared.apify_errors import is_quota_error
from app.shared.config import settings
from app.shared.exceptions import ExternalAPIError

logger = logging.getLogger(__name__)

# Dipanggil SETELAH dataset utama berhasil diambil, SELAGI `client` (dgn token
# yg SAMA sedang dipakai) masih di scope -- utk actor spt TikTok yg butuh
# panggilan dataset KEDUA (komentar per post) dgn client/token yg sama.
EnrichFn = Callable[[ApifyClient, list[dict[str, Any]]], list[dict[str, Any]]]


def _call_actor_sync(
    token: str, actor_id: str, run_input: dict[str, Any], enrich_fn: EnrichFn | None,
) -> list[dict[str, Any]]:
    client = ApifyClient(token)
    run = client.actor(actor_id).call(run_input=run_input)

    if run.get("status") != "SUCCEEDED":
        raise ExternalAPIError(service="Apify", message=f"Run status={run.get('status')} untuk actor={actor_id}")

    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    if enrich_fn:
        items = enrich_fn(client, items)
    return items


async def call_apify_actor(
    actor_id: str, run_input: dict[str, Any], enrich_fn: EnrichFn | None = None,
) -> list[dict[str, Any]]:
    """Panggil SATU actor Apify dgn ROTASI OTOMATIS antar token di pool --
    kalau token yg dipakai kena quota/rate-limit, tandai exhausted lalu
    coba token BERIKUTNYA, sampai berhasil atau SEMUA token di pool sudah
    dicoba. Token yg SUDAH diketahui exhausted (window TTL) dicoba PALING
    TERAKHIR, tapi TETAP dicoba kalau semua token "segar" gagal (jaga2
    kalau ternyata sudah pulih lebih cepat dari asumsi TTL kita).

    `enrich_fn` opsional -- dipanggil SEBELUM token/client dilepas, utk
    actor yg butuh panggilan dataset TAMBAHAN dgn client yg SAMA (mis.
    TikTok: komentar per post ada di dataset terpisah).

    Error BUKAN kuota (mis. actor gagal krn input salah/target tidak ada)
    TIDAK memicu rotasi -- langsung di-raise, ganti token tidak akan
    membantu utk error jenis itu, malah bisa menutupi bug asli.
    """
    import asyncio

    pool = await pool_cfg.get_pool()
    tokens_to_try = pool if pool else ([settings.apify_api_token] if settings.apify_api_token else [])
    if not tokens_to_try:
        raise ExternalAPIError(service="Apify", message="Belum ada Apify API token (pool kosong & APIFY_API_TOKEN .env jg kosong)")

    if pool:
        exhausted_flags = {t: await pool_cfg.is_exhausted(t) for t in tokens_to_try}
        ordered_tokens = sorted(tokens_to_try, key=lambda t: exhausted_flags[t])
    else:
        ordered_tokens = tokens_to_try

    last_exc: Exception | None = None
    for token in ordered_tokens:
        try:
            return await asyncio.to_thread(_call_actor_sync, token, actor_id, run_input, enrich_fn)
        except Exception as exc:
            if not is_quota_error(exc=exc):
                raise
            logger.warning(
                "[ApifyRotation] token %s kena quota (actor=%s) -- rotasi ke token berikutnya (pool=%d token)",
                pool_cfg.mask_token(token), actor_id, len(ordered_tokens),
            )
            if pool:
                await pool_cfg.mark_exhausted(token)
            last_exc = exc
            continue

    logger.error("[ApifyRotation] SEMUA %d token kena quota utk actor=%s -- request gagal total", len(ordered_tokens), actor_id)
    raise last_exc or ExternalAPIError(service="Apify", message="Semua token Apify kena quota")
