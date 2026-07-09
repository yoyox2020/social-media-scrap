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
