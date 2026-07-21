"""
Deteksi error kuota EnsembleData -- pola SAMA dgn app/shared/apify_errors.py
(dua provider berbeda, gejala kuota berbeda: EnsembleData HTTP 495
"Maximum requests limit reached for today", bukan 402/429 spt Apify).

SATU sumber kebenaran dipakai lintas: rotasi token
(app/integrations/ensemble_data/client.py), tagging quota utk
mark_failed_permanent_if_exhausted() (app/services/threads/pipeline_service.py),
dan kapan pun butuh deteksi ini ke depan.
"""
from __future__ import annotations

from app.shared.apify_errors import QUOTA_ERROR_PREFIX

# "492"/"not been verified" ditambahkan 2026-07-20 -- ditemukan live saat
# user tambah token EnsembleData baru ke pool: akun baru yg emailnya belum
# diverifikasi balikin HTTP 492 "Your email has not been verified.", BUKAN
# soal kuota tapi efeknya SAMA (token ini tidak akan berfungsi sampai
# diperbaiki di sisi EnsembleData) -- diperlakukan sama spt quota supaya
# token yg bermasalah ditandai exhausted (skip ke token lain di pool)
# drpd dicoba ulang terus tiap kali kebetulan kepilih random.
_QUOTA_TEXT_SIGNALS = ("495", "maximum requests limit", "quota", "rate limit", "492", "not been verified")


def is_quota_error(exc: BaseException | None = None, message: str | None = None) -> bool:
    """True kalau exception/pesan ini kelihatan seperti kuota EnsembleData habis."""
    text = (message or (str(exc) if exc else "")).lower()
    return any(signal in text for signal in _QUOTA_TEXT_SIGNALS)


def tag_if_quota_error(message: str, exc: BaseException | None = None) -> str:
    """Prefix `message` dgn QUOTA_ERROR_PREFIX (SAMA dgn Apify) kalau ini
    kelihatan kegagalan kuota EnsembleData."""
    if not message or not is_quota_error(exc=exc, message=message):
        return message
    return f"{QUOTA_ERROR_PREFIX} {message}"
