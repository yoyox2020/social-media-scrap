"""
Celery app -- API v2 (2026-07-22).

Agent lain (agent_facebook, agent_tiktok, dst -- lihat tabel
`agent_registry`) MASIH kosong tugasnya, tambahkan modul task-nya di
sini via `include=[...]` dan jadwalnya di `beat_schedule` kalau sudah
punya kode scraping asli, ikuti pola 6-lapis yang sudah dipakai project
ini sebelumnya (config -> agent/service -> worker task -> beat schedule
-> endpoint status -> dashboard).

YouTube (2026-07-22, permintaan user "crawling otomatis tiap 1 jam
utk top 20 topik") SUDAH punya jadwal -- lihat
app/workers/youtube_auto_crawl_worker.py utk detail sumber topik &
PERINGATAN KUOTA (baru 1 key YouTube asli, kuota bisa habis sblm jadwal
berikutnya)."""
from celery import Celery

# Wajib diimport SEBELUM task apa pun jalan -- Celery proses TERPISAH
# dari app.main (API), jadi mapper registry SQLAlchemy-nya juga
# terpisah. Tanpa ini, task yg query model dgn relationship string
# (mis. Comment -> "Sentiment") crash InvalidRequestError begitu ORM
# pertama kali dipakai (ditemukan 2026-07-22 waktu build auto-crawl).
import app.infrastructure.database.register_all_models  # noqa: F401,E402
from app.shared.config import settings  # noqa: E402

celery_app = Celery(
    "social_intelligence",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.youtube_auto_crawl_worker", "app.workers.tiktok_auto_crawl_worker"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Jakarta",
    enable_utc=True,
    beat_schedule={
        "youtube-auto-crawl-hourly": {
            "task": "youtube.auto_crawl_top_topics",
            "schedule": 3600.0,
        },
        "tiktok-auto-crawl-hourly": {
            "task": "tiktok.auto_crawl_top_topics",
            "schedule": 3600.0,
        },
    },
)
