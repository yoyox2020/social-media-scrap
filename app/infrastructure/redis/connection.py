from redis.asyncio import Redis, from_url

from app.shared.config import settings

_redis_client: Redis | None = None


async def get_redis() -> Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = from_url(settings.redis_url, decode_responses=True)
    return _redis_client


async def close_redis() -> None:
    global _redis_client
    if _redis_client:
        await _redis_client.aclose()
        _redis_client = None


async def reset_redis_client() -> None:
    """Buang referensi client TANPA mencoba aclose() -- dipakai di awal tiap
    task Celery yg jalan lewat `asyncio.run(_run())` (lihat
    app/workers/youtube_discovery_worker.py, youtube_metadata_worker.py).

    Kenapa TIDAK pakai close_redis(): asyncio.run() per-task bikin event
    loop BARU tiap dipanggil lalu MENUTUPNYA saat selesai. Kalau worker
    process yg SAMA sebelumnya sudah pernah pakai get_redis() (task lain),
    _redis_client yg ke-cache masih terikat ke event loop task SEBELUMNYA
    yg SUDAH tertutup -- coba .aclose() client itu di event loop BARU ikut
    gagal dgn error yg SAMA ("Event loop is closed"). Solusinya: buang saja
    referensinya (biarkan GC yg bereskan objek lama), get_redis() berikutnya
    otomatis bikin client baru yg terikat ke event loop task INI."""
    global _redis_client
    _redis_client = None
