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


def _get_fresh_session():
    """
    Buat AsyncSession baru dengan engine baru per-task.
    Diperlukan karena Celery ForkPoolWorker mewarisi engine dari parent process,
    tapi asyncpg connections terikat ke event loop parent yang sudah tidak valid
    di child process ketika asyncio.run() membuat event loop baru.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from app.shared.config import settings

    fresh_engine = create_async_engine(
        settings.database_url,
        pool_size=2,
        max_overflow=0,
        echo=False,
    )
    session_factory = async_sessionmaker(
        bind=fresh_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    return fresh_engine, session_factory


async def _run_collection(keyword_id: str, platform: str, max_pages: int) -> dict:
    from app.repositories.keyword_repository import KeywordRepository
    from app.services.collector.service import CollectorService

    fresh_engine, session_factory = _get_fresh_session()
    async with session_factory() as db:
        keyword_repo = KeywordRepository(db)
        service = CollectorService(keyword_repo)
        result = await service.collect_for_platform(
            keyword_id=uuid.UUID(keyword_id),
            platform=platform,
            max_pages=max_pages,
            db=db,
        )
    await fresh_engine.dispose()
    return result.to_dict()
