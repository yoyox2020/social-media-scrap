"""
Registry provider Facebook + fungsi fallback berurutan — pola identik dengan
app/services/instagram/providers/registry.py.

Provider aktif sekarang cuma Apify. Meta Graph API resmi SENGAJA TIDAK
dimasukkan di sini karena terbukti live (lihat
docs/flow scrape/flow-scrap-facebook.md, 05 Juli 2026) cuma bisa akses Page
yang dikelola sendiri — tidak berguna untuk akun manapun yang ditemukan AI
discovery. Kalau nanti ada provider baru (app Meta Business terverifikasi,
atau pihak ketiga lain), tinggal:
  1. Buat class baru implement BaseFacebookSearchProvider
  2. Tambah 1 baris ke PROVIDERS dict di bawah
  3. Tambah namanya ke FACEBOOK_SEARCH_PROVIDER_ORDER di .env
Tidak ada perubahan di pipeline_service.py atau kode pemanggil manapun.
"""
from __future__ import annotations

import logging

from app.services.facebook.providers.apify_provider import ApifyFacebookProvider
from app.services.facebook.providers.base import BaseFacebookSearchProvider
from app.shared.config import settings
from app.shared.exceptions import ExternalAPIError

logger = logging.getLogger(__name__)

PROVIDERS: dict[str, type[BaseFacebookSearchProvider]] = {
    "apify": ApifyFacebookProvider,
    # slot untuk provider berikutnya — tambah di sini + FACEBOOK_SEARCH_PROVIDER_ORDER
}


async def search_profile_with_fallback(
    identifier: str, max_posts: int, max_comments: int
) -> tuple[list[dict], str]:
    order = [p.strip() for p in settings.facebook_search_provider_order.split(",") if p.strip()]
    last_exc: Exception | None = None
    for provider_name in order:
        provider_cls = PROVIDERS.get(provider_name)
        if not provider_cls:
            logger.warning("[FacebookProvider] provider tidak dikenal di PROVIDERS: %s", provider_name)
            continue
        try:
            rows = await provider_cls().search_profile(identifier, max_posts, max_comments)
            return rows, provider_name
        except Exception as exc:
            logger.warning("[FacebookProvider] %s gagal untuk %s: %s", provider_name, identifier, exc)
            last_exc = exc
    raise last_exc or ExternalAPIError(service="Facebook", message="Semua provider Facebook gagal")
