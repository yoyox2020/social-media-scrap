"""
Celery task untuk pengumpulan data dari social media.

Celery tidak mendukung async natively, jadi kita gunakan asyncio.run()
untuk menjalankan logika async di dalam task sinkron.
"""
import asyncio
import uuid

from app.workers.celery_app import celery_app


@celery_app.task(
    name="workers.collect_posts",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def collect_posts_task(self, keyword_id: str, platform: str, max_pages: int = 5) -> dict:
    """
    Kumpulkan post dari platform tertentu untuk satu keyword.

    Args:
        keyword_id: UUID keyword yang akan diproses
        platform:   Nama platform (tiktok, youtube, instagram, reddit, threads)
        max_pages:  Maksimum halaman yang difetch per job

    Returns:
        dict CollectionResult berisi statistik koleksi
    """
    try:
        result = asyncio.run(_run_collection(keyword_id, platform, max_pages))
        return result
    except Exception as exc:
        raise self.retry(exc=exc)


async def _run_collection(keyword_id: str, platform: str, max_pages: int) -> dict:
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.repositories.keyword_repository import KeywordRepository
    from app.services.collector.service import CollectorService

    async with AsyncSessionLocal() as db:
        keyword_repo = KeywordRepository(db)
        service = CollectorService(keyword_repo)
        result = await service.collect_for_platform(
            keyword_id=uuid.UUID(keyword_id),
            platform=platform,
            max_pages=max_pages,
        )
        return result.to_dict()
