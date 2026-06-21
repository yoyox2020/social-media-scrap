"""
Celery task untuk processing pipeline (clean, normalize, deduplicate).
Dipanggil setelah collection selesai atau secara manual via API.
"""
import asyncio
import uuid

from app.workers.celery_app import celery_app


@celery_app.task(
    name="workers.process_posts",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def process_posts_task(self, keyword_id: str, force_reprocess: bool = False) -> dict:
    """
    Proses semua post untuk satu keyword:
    clean → normalize → detect language → deduplicate.

    Args:
        keyword_id:       UUID keyword yang akan diproses
        force_reprocess:  True = proses ulang post yang sudah diproses

    Returns:
        dict ProcessResult
    """
    try:
        return asyncio.run(_run_processing(keyword_id, force_reprocess))
    except Exception as exc:
        raise self.retry(exc=exc)


async def _run_processing(keyword_id: str, force_reprocess: bool) -> dict:
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.services.processing.service import ProcessingService

    async with AsyncSessionLocal() as db:
        service = ProcessingService(db)
        result = await service.process_keyword(
            keyword_id=uuid.UUID(keyword_id),
            force_reprocess=force_reprocess,
        )
        await db.commit()
        return result.to_dict()
