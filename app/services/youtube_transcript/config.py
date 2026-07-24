"""Konfigurasi proxy Webshare utk YouTube Transcript Agent (2026-07-25)
-- kredensial di Redis (bisa diubah admin tanpa redeploy, pola SAMA dgn
config lain di project ini), BUKAN lewat third_party_apis (itu utk pool
KEY yg dirotasi antar-alternatif -- proxy Webshare "rotate" BEDA konsep,
rotasi IP-nya terjadi OTOMATIS di level jaringan per-request oleh
Webshare sendiri via 1 username+password "-rotate", bukan kita yg
gilir-gilir kredensial).

DITEMUKAN LIVE 2026-07-25 (penting utk siapa pun ubah config ini nanti):
- Endpoint LIST (cek bahasa apa saja yg tersedia) jalan lancar walau
  TANPA proxy sekalipun (server kita, cloud IP, TIDAK diblokir YouTube
  utk endpoint ini).
- Endpoint FETCH (ambil ISI teks asli) 100% diblokir tanpa proxy
  (`RequestBlocked`, YouTube blokir cloud/datacenter IP utk endpoint ini
  spesifik) -- WAJIB proxy.
- Username BIASA (`xjflncma`) vs username ROTATE (`xjflncma-rotate`,
  suffix wajib dari Webshare) HASILNYA BEDA -- yg TANPA "-rotate" gagal
  konsisten (429/407), yg PAKAI "-rotate" berhasil konsisten. WAJIB pakai
  suffix ini."""
from __future__ import annotations

from app.infrastructure.redis.connection import get_redis

_KEY_PROXY_USERNAME = "youtube_transcript:proxy_username"
_KEY_PROXY_PASSWORD = "youtube_transcript:proxy_password"
_KEY_BATCH_SIZE = "youtube_transcript:batch_size"

DEFAULT_BATCH_SIZE = 50
ALLOWED_BATCH_SIZE = {10, 25, 50, 100, 200}


async def get_proxy_credentials() -> tuple[str, str] | None:
    redis = await get_redis()
    username = await redis.get(_KEY_PROXY_USERNAME)
    password = await redis.get(_KEY_PROXY_PASSWORD)
    if not username or not password:
        return None
    username = username if isinstance(username, str) else username.decode()
    password = password if isinstance(password, str) else password.decode()
    return username, password


async def set_proxy_credentials(username: str, password: str) -> None:
    username = username.strip()
    password = password.strip()
    if not username or not password:
        raise ValueError("username dan password proxy tidak boleh kosong")
    redis = await get_redis()
    await redis.set(_KEY_PROXY_USERNAME, username)
    await redis.set(_KEY_PROXY_PASSWORD, password)


def mask_credential(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


async def get_batch_size() -> int:
    redis = await get_redis()
    raw = await redis.get(_KEY_BATCH_SIZE)
    if raw is not None:
        return int(raw)
    await redis.set(_KEY_BATCH_SIZE, str(DEFAULT_BATCH_SIZE))
    return DEFAULT_BATCH_SIZE


async def set_batch_size(size: int) -> int:
    if size not in ALLOWED_BATCH_SIZE:
        raise ValueError(f"batch_size harus salah satu dari {sorted(ALLOWED_BATCH_SIZE)}")
    redis = await get_redis()
    await redis.set(_KEY_BATCH_SIZE, str(size))
    return size
