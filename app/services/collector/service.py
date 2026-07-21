"""
Collector Service: orkestrasi pengumpulan data dari platform social media.

Flow:
  API trigger → CollectorService → Celery task per platform
  Celery task → Connector (EnsembleData) → Normalizer → PostRepository
"""
import logging
import uuid
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

from app.repositories.keyword_repository import KeywordRepository
from app.services.collector.schemas import CollectJobResponse, CollectionResult
from app.shared.exceptions import NotFoundError, ValidationError
from app.integrations.ensemble_data.endpoints import SUPPORTED_COLLECTION_PLATFORMS

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class CollectorService:
    def __init__(self, keyword_repo: KeywordRepository):
        self.keyword_repo = keyword_repo

    async def trigger_collection(self, keyword_id: uuid.UUID, platforms: list[str]) -> CollectJobResponse:
        """Dispatch Celery task per platform. Return job IDs untuk tracking."""
        # Import di sini untuk menghindari circular import saat modul diload
        from app.workers.collector_worker import collect_posts_task

        keyword = await self.keyword_repo.get_by_id(keyword_id)
        if not keyword:
            raise NotFoundError("Keyword", str(keyword_id))
        if not keyword.is_active:
            raise ValidationError("Keyword tidak aktif")

        invalid = [p for p in platforms if p not in SUPPORTED_COLLECTION_PLATFORMS]
        if invalid:
            raise ValidationError(f"Platform tidak didukung: {invalid}")

        jobs = []
        for platform in platforms:
            task = collect_posts_task.delay(str(keyword_id), platform)
            jobs.append({"platform": platform, "job_id": task.id, "status": "queued"})

        return CollectJobResponse(
            keyword_id=keyword_id,
            keyword_text=keyword.keyword,
            jobs=jobs,
        )

    async def collect_for_platform(
        self,
        keyword_id: uuid.UUID,
        platform: str,
        max_pages: int = 5,
        max_results: int | None = None,
        db: "AsyncSession | None" = None,
    ) -> CollectionResult:
        """
        Jalankan koleksi secara langsung (sinkron/async) tanpa Celery.
        Dipanggil dari dalam Celery task.

        Jika `db` tidak diberikan, dibuat session baru dari AsyncSessionLocal
        global. Saat dipanggil dari Celery ForkPoolWorker, caller WAJIB
        mengirim session yang dibuat lewat fresh engine (lihat
        `_get_fresh_session()` di app/workers/youtube_worker.py) — engine
        global terikat ke event loop parent process dan akan menyebabkan
        asyncpg InterfaceError ("another operation is in progress") jika
        dipakai di event loop baru milik child process.
        """
        if db is not None:
            return await self._collect_for_platform_with_session(
                db, keyword_id, platform, max_pages, max_results
            )

        from app.infrastructure.database.connection import AsyncSessionLocal

        async with AsyncSessionLocal() as fresh_db:
            return await self._collect_for_platform_with_session(
                fresh_db, keyword_id, platform, max_pages, max_results
            )

    async def _collect_for_platform_with_session(
        self,
        db: "AsyncSession",
        keyword_id: uuid.UUID,
        platform: str,
        max_pages: int,
        max_results: int | None,
    ) -> CollectionResult:
        from app.integrations.ensemble_data.client import EnsembleDataClient
        from app.repositories.post_repository import PostRepository
        from app.services.processing.normalizer import get_normalizer

        keyword_repo = KeywordRepository(db)
        keyword = await keyword_repo.get_by_id(keyword_id)
        if not keyword:
            raise NotFoundError("Keyword", str(keyword_id))

        result = CollectionResult(platform=platform, keyword=keyword.keyword)
        normalizer = get_normalizer(platform)
        connector = _get_connector(platform)
        post_repo = PostRepository(db)

        logger.info(
            "[Collector] Mulai scraping — platform=%s keyword=%r max_pages=%d",
            platform, keyword.keyword, max_pages,
        )

        async with EnsembleDataClient() as client:
            connector_instance = connector(client)
            cursor = None

            for page in range(max_pages):
                try:
                    logger.info("[Collector] Fetch halaman %d/%d — keyword=%r", page + 1, max_pages, keyword.keyword)
                    raw = await _fetch_page(
                        connector_instance, platform, keyword.keyword, cursor, max_pages
                    )
                    if raw.get("_source") == "youtube_data_api":
                        result.used_fallback = True
                    items = connector_instance.extract_posts(raw)

                    if not items:
                        logger.info("[Collector] Halaman %d kosong, berhenti.", page + 1)
                        break

                    if max_results is not None:
                        items = items[:max_results]

                    posts = normalizer.normalize(items, keyword_id)
                    result.total_fetched += len(posts)
                    result.pages_fetched += 1

                    # Deduplication
                    ext_ids = [p.external_id for p in posts]
                    existing = await post_repo.get_existing_external_ids(ext_ids, platform)
                    new_posts = [p for p in posts if p.external_id not in existing]
                    result.skipped_duplicates += len(posts) - len(new_posts)

                    logger.info(
                        "[Collector] Halaman %d: total=%d baru=%d duplikat=%d",
                        page + 1, len(posts), len(new_posts), len(posts) - len(new_posts),
                    )

                    if platform == "youtube" and new_posts:
                        # Isi views/likes/comments yang selalu 0 dari hasil search --
                        # lihat docstring enrich_youtube_statistics() utk detail root cause.
                        # Cuma utk new_posts (bukan posts) supaya tidak buang kuota API
                        # utk video yang sudah ada di DB.
                        from app.services.processing.normalizer import enrich_youtube_statistics
                        await enrich_youtube_statistics(new_posts)

                    if new_posts:
                        inserted = await post_repo.bulk_create(new_posts)
                        result.new_posts += inserted
                        logger.info("[Collector] Disimpan ke DB: %d video baru", inserted)

                    cursor = connector_instance.extract_cursor(raw)
                    if cursor is None:
                        logger.info("[Collector] Tidak ada halaman berikutnya, selesai.")
                        break

                except Exception as exc:
                    logger.error("[Collector] Error halaman %d: %s", page + 1, exc)
                    result.errors.append(f"Page {page + 1}: {exc}")
                    break

            await db.commit()

        logger.info(
            "[Collector] Selesai — keyword=%r total=%d baru=%d duplikat=%d error=%d",
            keyword.keyword, result.total_fetched, result.new_posts,
            result.skipped_duplicates, len(result.errors),
        )
        return result


def _get_connector(platform: str):
    """Kembalikan kelas connector berdasarkan nama platform."""
    from app.integrations.tiktok.connector import TikTokConnector
    from app.integrations.youtube.connector import YouTubeConnector
    from app.integrations.reddit.connector import RedditConnector
    from app.integrations.threads.connector import ThreadsConnector

    mapping = {
        "tiktok": TikTokConnector,
        "youtube": YouTubeConnector,
        "reddit": RedditConnector,
        "threads": ThreadsConnector,
    }
    connector = mapping.get(platform)
    if not connector:
        raise ValueError(f"Connector tidak tersedia untuk platform: {platform}")
    return connector


async def _fetch_page(connector, platform: str, keyword: str, cursor, max_pages: int = 5) -> dict:
    """Ambil satu halaman data dari connector yang sesuai."""
    if platform == "tiktok":
        return await connector.search_by_keyword(keyword, cursor=cursor or 0)
    elif platform == "youtube":
        # YouTube menggunakan depth (jumlah halaman per call) bukan cursor.
        # Kita set depth=max_pages agar satu call = semua halaman yang diinginkan.
        return await connector.search_by_keyword(keyword, depth=max_pages)
    elif platform == "reddit":
        return await connector.search_by_keyword(keyword, after=cursor)
    elif platform == "threads":
        return await connector.search_by_keyword(keyword, cursor=cursor)
    else:
        raise ValueError(f"Platform tidak dikenal: {platform}")
