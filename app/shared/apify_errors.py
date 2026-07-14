"""
Deteksi error kuota/rate-limit Apify -- dipakai lintas platform (Facebook,
TikTok, dll, siapapun yang lewat provider Apify) supaya kegagalan SEMENTARA
(kuota/kredit Apify habis, rate limit) TIDAK ikut dihitung sebagai kegagalan
PERMANEN topik oleh
app.services.trend_recommendations.service.mark_failed_permanent_if_exhausted().
Tanpa ini, topik yang genuinely valid bisa mati permanen cuma karena akun
Apify kita kehabisan kredit beberapa hari berturut-turut.

Cara deteksi (dua lapis, dari yang paling akurat):
  1. Structured: kalau exception adalah ApifyApiError (atau subclass-nya,
     misal RateLimitError) dari `apify_client`, package itu SUDAH taruh HTTP
     status code asli dari Apify di attribute `.status_code`. 402=payment
     required (kuota/kredit habis), 429=rate limit. Dicek via getattr (duck
     typing) supaya modul ini TIDAK perlu import apify_client -- kalau
     exception-nya sudah kadung dibungkus jadi ExternalAPIError kita sendiri
     (lihat app/shared/exceptions.py), `.status_code`-nya SELALU 502 (bukan
     kode asli Apify), jadi otomatis tidak match sini -- itu benar, karena di
     titik itu kode asli sudah hilang.
  2. Fallback teks: cocokkan pesan error (str exception atau string apapun)
     ke kata kunci umum kuota/rate-limit. Dipakai kalau exception aslinya
     sudah tidak tersedia lagi (misal cuma tersisa str-nya).

Trade-off yang disadari: lapis 2 berbasis pencocokan teks, bukan structured
error code -- kalau Apify ubah format pesannya, deteksi lapis 2 bisa diam-diam
berhenti bekerja. Lapis 1 (status_code) tidak kena masalah ini selama
`apify_client` tetap isi `.status_code` dari response asli.
"""
from __future__ import annotations

_QUOTA_STATUS_CODES = {402, 429}

_QUOTA_TEXT_SIGNALS = (
    "insufficient", "credit", "quota", "usage-hard-limit", "usage hard limit",
    "rate limit", "rate-limit", "monthly usage", "too many requests",
)

# Prefix dipakai buat nandain ScrapeRun.error_message supaya bisa di-exclude
# dari hitungan failed_permanent TANPA nambah kolom/status baru di skema.
QUOTA_ERROR_PREFIX = "[QUOTA]"


def is_quota_error(exc: BaseException | None = None, message: str | None = None) -> bool:
    """True kalau exception/pesan ini kelihatan seperti kuota/rate-limit Apify habis."""
    status_code = getattr(exc, "status_code", None)
    if status_code in _QUOTA_STATUS_CODES:
        return True

    text = (message or (str(exc) if exc else "")).lower()
    return any(signal in text for signal in _QUOTA_TEXT_SIGNALS)


def tag_if_quota_error(message: str, exc: BaseException | None = None) -> str:
    """Prefix `message` dengan QUOTA_ERROR_PREFIX kalau ini kelihatan kegagalan
    kuota/rate-limit Apify -- dipakai saat menyusun ScrapeRun.error_message."""
    if not message or not is_quota_error(exc=exc, message=message):
        return message
    return f"{QUOTA_ERROR_PREFIX} {message}"


async def get_apify_account_status() -> dict:
    """
    Cek status akun Apify LANGSUNG dari API resminya (`GET /v2/users/me`) --
    BEDA dari is_quota_error() di atas yang cuma menebak dari teks error
    SETELAH sebuah panggilan gagal. Ini cek proaktif "apakah kuota Apify
    genuinely habis SEKARANG", dipakai dashboard /scraping-status supaya
    user tahu penyebab data Facebook/Instagram/TikTok/Twitter kosong itu
    kuota habis, bukan "topiknya memang tidak ada di sana".

    `effectivePlatformFeatures.ACTORS.isEnabled == false` dgn
    `disabledReasonType == "MONTHLY_TOTAL_USAGE_HARD_LIMIT_EXCEEDED"` berarti
    SEMUA actor run (scraping apa pun lewat Apify) akan gagal sampai kuota
    bulanan reset atau plan di-upgrade -- ini status paling akurat yang bisa
    dicek tanpa menunggu sebuah scrape run gagal duluan.
    """
    import httpx

    from app.shared.config import settings

    if not settings.apify_api_token:
        return {"checked": False, "exhausted": False, "message": "APIFY_API_TOKEN belum di-set"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.apify.com/v2/users/me",
                params={"token": settings.apify_api_token},
            )
            resp.raise_for_status()
            data = resp.json()["data"]
    except Exception as exc:
        return {"checked": False, "exhausted": False, "message": f"Gagal cek status Apify: {exc}"}

    plan = data.get("plan", {})
    actors_feature = (data.get("effectivePlatformFeatures") or {}).get("ACTORS", {})
    exhausted = not actors_feature.get("isEnabled", True)

    return {
        "checked": True,
        "exhausted": exhausted,
        "plan": plan.get("id"),
        "monthly_limit_usd": plan.get("maxMonthlyUsageUsd"),
        "message": (
            f"Kuota Apify ({plan.get('id', '?')}, ${plan.get('maxMonthlyUsageUsd', '?')}/bulan) "
            f"SUDAH HABIS -- {actors_feature.get('disabledReason', 'semua actor run akan gagal')}. "
            "Facebook/Instagram/TikTok/Twitter/Smart Search tier-3 tidak akan dapat data baru "
            "sampai kuota reset bulan depan atau plan di-upgrade."
            if exhausted else
            f"Kuota Apify ({plan.get('id', '?')}) masih tersedia."
        ),
    }
