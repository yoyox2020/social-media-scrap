"""
Viral Tracking Service.

Alur otomatis:
  1. detect_and_create_trackers  — temukan post >=1M views tanpa tracker → buat ViralChannelTracker
  2. run_daily_channel_scrape     — scrape 5 video/hari dari channel yang dilacak
  3. check_and_flag_commenters    — temukan akun yang >10x komentar → flag + buat tracker baru
"""
from __future__ import annotations

import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.domain.posts.models import Post
from app.domain.viral_tracking.models import FlaggedAccount, ViralChannelTracker, ViralKeywordTracker

VIRAL_VIEW_THRESHOLD = 1_000_000
TRACKER_DAYS = 7
POSTS_PER_DAY = 5
COMMENTER_FLAG_THRESHOLD = 10


# ── Public API ────────────────────────────────────────────────────────────────


async def detect_and_create_trackers(db: AsyncSession) -> list[uuid.UUID]:
    """
    Cari post YouTube dengan views >=1M yang belum punya tracker, buat tracker baru.
    Satu tracker per channel — jika channel sudah punya tracker aktif, skip.
    Return list tracker_id yang baru dibuat.
    """
    # Post yang sudah ada trackernya (berdasarkan trigger_post_id)
    existing_tracker_post_ids_result = await db.execute(
        select(ViralChannelTracker.trigger_post_id).where(
            ViralChannelTracker.trigger_post_id.isnot(None)
        )
    )
    already_tracked_posts: set[uuid.UUID] = set(existing_tracker_post_ids_result.scalars().all())

    # Channel yang sudah punya tracker aktif (deduplication per channel)
    existing_active_channels_result = await db.execute(
        select(ViralChannelTracker.channel_id).where(
            ViralChannelTracker.status == "active"
        )
    )
    tracked_channels: set[str] = set(existing_active_channels_result.scalars().all())

    viral_posts_result = await db.execute(
        select(Post).where(
            Post.platform == "youtube",
            Post.metadata_["views"].as_integer() >= VIRAL_VIEW_THRESHOLD,
        )
        # Proses post terlama dulu agar trigger_post yang paling awal yang jadi acuan
        .order_by(Post.collected_at.asc())
    )
    viral_posts: list[Post] = list(viral_posts_result.scalars().all())

    new_tracker_ids: list[uuid.UUID] = []
    newly_tagged_posts: list[Post] = []
    now = datetime.now(timezone.utc)

    for post in viral_posts:
        if post.id in already_tracked_posts:
            continue

        channel_id = _extract_channel_id_from_raw(post.raw_data or {})
        if not channel_id or channel_id in tracked_channels:
            continue

        # Cek sekali lagi ke DB sebelum insert — hindari race condition
        # jika detect task berjalan paralel di beberapa worker
        existing_check = await db.scalar(
            select(ViralChannelTracker.id).where(
                ViralChannelTracker.channel_id == channel_id,
                ViralChannelTracker.status == "active",
            ).limit(1)
        )
        if existing_check:
            tracked_channels.add(channel_id)
            continue

        channel_name = _extract_channel_name_from_raw(post.raw_data or {})

        tracker = ViralChannelTracker(
            channel_id=channel_id,
            channel_name=channel_name,
            trigger_post_id=post.id,
            keyword_id=post.keyword_id,
            tracker_type="viral",
            started_at=now,
            ends_at=now + timedelta(days=TRACKER_DAYS),
            status="active",
            posts_collected=0,
        )
        db.add(tracker)
        await db.flush()
        new_tracker_ids.append(tracker.id)
        tracked_channels.add(channel_id)

        # Tag trigger post langsung di DB — tidak perlu tunggu daily scrape
        meta = post.metadata_ or {}
        meta["tracker_id"] = str(tracker.id)
        meta["source"] = "viral_tracking"
        post.metadata_ = meta
        flag_modified(post, "metadata_")
        if post.content and not post.cleaned_content:
            from app.services.processing.cleaner import default_cleaner
            post.cleaned_content = default_cleaner.clean(post.content)
            post.is_processed = True
        newly_tagged_posts.append(post)

    await db.commit()

    # Dispatch embedding untuk trigger post yang baru di-tag (di luar transaction)
    if newly_tagged_posts:
        from app.workers.ai_worker import analyze_post_task
        for post in newly_tagged_posts:
            if post.id and post.embedding is None:
                analyze_post_task.delay(
                    str(post.id),
                    run_sentiment=False,
                    run_ner=False,
                    run_embedding=True,
                )

    return new_tracker_ids


async def run_daily_channel_scrape(db: AsyncSession, tracker_id: uuid.UUID) -> int:
    """
    Scrape 5 video terbaru dari channel tracker. Skip jika sudah scraping hari ini.
    Return jumlah post baru yang disimpan.
    """
    tracker = await db.get(ViralChannelTracker, tracker_id)
    if not tracker or tracker.status != "active":
        return 0

    today = date.today()
    if tracker.last_scraped_date == today:
        return 0  # Sudah scraping hari ini

    now = datetime.now(timezone.utc)
    if tracker.ends_at < now:
        tracker.status = "completed"
        await db.commit()
        return 0

    from app.integrations.ensemble_data.client import EnsembleDataClient
    from app.integrations.youtube.connector import YouTubeConnector
    from app.repositories.post_repository import PostRepository
    from app.services.processing.normalizer import YouTubeNormalizer

    post_repo = PostRepository(db)
    normalizer = YouTubeNormalizer()
    new_count = 0
    items: list = []  # pastikan selalu terdefinisi untuk after-try code

    try:
        async with EnsembleDataClient() as client:
            connector = YouTubeConnector(client)
            raw = await connector.get_channel_videos(tracker.channel_id, cursor="")
            items = connector.extract_posts(raw)
            items = items[:POSTS_PER_DAY]

            # Jika channel tidak punya video → tetap lanjut ke after-try
            # agar last_scraped_date di-set dan scrape_log ditulis (tidak loop selamanya)
            if items:
                posts = normalizer.normalize(items, tracker.keyword_id)
                ext_ids = [p.external_id for p in posts]
                existing = await post_repo.get_existing_external_ids(ext_ids, "youtube")
                new_posts = [p for p in posts if p.external_id not in existing]

                if new_posts:
                    # Isi views/likes/comments yang selalu 0 dari hasil search --
                    # lihat docstring enrich_youtube_statistics() di normalizer.py.
                    from app.services.processing.normalizer import enrich_youtube_statistics
                    await enrich_youtube_statistics(new_posts)

                for post in new_posts:
                    meta = post.metadata_ or {}
                    meta["tracker_id"] = str(tracker_id)
                    meta["source"] = "viral_tracking"
                    post.metadata_ = meta

                if new_posts:
                    from app.services.processing.cleaner import default_cleaner
                    for post in new_posts:
                        if post.content:
                            post.cleaned_content = default_cleaner.clean(post.content)
                            post.is_processed = True
                    new_count = await post_repo.bulk_create(new_posts)
                    if new_count > 0:
                        from app.workers.ai_worker import analyze_post_task
                        for post in new_posts:
                            if post.id:
                                analyze_post_task.delay(
                                    str(post.id),
                                    run_sentiment=False,
                                    run_ner=False,
                                    run_embedding=True,
                                )

                if new_posts:
                    from app.services.youtube.pipeline_service import collect_comments_for_video
                    for post in new_posts:
                        if post.id and tracker.keyword_id:
                            try:
                                await collect_comments_for_video(
                                    db=db,
                                    post_id=post.id,
                                    keyword_id=tracker.keyword_id,
                                    max_comments=50,
                                    max_pages=1,
                                )
                            except Exception:
                                pass  # lanjut ke post berikutnya

    except Exception as exc:
        _append_scrape_log(tracker, today, posts_new=0, posts_skipped=0, error=str(exc))
        tracker.last_scraped_date = today
        await db.commit()
        return 0

    # Hitung skipped = video ditemukan tapi sudah ada di DB (dedup)
    # posts_collected += new + skipped → mencerminkan total video channel yang kita punya
    skipped = len(items) - new_count if items else 0
    _append_scrape_log(tracker, today, posts_new=new_count, posts_skipped=skipped)
    tracker.last_scraped_date = today
    tracker.posts_collected = (tracker.posts_collected or 0) + new_count + skipped
    await db.commit()
    return new_count


def _append_scrape_log(
    tracker: "ViralChannelTracker",
    scrape_date: "date",
    *,
    posts_new: int,
    posts_skipped: int,
    error: str | None = None,
) -> None:
    """Tambahkan satu entri ke scrape_logs JSONB. Tidak commit — caller yang commit."""
    from datetime import date as _date
    from sqlalchemy.orm.attributes import flag_modified

    logs: list = list(tracker.scrape_logs or [])
    day_num = (scrape_date - tracker.started_at.date()).days + 1
    entry: dict = {
        "day": day_num,
        "date": scrape_date.isoformat(),
        "posts_new": posts_new,
        "posts_skipped": posts_skipped,
    }
    if error:
        entry["error"] = error[:300]
    logs.append(entry)
    tracker.scrape_logs = logs
    # Sinyal ke SQLAlchemy bahwa JSONB column berubah (mutasi in-place tidak terdeteksi otomatis)
    flag_modified(tracker, "scrape_logs")


async def check_and_flag_commenters(db: AsyncSession, tracker_id: uuid.UUID) -> list[uuid.UUID]:
    """
    Cari akun yang komentar >10x pada post tracker ini.
    Flag mereka di flagged_accounts, buat tracker baru jika channel_id valid (UCxxx).
    Return list flagged_account IDs yang baru dibuat.
    """
    tracker = await db.get(ViralChannelTracker, tracker_id)
    if not tracker:
        return []

    # Ambil semua komentar pada post yang dikumpulkan via tracker ini
    rows = await db.execute(
        text("""
            SELECT
                c.author,
                c.metadata ->> 'author_channel_id' AS channel_id,
                COUNT(*) AS cnt
            FROM comments c
            JOIN posts p ON p.id = c.post_id
            WHERE
                p.platform = 'youtube'
                AND p.metadata ->> 'tracker_id' = :tracker_id
            GROUP BY c.author, c.metadata ->> 'author_channel_id'
            HAVING COUNT(*) > :threshold
        """),
        {"tracker_id": str(tracker_id), "threshold": COMMENTER_FLAG_THRESHOLD},
    )
    commenter_rows = rows.fetchall()

    if not commenter_rows:
        return []

    # Ambil akun yang sudah diflag di tracker ini agar tidak duplikat
    existing_flagged = set(
        (await db.execute(
            select(FlaggedAccount.channel_id).where(
                FlaggedAccount.tracker_id == tracker_id
            )
        )).scalars().all()
    )

    now = datetime.now(timezone.utc)
    new_flag_ids: list[uuid.UUID] = []

    for row in commenter_rows:
        author_name: str = row.author or "unknown"
        ch_id: str | None = row.channel_id
        cnt: int = row.cnt

        if ch_id and ch_id in existing_flagged:
            continue

        # Buat ViralChannelTracker untuk commenter jika channel_id valid
        analysis_tracker_id: uuid.UUID | None = None
        if ch_id and ch_id.startswith("UC"):
            analysis_tracker = ViralChannelTracker(
                channel_id=ch_id,
                channel_name=author_name,
                trigger_post_id=tracker.trigger_post_id,
                keyword_id=tracker.keyword_id,
                tracker_type="flagged_commenter",
                started_at=now,
                ends_at=now + timedelta(days=TRACKER_DAYS),
                status="active",
                posts_collected=0,
            )
            db.add(analysis_tracker)
            await db.flush()
            analysis_tracker_id = analysis_tracker.id

        flagged = FlaggedAccount(
            channel_id=ch_id or "",
            channel_name=author_name,
            comment_count=cnt,
            tracker_id=tracker_id,
            trigger_post_id=tracker.trigger_post_id,
            analysis_tracker_id=analysis_tracker_id,
            flagged_at=now,
        )
        db.add(flagged)
        await db.flush()
        new_flag_ids.append(flagged.id)

    await db.commit()
    return new_flag_ids


async def create_keyword_tracker(db: AsyncSession, search_query: str) -> ViralKeywordTracker:
    """
    Buat ViralKeywordTracker baru untuk search_query.
    Jika sudah ada tracker aktif dengan query yang sama (case-insensitive), kembalikan yang sudah ada.
    """
    q_lower = search_query.strip().lower()
    existing = await db.scalar(
        select(ViralKeywordTracker)
        .where(
            func.lower(ViralKeywordTracker.search_query) == q_lower,
            ViralKeywordTracker.status == "active",
        )
        .limit(1)
    )
    if existing:
        return existing

    now = datetime.now(timezone.utc)
    tracker = ViralKeywordTracker(
        search_query=search_query.strip(),
        status="active",
        started_at=now,
        ends_at=now + timedelta(days=TRACKER_DAYS),
        posts_collected=0,
    )
    db.add(tracker)
    await db.commit()
    await db.refresh(tracker)
    return tracker


async def run_daily_keyword_scrape(db: AsyncSession, tracker_id: uuid.UUID) -> int:
    """
    Scrape video YouTube berdasarkan search_query tracker. Skip jika sudah scraping hari ini.
    Setelah scrape, kumpulkan komentar untuk setiap video baru.
    Return jumlah post baru.
    """
    tracker = await db.get(ViralKeywordTracker, tracker_id)
    if not tracker or tracker.status != "active":
        return 0

    today = date.today()
    if tracker.last_scraped_date == today:
        return 0

    now = datetime.now(timezone.utc)
    if tracker.ends_at < now:
        tracker.status = "completed"
        await db.commit()
        return 0

    from app.integrations.ensemble_data.client import EnsembleDataClient
    from app.integrations.youtube.connector import YouTubeConnector
    from app.repositories.post_repository import PostRepository
    from app.services.processing.normalizer import YouTubeNormalizer

    post_repo = PostRepository(db)
    normalizer = YouTubeNormalizer()
    new_count = 0
    items: list = []

    try:
        async with EnsembleDataClient() as client:
            connector = YouTubeConnector(client)
            raw = await connector.search_by_keyword(tracker.search_query, depth=1)
            items = connector.extract_posts(raw)
            items = items[:POSTS_PER_DAY]

        if items:
            posts = normalizer.normalize(items, None)
            ext_ids = [p.external_id for p in posts]
            existing_set = await post_repo.get_existing_external_ids(ext_ids, "youtube")
            new_posts = [p for p in posts if p.external_id not in existing_set]

            if new_posts:
                # Isi views/likes/comments yang selalu 0 dari hasil search --
                # lihat docstring enrich_youtube_statistics() di normalizer.py.
                from app.services.processing.normalizer import enrich_youtube_statistics
                await enrich_youtube_statistics(new_posts)

            for post in new_posts:
                meta = post.metadata_ or {}
                meta["keyword_tracker_id"] = str(tracker_id)
                meta["source"] = "keyword_tracking"
                post.metadata_ = meta

            if new_posts:
                from app.services.processing.cleaner import default_cleaner
                for post in new_posts:
                    if post.content:
                        post.cleaned_content = default_cleaner.clean(post.content)
                        post.is_processed = True
                new_count = await post_repo.bulk_create(new_posts)

            if new_posts:
                from app.services.youtube.pipeline_service import collect_comments_for_video
                for post in new_posts:
                    if post.id:
                        try:
                            await collect_comments_for_video(
                                db=db,
                                post_id=post.id,
                                keyword_id=None,
                                max_comments=50,
                                max_pages=1,
                            )
                        except Exception:
                            pass

    except Exception as exc:
        _append_keyword_log(tracker, today, posts_new=0, posts_skipped=0, error=str(exc))
        tracker.last_scraped_date = today
        await db.commit()
        return 0

    skipped = len(items) - new_count if items else 0
    _append_keyword_log(tracker, today, posts_new=new_count, posts_skipped=skipped)
    tracker.last_scraped_date = today
    tracker.posts_collected = (tracker.posts_collected or 0) + new_count
    await db.commit()
    return new_count


async def resume_active_keyword_trackers(db: AsyncSession) -> dict[str, list[str]]:
    """
    Harian: tandai keyword tracker expired sebagai completed, kembalikan yang perlu scraping.
    """
    now = datetime.now(timezone.utc)
    today = date.today()

    expired_result = await db.execute(
        select(ViralKeywordTracker).where(
            ViralKeywordTracker.status == "active",
            ViralKeywordTracker.ends_at < now,
        )
    )
    expired = expired_result.scalars().all()
    for t in expired:
        t.status = "completed"

    active_result = await db.execute(
        select(ViralKeywordTracker).where(
            ViralKeywordTracker.status == "active",
            ViralKeywordTracker.ends_at >= now,
        )
    )
    active = active_result.scalars().all()
    needs_scrape = [str(t.id) for t in active if t.last_scraped_date != today]

    if expired:
        await db.commit()

    return {"expired_completed": [str(t.id) for t in expired], "needs_scrape": needs_scrape}


def _append_keyword_log(
    tracker: "ViralKeywordTracker",
    scrape_date: "date",
    *,
    posts_new: int,
    posts_skipped: int,
    error: str | None = None,
) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    logs: list = list(tracker.day_logs or [])
    day_num = (scrape_date - tracker.started_at.date()).days + 1
    entry: dict = {
        "day": day_num,
        "date": scrape_date.isoformat(),
        "posts_new": posts_new,
        "posts_skipped": posts_skipped,
    }
    if error:
        entry["error"] = error[:300]
    logs.append(entry)
    tracker.day_logs = logs
    flag_modified(tracker, "day_logs")


async def resume_active_trackers(db: AsyncSession) -> dict[str, list[str]]:
    """
    Jalankan harian: tandai tracker expired sebagai completed, kembalikan
    daftar tracker yang masih aktif dan belum scraping hari ini.
    """
    now = datetime.now(timezone.utc)
    today = date.today()

    # Tandai yang expired
    expired_result = await db.execute(
        select(ViralChannelTracker).where(
            ViralChannelTracker.status == "active",
            ViralChannelTracker.ends_at < now,
        )
    )
    expired = expired_result.scalars().all()
    for t in expired:
        t.status = "completed"

    # Aktif + belum scraping hari ini
    pending_result = await db.execute(
        select(ViralChannelTracker).where(
            ViralChannelTracker.status == "active",
            ViralChannelTracker.ends_at >= now,
        )
    )
    active = pending_result.scalars().all()

    needs_scrape = [str(t.id) for t in active if t.last_scraped_date != today]

    if expired:
        await db.commit()

    return {
        "expired_completed": [str(t.id) for t in expired],
        "needs_scrape": needs_scrape,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────


def _extract_channel_id_from_raw(raw_data: dict[str, Any]) -> str | None:
    """Ekstrak channel_id (UCxxx) dari raw_data post — support EnsembleData & YT Data API v3."""
    # YouTube Data API v3 format
    snippet = raw_data.get("snippet") or {}
    if snippet.get("channelId"):
        return snippet["channelId"]

    # EnsembleData videoRenderer format
    try:
        return (
            raw_data["longBylineText"]["runs"][0]
            ["navigationEndpoint"]["browseEndpoint"]["browseId"]
        )
    except (KeyError, IndexError, TypeError):
        pass

    try:
        return (
            raw_data["ownerText"]["runs"][0]
            ["navigationEndpoint"]["browseEndpoint"]["browseId"]
        )
    except (KeyError, IndexError, TypeError):
        pass

    return None


def _extract_channel_name_from_raw(raw_data: dict[str, Any]) -> str:
    """Ekstrak nama channel dari raw_data post."""
    snippet = raw_data.get("snippet") or {}
    if snippet.get("channelTitle"):
        return snippet["channelTitle"]

    def _runs_text(obj: dict | None) -> str:
        if not obj:
            return ""
        return "".join(r.get("text", "") for r in (obj.get("runs") or []))

    name = _runs_text(raw_data.get("longBylineText")) or _runs_text(raw_data.get("ownerText"))
    return name or "unknown"
