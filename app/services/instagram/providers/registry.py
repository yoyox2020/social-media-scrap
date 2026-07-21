"""
Registry provider pencarian Instagram — cari & scrape profil by username,
dengan auto-fallback antar provider sesuai urutan di
`settings.instagram_search_provider_order` (default:
"apify_post_scraper,apify,ensembledata" -- 2026-07-20, `apify_post_scraper`
jadi PRIMARY krn `apify` lama TERBUKTI tidak pernah kirim thumbnail sama
sekali, lihat apify_post_scraper_provider.py. `apify` lama TETAP dipertahankan
sbg fallback -- kalau `apify_post_scraper` gagal/kuota Apify habis, otomatis
jatuh ke provider berikutnya di urutan, TIDAK ada downtime, cuma thumbnail-nya
kosong lagi sementara spt sebelumnya).

Untuk ganti urutan/nonaktifkan provider: cukup ubah config, tidak perlu ubah
kode. Untuk tambah provider baru: buat class baru yang implement
BaseInstagramSearchProvider, daftarkan di PROVIDERS di bawah — tidak ada
tempat lain yang perlu diubah.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.instagram.providers.apify_post_scraper_provider import ApifyPostScraperInstagramProvider
from app.services.instagram.providers.apify_provider import ApifyInstagramProvider
from app.services.instagram.providers.base import BaseInstagramSearchProvider
from app.services.instagram.providers.ensemble_data_provider import EnsembleDataInstagramProvider

logger = logging.getLogger(__name__)

PROVIDERS: dict[str, type[BaseInstagramSearchProvider]] = {
    "apify_post_scraper": ApifyPostScraperInstagramProvider,
    "apify": ApifyInstagramProvider,
    "ensembledata": EnsembleDataInstagramProvider,
}


async def search_profile_with_fallback(
    username: str, max_posts: int, max_comments: int
) -> tuple[list[dict[str, Any]], str]:
    """
    Coba tiap provider di `settings.instagram_search_provider_order` (urutan
    dari kiri ke kanan), berhenti di provider pertama yang berhasil.
    Return (rows, nama_provider_yang_berhasil). Raise exception provider
    terakhir kalau semua gagal.
    """
    from app.shared.config import settings
    from app.shared.exceptions import ExternalAPIError

    order = [p.strip() for p in settings.instagram_search_provider_order.split(",") if p.strip()]
    last_exc: Exception | None = None

    for provider_name in order:
        provider_cls = PROVIDERS.get(provider_name)
        if provider_cls is None:
            logger.warning("[InstagramProvider] provider tidak dikenal: %s (skip)", provider_name)
            continue
        try:
            rows = await provider_cls().search_profile(username, max_posts, max_comments)
            return rows, provider_name
        except Exception as exc:
            logger.warning("[InstagramProvider] %s gagal untuk username=%s: %s", provider_name, username, exc)
            last_exc = exc

    raise last_exc or ExternalAPIError(service="Instagram", message="Semua provider pencarian gagal/tidak dikonfigurasi")
