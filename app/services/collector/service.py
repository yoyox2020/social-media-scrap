"""
Collector Service: orkestrasi pengumpulan data dari platform social media.

Flow:
  API trigger → CollectorService → Celery task per platform
  Celery task → Connector (EnsembleData) → Normalizer → PostRepository
"""
import uuid

from app.repositories.keyword_repository import KeywordRepository
from app.services.collector.schemas import CollectJobResponse, CollectionResult
from app.shared.exceptions import NotFoundError, ValidationError
from app.integrations.ensemble_data.endpoints import SUPPORTED_COLLECTION_PLATFORMS


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
    ) -> CollectionResult:
        """
        Jalankan koleksi secara langsung (sinkron/async) tanpa Celery.
        Dipanggil dari dalam Celery task.
        """
        from app.infrastructure.database.connection import AsyncSessionLocal
        from app.integrations.ensemble_data.client import EnsembleDataClient
        from app.repositories.post_repository import PostRepository
        from app.services.processing.normalizer import get_normalizer
        from app.domain.keywords.models import Keyword

        async with AsyncSessionLocal() as db:
            keyword_repo = KeywordRepository(db)
            keyword = await keyword_repo.get_by_id(keyword_id)
            if not keyword:
                raise NotFoundError("Keyword", str(keyword_id))

            result = CollectionResult(platform=platform, keyword=keyword.keyword)
            normalizer = get_normalizer(platform)
            connector = _get_connector(platform)
            post_repo = PostRepository(db)

            async with EnsembleDataClient() as client:
                connector_instance = connector(client)
                cursor = None

                for page in range(max_pages):
                    try:
                        raw = await _fetch_page(
                            connector_instance, platform, keyword.keyword, cursor, max_pages
                        )
                        items = connector_instance.extract_posts(raw)

                        if not items:
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

                        if new_posts:
                            inserted = await post_repo.bulk_create(new_posts)
                            result.new_posts += inserted

                        cursor = connector_instance.extract_cursor(raw)
                        if cursor is None:
                            break

                    except Exception as exc:
                        result.errors.append(f"Page {page + 1}: {exc}")
                        break

                await db.commit()

        return result


def _get_connector(platform: str):
    """Kembalikan kelas connector berdasarkan nama platform."""
    from app.integrations.tiktok.connector import TikTokConnector
    from app.integrations.youtube.connector import YouTubeConnector
    from app.integrations.instagram.connector import InstagramConnector
    from app.integrations.reddit.connector import RedditConnector
    from app.integrations.threads.connector import ThreadsConnector

    mapping = {
        "tiktok": TikTokConnector,
        "youtube": YouTubeConnector,
        "instagram": InstagramConnector,
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
    elif platform == "instagram":
        return await connector.search(keyword)
    elif platform == "reddit":
        return await connector.search_by_keyword(keyword, after=cursor)
    elif platform == "threads":
        return await connector.search_by_keyword(keyword, cursor=cursor)
    else:
        raise ValueError(f"Platform tidak dikenal: {platform}")
