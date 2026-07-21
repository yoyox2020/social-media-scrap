"""
Kirim pesan WhatsApp via Fonnte (https://fonnte.com) -- gateway WA
Indonesia, device dihubungkan lewat scan QR di dashboard Fonnte (di luar
scope kode ini), API-nya tinggal POST token+target+message.

GAGAL KIRIM (network/API error) TIDAK BOLEH bikin task notifikasi topik
gagal/rollback -- pengiriman WA ini best-effort SETELAH notifikasi
tersimpan di DB, jadi exception di sini SELALU ditangkap+dicatat log,
tidak pernah dilempar ke pemanggil (lihat notification_service.py).
"""
from __future__ import annotations

import logging

import httpx

from app.services.whatsapp_notify.config import (
    FONNTE_SEND_URL,
    get_fonnte_token,
    get_target_numbers,
    is_configured,
)

logger = logging.getLogger(__name__)


async def send_whatsapp_message(message: str) -> bool:
    """Kirim `message` ke SEMUA nomor tujuan yg dikonfigurasi (satu
    panggilan API, Fonnte terima banyak target dipisah koma). Return False
    (bukan raise) kalau belum dikonfigurasi ATAU pengiriman gagal --
    caller cukup log/abaikan, JANGAN pernah membuat task pemanggil ikut
    gagal gara-gara WA."""
    if not await is_configured():
        logger.info("send_whatsapp_message: belum dikonfigurasi (token/nomor kosong), skip")
        return False

    token = await get_fonnte_token()
    numbers = await get_target_numbers()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                FONNTE_SEND_URL,
                headers={"Authorization": token},
                data={"target": ",".join(numbers), "message": message},
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("status") is False:
                logger.warning("send_whatsapp_message: Fonnte menolak: %s", body)
                return False
            return True
    except Exception as exc:
        logger.warning("send_whatsapp_message: gagal kirim (%s)", exc)
        return False
