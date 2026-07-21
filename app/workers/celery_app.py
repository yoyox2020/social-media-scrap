"""
Celery app -- API v2 (2026-07-22).

Kosong (belum ada task/beat_schedule sama sekali) sengaja: SEMUA worker
platform lama dihapus bersamaan restrukturisasi total (lihat docstring
app/main.py & project_api_v2_restructure di memory). File ini
dipertahankan minimal HANYA supaya container `worker`/`worker-ai`/
`worker-beat` bisa hidup lagi (sebelumnya crash total krn modul
`app.workers.celery_app` sempat tidak ada).

Kalau agent baru (agent_youtube, agent_facebook, dst -- lihat tabel
`agent_registry`/`agent_key_pool`) mulai punya kode scraping asli,
tambahkan modul task-nya di sini via `include=[...]` dan jadwalnya di
`beat_schedule`, ikuti pola 6-lapis yang sudah dipakai project ini
sebelumnya (config -> agent/service -> worker task -> beat schedule ->
endpoint status -> dashboard).
"""
from celery import Celery

from app.shared.config import settings

celery_app = Celery(
    "social_intelligence",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[],  # tambahkan modul workers.<agent>_worker di sini kalau sudah ada task asli
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Jakarta",
    enable_utc=True,
    beat_schedule={},  # tambahkan jadwal per agent di sini kalau sudah ada task asli
)
