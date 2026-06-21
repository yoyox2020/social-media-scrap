"""
Processing Service — orkestrasi pipeline pembersihan dan deduplication post.

Pipeline per keyword:
  1. Ambil post yang belum diproses
  2. Bersihkan teks  (TextCleaner)
  3. Normalisasi teks (TextNormalizer)
  4. Deteksi bahasa   (TextNormalizer.detect_language)
  5. Deteksi near-duplicate (NearDuplicateDetector)
  6. Bulk update posts di DB
"""
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.posts.models import Post
from app.services.processing.cleaner import TextCleaner, default_cleaner
from app.services.processing.deduplicator import NearDuplicateDetector, default_detector
from app.services.processing.schemas import ProcessResult
from app.services.processing.text_normalizer import TextNormalizer, default_normalizer


class ProcessingService:
    def __init__(
        self,
        db: AsyncSession,
        cleaner: TextCleaner | None = None,
        normalizer: TextNormalizer | None = None,
        detector: NearDuplicateDetector | None = None,
    ):
        self.db = db
        self.cleaner = cleaner or default_cleaner
        self.normalizer = normalizer or default_normalizer
        self.detector = detector or default_detector

    async def process_keyword(
        self,
        keyword_id: uuid.UUID,
        force_reprocess: bool = False,
        batch_size: int = 500,
    ) -> ProcessResult:
        """
        Proses semua post milik keyword_id.
        force_reprocess=True akan memproses ulang post yang sudah diproses.
        """
        result = ProcessResult(keyword_id=keyword_id)

        posts = await self._fetch_posts(keyword_id, force_reprocess)
        result.total_posts = len(posts)

        if not posts:
            return result

        # ── Step 1: Clean + normalize + detect language ────────────────────────
        updates: list[dict] = []
        for post in posts:
            try:
                cleaned = self.cleaner.clean(post.content)
                language = self.normalizer.detect_language(cleaned)
                updates.append({
                    "id": post.id,
                    "cleaned_content": cleaned,
                    "language": language,
                    "is_processed": True,
                    "is_near_duplicate": False,  # reset sebelum deteksi ulang
                })
                result.cleaned += 1
            except Exception as exc:
                result.errors.append(f"post {post.id}: {exc}")

        # ── Step 2: Near-duplicate detection ──────────────────────────────────
        post_ids = [u["id"] for u in updates]
        contents = [u["cleaned_content"] for u in updates]

        try:
            dup_results = self.detector.find_duplicates(post_ids, contents)
            dup_ids = {d.post_id for d in dup_results}
            result.near_duplicates_found = len(dup_ids)

            for u in updates:
                if u["id"] in dup_ids:
                    u["is_near_duplicate"] = True
        except Exception as exc:
            result.errors.append(f"deduplication error: {exc}")

        # ── Step 3: Bulk update ────────────────────────────────────────────────
        try:
            await self._bulk_update(updates)
        except Exception as exc:
            result.errors.append(f"bulk update failed: {exc}")

        result.skipped_already_processed = result.total_posts - result.cleaned

        return result

    async def _fetch_posts(self, keyword_id: uuid.UUID, force: bool) -> list[Post]:
        stmt = select(Post).where(Post.keyword_id == keyword_id)
        if not force:
            stmt = stmt.where(Post.is_processed == False)  # noqa: E712
        stmt = stmt.order_by(Post.published_at.asc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def _bulk_update(self, updates: list[dict]) -> None:
        """Update banyak post sekaligus menggunakan executemany."""
        if not updates:
            return
        await self.db.execute(
            update(Post),
            updates,
        )
        await self.db.flush()
