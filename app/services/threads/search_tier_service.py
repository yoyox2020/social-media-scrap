"""
Alur tier pencarian Threads (Fase 1, 2026-07-20) -- lihat
docs/threads-redesign-schema.md §2:

  Tier 1: data sudah ada di DB & masih segar (< cache_freshness_hours)?
          -> pakai itu, JANGAN panggil EnsembleData sama sekali.
  Tier 2: belum ada/basi -> slot job paralel Threads masih ada?
          -> dispatch Celery task spt biasa.
  Tier 3: slot penuh -> masuk `threads_search_queue`, diproses belakangan
          oleh task `threads-queue-drain` (Fase 2).

Dipakai oleh POST /threads/search (manual) DAN nanti oleh task
antrian/trend-scrape -- SATU sumber logika, tidak diduplikasi.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.posts.models import Post
from app.domain.scrape_runs.models import ScrapeRun
from app.domain.threads.models import ThreadsSearchQueue
from app.services.threads import search_tier_config as cfg


async def get_fresh_cached_post_count(db: AsyncSession, keyword: str) -> int:
    """Tier 1: berapa post Threads utk keyword ini yg di-collect dalam
    `cache_freshness_hours` terakhir. > 0 berarti data dianggap masih
    segar, tidak perlu scrape ulang."""
    freshness_hours = await cfg.get_cache_freshness_hours()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=freshness_hours)
    q_clean = keyword.strip()
    count = await db.scalar(
        select(func.count(Post.id)).where(
            Post.platform == "threads",
            Post.collected_at >= cutoff,
            (Post.content.ilike(f"%{q_clean}%")) | (Post.author.ilike(f"%{q_clean}%")),
        )
    )
    return count or 0


async def count_running_threads_jobs(db: AsyncSession) -> int:
    """Tier 2: berapa scrape_runs platform=threads yg statusnya MASIH
    'running' sekarang -- proxy sederhana utk "slot job paralel yg
    sedang dipakai", tanpa perlu introspeksi Celery broker langsung."""
    count = await db.scalar(
        select(func.count(ScrapeRun.id)).where(
            ScrapeRun.platform == "threads",
            ScrapeRun.status == "running",
        )
    )
    return count or 0


async def has_available_slot(db: AsyncSession) -> bool:
    max_jobs = await cfg.get_max_concurrent_jobs()
    running = await count_running_threads_jobs(db)
    return running < max_jobs


async def enqueue_search(
    db: AsyncSession,
    keyword: str,
    source: str,
    source_ref_id=None,
) -> ThreadsSearchQueue:
    """Tier 3: simpan keyword ke antrian tertunda, TIDAK dispatch
    Celery task -- akan dicoba lagi oleh `threads-queue-drain` (Fase 2)."""
    item = ThreadsSearchQueue(
        keyword_text=keyword.strip(),
        source=source,
        source_ref_id=source_ref_id,
        status="pending",
        attempts=0,
        requested_at=datetime.now(timezone.utc),
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item
