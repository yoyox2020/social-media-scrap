"""
Konfigurasi pengiriman notifikasi topik viral ke WhatsApp (via Fonnte) --
SEMUA di Redis (bukan .env), pola SAMA PERSIS dgn agent config lain di
project ini (mis. app/services/youtube_discovery/config.py): efeknya
LANGSUNG aktif di run berikutnya, tanpa restart. Diatur dari halaman
"Kelola API Key" (token) + endpoint khusus di bawah (nomor tujuan).

Nomor tujuan disimpan sbg satu string dipisah koma (format Fonnte:
"6281234567890,6289876543210") -- SATU pesan bisa dikirim ke banyak nomor
sekaligus dlm satu panggilan API.
"""
from __future__ import annotations

from app.infrastructure.redis.connection import get_redis

_KEY_FONNTE_TOKEN = "whatsapp_notify:fonnte_token"
_KEY_TARGET_NUMBERS = "whatsapp_notify:target_numbers"

FONNTE_SEND_URL = "https://api.fonnte.com/send"


async def get_fonnte_token() -> str | None:
    redis = await get_redis()
    raw = await redis.get(_KEY_FONNTE_TOKEN)
    if raw is None:
        return None
    return raw if isinstance(raw, str) else raw.decode()


async def set_fonnte_token(token: str) -> None:
    token = token.strip()
    if not token:
        raise ValueError("token tidak boleh kosong")
    redis = await get_redis()
    await redis.set(_KEY_FONNTE_TOKEN, token)


async def get_target_numbers() -> list[str]:
    redis = await get_redis()
    raw = await redis.get(_KEY_TARGET_NUMBERS)
    if raw is None:
        return []
    val = raw if isinstance(raw, str) else raw.decode()
    return [n.strip() for n in val.split(",") if n.strip()]


async def set_target_numbers(numbers: str) -> list[str]:
    """`numbers` string dipisah koma, mis. "6281234567890,6289876543210" --
    divalidasi tidak kosong, whitespace di tiap nomor dibuang."""
    parsed = [n.strip() for n in numbers.split(",") if n.strip()]
    if not parsed:
        raise ValueError("Minimal 1 nomor tujuan (pisahkan dgn koma kalau lebih dari 1)")
    redis = await get_redis()
    await redis.set(_KEY_TARGET_NUMBERS, ",".join(parsed))
    return parsed


async def is_configured() -> bool:
    """True kalau token DAN minimal 1 nomor tujuan sudah diatur -- dipakai
    notification_service.py utk skip pengiriman diam-diam (tanpa error)
    kalau belum di-setup, drpd task gagal krn WA belum dikonfigurasi."""
    token = await get_fonnte_token()
    numbers = await get_target_numbers()
    return bool(token) and bool(numbers)
