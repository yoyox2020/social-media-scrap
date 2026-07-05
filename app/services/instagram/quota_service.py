"""
Kuota harian untuk panggilan provider Instagram ad-hoc (manual, di luar
pipeline trend_recommendations yang punya budget sendiri & terpisah —
lihat app/services/instagram_trending/trend_scrape_service.py, TIDAK disentuh
di sini).

Tidak ada tabel ledger baru — dihitung ulang tiap kali dari `scrape_runs`,
mengikuti pola yang sudah ada (`ig_scraped_today` di monitor-public).
Pipeline trend_recommendations selalu menandai `triggered_by='celery_beat'`,
jadi query di sini otomatis mengecualikannya tanpa perlu tag baru.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.config import settings
from app.shared.exceptions import ExternalAPIError


async def get_usage_today(db: AsyncSession) -> dict[str, int]:
    row = (await db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE keyword_text LIKE 'search:%') AS search_used,
            COUNT(*) AS total_used
        FROM scrape_runs
        WHERE platform = 'instagram'
          AND triggered_by IN ('manual_api', 'manual_cli')
          AND started_at::date = CURRENT_DATE
    """))).mappings().first()
    return {
        "search_used": (row["search_used"] if row else 0) or 0,
        "total_used":  (row["total_used"] if row else 0) or 0,
    }


async def enforce_quota(db: AsyncSession, operation: str = "search") -> None:
    """
    Raise ExternalAPIError kalau kuota harian habis.

    - `operation="search"`: minimal `instagram_search_daily_min` panggilan
      search dijamin tersedia; setelah itu ikut kuota bersama
      `instagram_shared_daily_budget`.
    - operation lain: dibatasi sisa kuota bersama SETELAH floor search
      disisihkan (`instagram_shared_daily_budget - instagram_search_daily_min`).
    """
    usage = await get_usage_today(db)

    if operation == "search":
        if usage["search_used"] < settings.instagram_search_daily_min:
            return
        if usage["total_used"] < settings.instagram_shared_daily_budget:
            return
        raise ExternalAPIError(
            service="Instagram",
            message=(
                f"Kuota harian Instagram habis ({usage['total_used']}/"
                f"{settings.instagram_shared_daily_budget}). Coba lagi besok."
            ),
        )

    non_search_cap = settings.instagram_shared_daily_budget - settings.instagram_search_daily_min
    non_search_used = usage["total_used"] - usage["search_used"]
    if non_search_used < non_search_cap:
        return
    raise ExternalAPIError(
        service="Instagram",
        message=f"Kuota harian Instagram (non-search) habis ({non_search_used}/{non_search_cap}).",
    )
