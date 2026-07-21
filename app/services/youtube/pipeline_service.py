"""
YouTube Pipeline Service.

Orchestrasi penuh:
  trending → keywords → videos → comments (semua halaman) → lexicon sentiment → analytics
"""
from __future__ import annotations

import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import extract, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.comments.models import Comment
from app.domain.keywords.models import Keyword
from app.domain.posts.models import Post
from app.domain.trending.models import TrendingTopic
from app.domain.youtube_analysis.models import LexiconAnalysis
from app.integrations.ensemble_data.client import EnsembleDataClient
from app.integrations.google_trends.connector import TrendingResult, fetch_trending
from app.integrations.youtube.connector import YouTubeConnector
from app.services.youtube.schemas import (
    CommentCollectionResult,
    DashboardResponse,
    DashboardSummary,
    KeywordPipelineStatus,
    KeywordSentimentSummary,
    SentimentDistributionItem,
    SentimentDistributionResponse,
    SentimentTableResponse,
    SentimentTableRow,
    TrendingFetchRequest,
    TrendingFetchResponse,
    TrendingItemResponse,
    WordCloudItem,
    WordCloudResponse,
)


# ─────────────────────────────────────────────────────────────────────────────
# TRENDING
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_and_store_trending(
    db: AsyncSession,
    request: TrendingFetchRequest,
) -> TrendingFetchResponse:
    """
    Ambil trending dari Google Trends, simpan ke DB, buat Keyword record,
    dan (opsional) queue Celery pipeline per keyword.
    """
    result: TrendingResult = fetch_trending(
        geo=request.geo,
        period=request.period,
        limit=request.limit,
    )

    for item in result.items:
        db.add(TrendingTopic(
            rank=item.rank,
            title=item.title,
            traffic=item.traffic,
            description=item.description,
            geo=item.geo,
            period=item.period,
            published_at=item.published_at,
            fetched_at=result.fetched_at,
        ))

    await db.flush()

    # Validasi project_id — fallback ke project aktif pertama jika tidak ada
    from app.domain.projects.models import Project
    project_exists = await db.scalar(
        select(Project.id).where(Project.id == request.project_id).limit(1)
    )
    if not project_exists:
        project_exists = await db.scalar(
            select(Project.id).where(Project.is_active == True).limit(1)  # noqa: E712
        )
    if not project_exists:
        from app.shared.exceptions import NotFoundError
        raise NotFoundError("Project", str(request.project_id))
    resolved_project_id = project_exists

    keywords_created = 0
    keyword_ids: list[uuid.UUID] = []

    for item in result.items:
        existing = await db.scalar(
            select(Keyword).where(
                Keyword.project_id == resolved_project_id,
                Keyword.keyword == item.title,
            )
        )
        if existing:
            keyword_ids.append(existing.id)
        else:
            kw = Keyword(
                project_id=resolved_project_id,
                keyword=item.title,
                is_active=True,
            )
            db.add(kw)
            await db.flush()
            keyword_ids.append(kw.id)
            keywords_created += 1

    await db.commit()

    jobs_queued = 0
    if request.auto_collect and keyword_ids:
        from app.workers.youtube_worker import collect_youtube_pipeline_task

        for kw_id in keyword_ids:
            collect_youtube_pipeline_task.delay(
                str(kw_id),
                max_pages=request.max_pages_per_keyword,
            )
            jobs_queued += 1

    return TrendingFetchResponse(
        geo=result.geo,
        period=result.period,
        fetched_at=result.fetched_at,
        items=[
            TrendingItemResponse(
                rank=i.rank,
                title=i.title,
                traffic=i.traffic,
                description=i.description,
                published_at=i.published_at,
            )
            for i in result.items
        ],
        keywords_created=keywords_created,
        jobs_queued=jobs_queued,
    )


# ─────────────────────────────────────────────────────────────────────────────
# COMMENT COLLECTION  (dengan pagination cursor)
# ─────────────────────────────────────────────────────────────────────────────

async def collect_comments_for_video(
    db: AsyncSession,
    post_id: uuid.UUID,
    keyword_id: uuid.UUID | None,
    max_comments: int = 50,
    max_pages: int = 1,
    skip_ensemble: bool = False,
) -> CommentCollectionResult:
    """
    Ambil semua halaman komentar untuk satu video, simpan ke DB,
    jalankan lexicon sentiment per komentar baru.

    Menggunakan cursor loop — setiap halaman ~20 komentar.

    Args:
        skip_ensemble: teruskan ke connector.get_video_comments() -- lewati
            percobaan EnsembleData, langsung YouTube Data API v3. Lihat
            docstring YouTubeConnector.get_video_comments() utk alasannya.
    """
    post = await db.get(Post, post_id)
    if not post:
        return CommentCollectionResult(
            video_external_id="",
            comments_fetched=0,
            comments_new=0,
            comments_analyzed=0,
            errors=["Post tidak ditemukan"],
        )

    video_id = post.external_id
    result = CommentCollectionResult(video_external_id=video_id)

    existing_ids_result = await db.scalars(
        select(Comment.external_id).where(Comment.post_id == post_id)
    )
    existing_ids: set[str] = set(existing_ids_result.all())

    try:
        async with EnsembleDataClient() as client:
            connector = YouTubeConnector(client)
            cursor: str = ""
            last_source: str | None = None
            all_raw_comments: list[dict] = []

            for _page in range(max_pages):
                raw = await connector.get_video_comments(video_id, cursor=cursor, skip_ensemble=skip_ensemble)
                current_source = raw.get("_source")  # None=EnsembleData, "youtube_data_api"=fallback
                page_comments = connector.extract_comments(raw)

                if not page_comments:
                    break

                all_raw_comments.extend(page_comments)
                result.comments_fetched += len(page_comments)

                # Ambil cursor untuk halaman berikutnya
                next_cursor = connector.extract_cursor(raw)
                if not next_cursor:
                    break

                # Jika sumber berubah di tengah pagination (EnsembleData → YouTube Data API),
                # cursor lama tidak kompatibel — hentikan pagination agar tidak kirim
                # EnsembleData cursor ke YouTube Data API atau sebaliknya.
                if last_source is not None and current_source != last_source:
                    break

                cursor = next_cursor
                last_source = current_source

                # Berhenti jika sudah melebihi max_comments
                if result.comments_fetched >= max_comments:
                    break

        # Simpan komentar baru (deduplication via external_id)
        new_comments: list[Comment] = []
        for raw_c in all_raw_comments[:max_comments]:
            ext_id = _get_comment_id(raw_c)
            if not ext_id or ext_id in existing_ids:
                continue

            toolbar = raw_c.get("toolbar") or {}
            author_channel_id = (raw_c.get("author") or {}).get("channelId")
            comment = Comment(
                post_id=post_id,
                external_id=ext_id,
                content=_get_comment_text(raw_c),
                author=_get_comment_author(raw_c),
                published_at=None,
                metadata_={
                    "like_count": _parse_count(toolbar.get("likeCountNotliked", "0")),
                    "reply_count": _parse_count(toolbar.get("replyCount", "0")),
                    "published_time": (raw_c.get("properties") or {}).get("publishedTime", ""),
                    "is_pinned": bool((raw_c.get("commentViewModel") or {}).get("pinnedText")),
                    **({"author_channel_id": author_channel_id} if author_channel_id else {}),
                },
            )
            db.add(comment)
            new_comments.append(comment)
            existing_ids.add(ext_id)

        await db.flush()
        result.comments_new = len(new_comments)

        # Lexicon sentiment
        analyzed = await _analyze_comments_lexicon(db, new_comments, keyword_id)
        result.comments_analyzed = analyzed

        await db.commit()

    except Exception as exc:
        result.errors.append(str(exc))
        await db.rollback()

    return result


async def _analyze_comments_lexicon(
    db: AsyncSession,
    comments: list[Comment],
    keyword_id: uuid.UUID | None,
) -> int:
    from app.ai.lexicon.service import analyze

    count = 0
    for comment in comments:
        if not comment.content:
            continue
        res = analyze(comment.content)
        db.add(LexiconAnalysis(
            comment_id=comment.id,
            keyword_id=keyword_id,
            matched_positive=res.matched_positive,
            matched_negative=res.matched_negative,
            removed_stopwords=res.removed_stopwords,
            score=res.score,
            label=res.label,
        ))
        count += 1

    await db.flush()
    return count


# ─────────────────────────────────────────────────────────────────────────────
# ANALYTICS
# ─────────────────────────────────────────────────────────────────────────────

async def get_sentiment_distribution(
    db: AsyncSession,
    keyword_id: uuid.UUID,
    date_from: date | None = None,
    date_to: date | None = None,
) -> SentimentDistributionResponse:
    keyword = await db.get(Keyword, keyword_id)
    if not keyword:
        from app.shared.exceptions import NotFoundError
        raise NotFoundError("Keyword", str(keyword_id))

    q = select(LexiconAnalysis.label).where(LexiconAnalysis.keyword_id == keyword_id)
    q = _apply_date_filter(q, LexiconAnalysis.created_at, date_from, date_to)
    rows = await db.execute(q)
    labels = [r[0] for r in rows.all()]
    total = len(labels)
    counter = Counter(labels)

    return SentimentDistributionResponse(
        keyword_id=keyword_id,
        keyword_text=keyword.keyword,
        total_comments=total,
        distribution=[
            SentimentDistributionItem(
                label=lbl,
                count=counter.get(lbl, 0),
                percentage=round(counter.get(lbl, 0) / total * 100, 2) if total else 0.0,
            )
            for lbl in ["positif", "negatif", "netral"]
        ],
    )


async def get_sentiment_table(
    db: AsyncSession,
    keyword_id: uuid.UUID,
    label_filter: str | None = None,
    limit: int = 100,
    offset: int = 0,
    date_from: date | None = None,
    date_to: date | None = None,
    hour: int | None = None,
) -> SentimentTableResponse:
    keyword = await db.get(Keyword, keyword_id)
    if not keyword:
        from app.shared.exceptions import NotFoundError
        raise NotFoundError("Keyword", str(keyword_id))

    base = (
        select(LexiconAnalysis, Comment, Post)
        .join(Comment, LexiconAnalysis.comment_id == Comment.id)
        .join(Post, Comment.post_id == Post.id)
        .where(LexiconAnalysis.keyword_id == keyword_id)
    )
    if label_filter:
        base = base.where(LexiconAnalysis.label == label_filter)
    base = _apply_date_filter(base, LexiconAnalysis.created_at, date_from, date_to, hour)

    count_q = select(func.count()).select_from(base.subquery())
    total = (await db.scalar(count_q)) or 0

    rows_result = await db.execute(base.offset(offset).limit(limit))
    rows = [
        SentimentTableRow(
            comment_id=comment.id,
            comment_text=comment.content,
            author=comment.author,
            video_url=post.url,
            matched_positive=analysis.matched_positive or [],
            matched_negative=analysis.matched_negative or [],
            removed_stopwords=analysis.removed_stopwords or [],
            score=analysis.score,
            label=analysis.label,
            analyzed_at=analysis.created_at,
        )
        for analysis, comment, post in rows_result.all()
    ]

    return SentimentTableResponse(
        keyword_id=keyword_id,
        keyword_text=keyword.keyword,
        total=total,
        rows=rows,
    )


async def get_wordcloud_data(
    db: AsyncSession,
    keyword_id: uuid.UUID,
    sentiment_filter: str | None = None,
    top_n: int = 100,
    date_from: date | None = None,
    date_to: date | None = None,
) -> WordCloudResponse:
    q = select(Comment.content).join(
        LexiconAnalysis, LexiconAnalysis.comment_id == Comment.id
    ).where(LexiconAnalysis.keyword_id == keyword_id)

    if sentiment_filter:
        q = q.where(LexiconAnalysis.label == sentiment_filter)
    q = _apply_date_filter(q, LexiconAnalysis.created_at, date_from, date_to)

    result = await db.scalars(q)
    from app.ai.lexicon.service import _stopwords, _tokenize

    stop_set = _stopwords()
    word_counter: Counter = Counter()

    for text in result.all():
        if not text:
            continue
        for token in _tokenize(text):
            if token not in stop_set and len(token) > 2:
                word_counter[token] += 1

    return WordCloudResponse(
        keyword_id=keyword_id,
        sentiment_filter=sentiment_filter,
        words=[WordCloudItem(word=w, count=c) for w, c in word_counter.most_common(top_n)],
    )


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD SUMMARY
# ─────────────────────────────────────────────────────────────────────────────

async def get_dashboard_summary(
    db: AsyncSession,
    project_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> DashboardResponse:
    """
    Agregasi lengkap untuk dashboard:
      - total trending, keyword, video, komentar, analisis
      - distribusi sentimen global
      - per-keyword sentiment summary
      - recent trending topics
    """
    today = date.today()
    filter_date_from = date_from or today
    filter_date_to = date_to or today

    # ── 1. Summary counts ─────────────────────────────────────────────────────
    trending_q = select(func.count(TrendingTopic.id))
    trending_q = _apply_date_filter(trending_q, TrendingTopic.fetched_at, filter_date_from, filter_date_to)
    total_trending_today = (await db.scalar(trending_q)) or 0

    kw_q = select(func.count(Keyword.id))
    if project_id:
        kw_q = kw_q.where(Keyword.project_id == project_id)
    total_keywords = (await db.scalar(kw_q)) or 0

    post_q = select(func.count(Post.id)).where(Post.platform == "youtube")
    if project_id:
        post_q = post_q.join(Keyword, Post.keyword_id == Keyword.id).where(
            Keyword.project_id == project_id
        )
    total_videos = (await db.scalar(post_q)) or 0

    comment_q = select(func.count(Comment.id)).join(
        Post, Comment.post_id == Post.id
    ).where(Post.platform == "youtube")
    if project_id:
        comment_q = comment_q.join(Keyword, Post.keyword_id == Keyword.id).where(
            Keyword.project_id == project_id
        )
    total_comments = (await db.scalar(comment_q)) or 0

    analyzed_q = select(func.count(LexiconAnalysis.id))
    if project_id:
        analyzed_q = analyzed_q.join(
            Keyword, LexiconAnalysis.keyword_id == Keyword.id
        ).where(Keyword.project_id == project_id)
    total_analyzed = (await db.scalar(analyzed_q)) or 0

    # ── 2. Global sentiment distribution ─────────────────────────────────────
    label_q = select(LexiconAnalysis.label)
    if project_id:
        label_q = label_q.join(
            Keyword, LexiconAnalysis.keyword_id == Keyword.id
        ).where(Keyword.project_id == project_id)

    label_rows = await db.scalars(label_q)
    label_counter = Counter(label_rows.all())
    label_total = sum(label_counter.values())

    sentiment_overview = [
        SentimentDistributionItem(
            label=lbl,
            count=label_counter.get(lbl, 0),
            percentage=round(label_counter.get(lbl, 0) / label_total * 100, 2) if label_total else 0.0,
        )
        for lbl in ["positif", "negatif", "netral"]
    ]

    # ── 3. Per-keyword sentiment summary ─────────────────────────────────────
    kw_list_q = select(Keyword)
    if project_id:
        kw_list_q = kw_list_q.where(Keyword.project_id == project_id)
    kw_list_q = kw_list_q.order_by(Keyword.created_at.desc()).limit(20)

    keywords = list((await db.scalars(kw_list_q)).all())

    kw_summaries: list[KeywordSentimentSummary] = []
    for kw in keywords:
        kw_labels = await db.scalars(
            select(LexiconAnalysis.label).where(LexiconAnalysis.keyword_id == kw.id)
        )
        kw_counter = Counter(kw_labels.all())
        kw_total = sum(kw_counter.values())

        vid_count = (await db.scalar(
            select(func.count(Post.id)).where(
                Post.keyword_id == kw.id, Post.platform == "youtube"
            )
        )) or 0

        kw_summaries.append(KeywordSentimentSummary(
            keyword_id=kw.id,
            keyword_text=kw.keyword,
            total_videos=vid_count,
            total_comments=kw_total,
            positif=kw_counter.get("positif", 0),
            negatif=kw_counter.get("negatif", 0),
            netral=kw_counter.get("netral", 0),
            dominant_sentiment=kw_counter.most_common(1)[0][0] if kw_counter else "netral",
        ))

    # ── 4. Recent trending ────────────────────────────────────────────────────
    recent_trending = list((await db.scalars(
        select(TrendingTopic)
        .order_by(TrendingTopic.fetched_at.desc())
        .limit(10)
    )).all())

    return DashboardResponse(
        summary=DashboardSummary(
            total_trending_today=total_trending_today,
            total_keywords=total_keywords,
            total_videos=total_videos,
            total_comments=total_comments,
            total_analyzed=total_analyzed,
            last_updated=datetime.now(timezone.utc),
        ),
        sentiment_overview=sentiment_overview,
        keyword_summaries=kw_summaries,
        recent_trending=[
            TrendingItemResponse(
                rank=t.rank,
                title=t.title,
                traffic=t.traffic or "",
                description=t.description or "",
                published_at=t.published_at,
            )
            for t in recent_trending
        ],
    )


# ─────────────────────────────────────────────────────────────────────────────
# PIPELINE STATUS PER KEYWORD
# ─────────────────────────────────────────────────────────────────────────────

async def get_keyword_pipeline_status(
    db: AsyncSession,
    keyword_id: uuid.UUID,
) -> KeywordPipelineStatus:
    """Progress pipeline untuk satu keyword: videos → comments → analyzed."""
    keyword = await db.get(Keyword, keyword_id)
    if not keyword:
        from app.shared.exceptions import NotFoundError
        raise NotFoundError("Keyword", str(keyword_id))

    total_videos = (await db.scalar(
        select(func.count(Post.id)).where(
            Post.keyword_id == keyword_id, Post.platform == "youtube"
        )
    )) or 0

    total_comments = (await db.scalar(
        select(func.count(Comment.id))
        .join(Post, Comment.post_id == Post.id)
        .where(Post.keyword_id == keyword_id)
    )) or 0

    total_analyzed = (await db.scalar(
        select(func.count(LexiconAnalysis.id)).where(
            LexiconAnalysis.keyword_id == keyword_id
        )
    )) or 0

    label_rows = await db.scalars(
        select(LexiconAnalysis.label).where(LexiconAnalysis.keyword_id == keyword_id)
    )
    counter = Counter(label_rows.all())

    return KeywordPipelineStatus(
        keyword_id=keyword_id,
        keyword_text=keyword.keyword,
        is_active=keyword.is_active,
        total_videos=total_videos,
        total_comments=total_comments,
        total_analyzed=total_analyzed,
        coverage_pct=round(total_analyzed / total_comments * 100, 1) if total_comments else 0.0,
        positif=counter.get("positif", 0),
        negatif=counter.get("negatif", 0),
        netral=counter.get("netral", 0),
    )


# ─────────────────────────────────────────────────────────────────────────────
# SMART SEARCH — cek DB dulu, auto-crawl jika belum ada
# ─────────────────────────────────────────────────────────────────────────────

async def smart_search_youtube(
    db: AsyncSession,
    q: str,
    max_pages: int = 1,
    max_comments_per_video: int = 20,
    max_comment_pages: int = 1,
    force_refresh: bool = False,
) -> dict:
    """
    Smart search YouTube — satu call, langsung dapat hasil:

    1. Jika keyword + data sudah ada di DB → kembalikan langsung (instant)
    2. Jika keyword baru / force_refresh → crawl synchronous mini:
         a. Fetch video (1 page ~20 video) → simpan ke DB
         b. Collect komentar untuk 3 video teratas → simpan + analisis sentimen
         c. Return hasilnya langsung ke user
         d. Queue Celery untuk crawl lebih dalam di background (semua halaman)
    3. force_refresh=True → tampilkan data lama + crawl ulang
    """
    from app.domain.projects.models import Project
    from app.workers.youtube_worker import collect_youtube_pipeline_task

    q_clean = q.strip()

    # ── 1. Cek keyword di DB (case-insensitive) ───────────────────────────────
    existing_kw = await db.scalar(
        select(Keyword).where(
            func.lower(Keyword.keyword) == q_clean.lower()
        ).limit(1)
    )

    if existing_kw:
        video_count = (await db.scalar(
            select(func.count(Post.id)).where(
                Post.keyword_id == existing_kw.id,
                Post.platform == "youtube",
            )
        )) or 0

        if video_count > 0 and not force_refresh:
            # Data sudah ada → return langsung
            return await _build_smart_search_result(db, existing_kw)

        if video_count > 0 and force_refresh:
            # Data lama ada, crawl ulang di background, return data lama
            task = collect_youtube_pipeline_task.delay(
                str(existing_kw.id),
                max_pages=max_pages,
                max_comments_per_video=max_comments_per_video,
                max_comment_pages=max_comment_pages,
            )
            result = await _build_smart_search_result(db, existing_kw)
            result["status"] = "refreshing"
            result["message"] = "Data lama ditampilkan. Pipeline crawl terbaru berjalan di background."
            result["job_id"] = task.id
            return result

        kw_id = existing_kw.id
        kw = existing_kw
    else:
        # ── 2. Keyword baru → buat record ─────────────────────────────────────
        from app.domain.projects.models import Project as ProjectModel
        from app.domain.users.models import User as UserModel

        project_id = await db.scalar(
            select(Project.id).where(Project.is_active == True).limit(1)  # noqa: E712
        )
        if not project_id:
            # Auto-buat project default jika belum ada
            first_user = await db.scalar(select(UserModel.id).limit(1))
            if not first_user:
                return {
                    "status": "error",
                    "message": "Tidak ada user di database. Daftar dulu via /api/v1/auth/register.",
                }
            default_project = ProjectModel(
                user_id=first_user,
                name="Default Project",
                description="Auto-created project untuk YouTube Intelligence",
                is_active=True,
            )
            db.add(default_project)
            await db.flush()
            project_id = default_project.id
            await db.commit()

        kw = Keyword(project_id=project_id, keyword=q_clean, is_active=True)
        db.add(kw)
        await db.flush()
        kw_id = kw.id           # simpan ID sebelum commit meng-expire object
        await db.commit()

    # ── 3. Crawl synchronous mini (langsung dalam request, tidak via Celery) ──
    #    a. Fetch + simpan video (1 halaman = ~20 video)
    from app.repositories.keyword_repository import KeywordRepository
    from app.services.collector.service import CollectorService

    kw_repo = KeywordRepository(db)
    svc = CollectorService(kw_repo)
    collection_result = await svc.collect_for_platform(
        keyword_id=kw_id,
        platform="youtube",
        max_pages=1,        # 1 halaman (~20 video), lalu dibatasi 2 di bawah
    )

    #    b. Ambil video yang baru tersimpan, collect komentar untuk 2 video pertama
    db.expire_all()             # synchronous — bukan await
    fresh_posts = list((await db.scalars(
        select(Post)
        .where(Post.keyword_id == kw_id, Post.platform == "youtube")
        .order_by(Post.collected_at.desc())
        .limit(2)           # hemat token: 2 video saja
    )).all())

    for post in fresh_posts:
        try:
            await collect_comments_for_video(
                db=db,
                post_id=post.id,
                keyword_id=kw_id,
                max_comments=5,     # hemat token: 5 komentar per video
                max_pages=1,
            )
        except Exception:
            pass  # lanjut ke video berikutnya jika satu gagal

    #    c. Queue Celery untuk crawl lebih dalam di background
    task = collect_youtube_pipeline_task.delay(
        str(kw_id),
        max_pages=max_pages,
        max_comments_per_video=max_comments_per_video,
        max_comment_pages=max_comment_pages,
    )

    #    d. Build result — ambil keyword object fresh dari DB
    db.expire_all()             # synchronous
    kw_fresh = await db.get(Keyword, kw_id)
    result = await _build_smart_search_result(db, kw_fresh)
    result["status"] = "ready"
    result["message"] = (
        f"Berhasil crawl {collection_result.new_posts} video baru. "
        f"Pipeline lanjutkan crawl lebih dalam di background (job_id: {task.id})."
    )
    result["background_job_id"] = task.id
    result["poll_url"] = f"/api/v1/youtube/status?keyword_id={kw_id}"
    return result


# ─────────────────────────────────────────────────────────────────────────────
# SEARCH VIDEO PALING BARU DIUPLOAD (2026-07-16) -- beda dari smart_search_youtube
# di atas (order=relevance, tidak peduli kebaruan): fungsi ini KHUSUS cari video
# ter-upload dalam `hours_back` jam terakhir, urut PALING BARU duluan, lewat
# YouTube Data API v3 LANGSUNG (search.list?publishedAfter=...&order=date) --
# BUKAN via EnsembleData/fallback biasa, karena EnsembleData tidak native
# support filter tanggal upload.
# ─────────────────────────────────────────────────────────────────────────────

async def search_recent_uploads(
    db: AsyncSession,
    keyword: str,
    hours_back: int = 24,
    max_results: int = 50,
    analyze_sentiment: bool = True,
    sentiment_top_n: int = 5,
    max_comments_per_video: int = 20,
) -> dict:
    """
    Cari video YouTube ter-upload dalam `hours_back` jam terakhir (default 24)
    utk `keyword`, urut PALING BARU duluan (video umur 1 jam pun HARUS tetap
    ketemu -- TIDAK ada publishedBefore, sengaja tidak exclude yang paling
    fresh, lihat YouTubeDataAPIClient.search_recent()). Hasil disimpan sbg
    Post baru (keyword dibuat/dipakai ulang sama seperti smart_search_youtube),
    di-enrich views/likes/comments (search.list TIDAK sertakan statistics,
    lihat enrich_youtube_statistics()).

    Ditambahkan 2026-07-17: kalau `analyze_sentiment=True` (default), setelah
    post baru tersimpan, `sentiment_top_n` video PALING BARU (bukan semua --
    tiap video butuh 1 panggilan komentar terpisah, mahal kalau video-nya
    banyak) langsung diambil komentarnya + dijalankan lexicon sentiment (pola
    SAMA persis dgn smart_search_youtube), hasilnya disertakan di response
    `sentiment` supaya bisa langsung dilihat TANPA perlu panggil endpoint
    terpisah. Untuk sentiment SEMUA post keyword ini (bukan cuma yg baru),
    lihat GET /youtube/sentiment/distribution?keyword_id=... yang sudah ada.

    CATATAN KUOTA: pakai YouTube Data API v3 LANGSUNG (bukan EnsembleData-first
    spt pipeline lain) -- search.list = 100 unit/call dari kuota gratis
    10.000/hari yg SAMA dgn fallback EnsembleData (lihat
    project_youtube_quota_incident_2026_07 di memory) -- pemakaian intensif
    endpoint ini ikut mengurangi jatah harian itu.
    """
    from app.domain.projects.models import Project
    from app.domain.users.models import User as UserModel
    from app.integrations.youtube_data_api.client import YouTubeDataAPIClient
    from app.repositories.post_repository import PostRepository
    from app.services.processing.normalizer import YouTubeNormalizer, enrich_youtube_statistics
    from app.shared.config import settings

    if not settings.youtube_data_api_key:
        return {"status": "error", "message": "YOUTUBE_DATA_API_KEY belum di-set di server"}

    q_clean = keyword.strip()
    if not q_clean:
        return {"status": "error", "message": "Keyword tidak boleh kosong"}

    # ── Get-or-create Keyword (pola sama dgn smart_search_youtube) ───────────
    kw = await db.scalar(select(Keyword).where(func.lower(Keyword.keyword) == q_clean.lower()).limit(1))
    if not kw:
        project_id = await db.scalar(select(Project.id).where(Project.is_active == True).limit(1))  # noqa: E712
        if not project_id:
            first_user = await db.scalar(select(UserModel.id).limit(1))
            if not first_user:
                return {"status": "error", "message": "Tidak ada user di database. Daftar dulu via /api/v1/auth/register."}
            default_project = Project(user_id=first_user, name="Default Project", is_active=True)
            db.add(default_project)
            await db.flush()
            project_id = default_project.id
        kw = Keyword(project_id=project_id, keyword=q_clean, is_active=True)
        db.add(kw)
        await db.flush()

    # WAJIB simpan sbg UUID biasa (bukan akses kw.id terus-menerus) --
    # collect_comments_for_video() di bawah bisa rollback() internal kalau
    # gagal (provider down dll), yg meng-EXPIRE seluruh objek di session
    # termasuk `kw`. Akses `kw.id` SETELAH itu bikin AsyncSession coba
    # auto-refresh secara sinkron -> MissingGreenlet crash (ditemukan
    # 2026-07-17 lewat test real-DB). `kw_id` sbg uuid.UUID polos kebal
    # dari masalah ini krn bukan atribut ORM yg bisa expired.
    kw_id = kw.id

    # ── Cari via YouTube Data API v3 langsung, filter publishedAfter ─────────
    published_after = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    client = YouTubeDataAPIClient(api_key=settings.youtube_data_api_key)
    raw = await client.search_recent(q_clean, published_after=published_after, max_results=max_results)

    connector = YouTubeConnector(client=None)  # extract_posts() murni parsing, tidak pakai self.client
    items = connector.extract_posts(raw)

    normalizer = YouTubeNormalizer()
    posts = normalizer.normalize(items, kw_id)

    post_repo = PostRepository(db)
    existing = await post_repo.get_existing_external_ids([p.external_id for p in posts], "youtube")
    new_posts = [p for p in posts if p.external_id not in existing]
    duplicate_posts = [p for p in posts if p.external_id in existing]

    if new_posts:
        await enrich_youtube_statistics(new_posts)
        inserted = await post_repo.bulk_create(new_posts)
    else:
        inserted = 0
    await db.commit()

    # ── Video DUPLIKAT (sudah pernah ditemukan run sebelumnya) -- objek
    # `p`-nya dibangun ULANG dari nol oleh normalizer TIAP kali fungsi ini
    # dipanggil (metadata_.views selalu 0 di awal, comment 'diisi
    # enrich_youtube_statistics()'), tapi enrich_youtube_statistics() di atas
    # HANYA jalan utk new_posts -- video duplikat TIDAK PERNAH di-enrich lagi
    # di sini, jadi field `views` di response "videos" selalu 0 utk video
    # lama walau datanya SUDAH ada di DB. Fix: ambil angka NYATA dari DB --
    # prioritaskan youtube_video_metadata (paling akurat, dijaga Views Refresh
    # Agent) drpd posts.metrics (bisa basi/belum pernah di-enrich sejak awal).
    if duplicate_posts:
        dup_ids = [p.external_id for p in duplicate_posts]
        real_stats_rows = (await db.execute(text("""
            SELECT p.external_id, p.metrics,
                   m.views AS meta_views, m.likes AS meta_likes
            FROM posts p
            LEFT JOIN youtube_video_metadata m ON m.post_id = p.id
            WHERE p.platform = 'youtube' AND p.external_id = ANY(:ids)
        """), {"ids": dup_ids})).mappings().all()
        real_stats_by_id = {}
        for row in real_stats_rows:
            metrics = row["metrics"] or {}
            real_stats_by_id[row["external_id"]] = {
                "views": row["meta_views"] if row["meta_views"] is not None else metrics.get("views", 0),
                "likes": row["meta_likes"] if row["meta_likes"] is not None else metrics.get("likes", 0),
            }
        for p in duplicate_posts:
            real = real_stats_by_id.get(p.external_id)
            if real:
                p.metadata_["views"] = real["views"]
                p.metadata_["likes"] = real["likes"]

    # ── Fetch komentar + lexicon sentiment utk N video PALING BARU ───────────
    # (bukan semua -- tiap video = 1 panggilan komentar terpisah, mahal kalau
    # video-nya banyak). new_posts sudah terurut sama seperti hasil Google
    # (paling baru duluan), jadi new_posts[:sentiment_top_n] otomatis video
    # yg paling baru diupload.
    sentiment_results: list[dict] = []
    if analyze_sentiment and new_posts:
        for post in new_posts[:sentiment_top_n]:
            try:
                cr = await collect_comments_for_video(
                    db=db, post_id=post.id, keyword_id=kw_id,
                    max_comments=max_comments_per_video, max_pages=1,
                )
                label_rows = (await db.scalars(
                    select(LexiconAnalysis.label)
                    .join(Comment, Comment.id == LexiconAnalysis.comment_id)
                    .where(Comment.post_id == post.id)
                )).all()
                counter = Counter(label_rows)
                sentiment_results.append({
                    "video_id": post.external_id,
                    "comments_fetched": cr.comments_fetched,
                    "comments_analyzed": cr.comments_analyzed,
                    "positif": counter.get("positif", 0),
                    "negatif": counter.get("negatif", 0),
                    "netral": counter.get("netral", 0),
                    "errors": cr.errors,
                })
            except Exception as exc:
                sentiment_results.append({"video_id": post.external_id, "error": str(exc)})

    return {
        "status": "ok",
        "keyword": q_clean,
        "keyword_id": str(kw_id),
        "hours_back": hours_back,
        "found": len(posts),
        "new": inserted,
        "duplicate": len(posts) - len(new_posts),
        "window": {
            "from": published_after.isoformat(),
            "to": datetime.now(timezone.utc).isoformat(),
        },
        "sentiment": sentiment_results,
        "sentiment_note": (
            f"Komentar+sentiment cuma diambil utk {sentiment_top_n} video PALING BARU dari yg baru ditemukan "
            "(hemat kuota/waktu) -- utk sentiment SEMUA post keyword ini, pakai "
            "GET /youtube/sentiment/distribution?keyword_id=" + str(kw_id)
        ),
        "videos": [
            {
                "video_id": p.external_id,
                "title": p.content,
                "channel": p.author,
                "url": p.url,
                "thumbnail": p.metadata_.get("thumbnail", ""),
                "views": p.metadata_.get("views", 0),
                "likes": p.metadata_.get("likes", 0),
                "published_at": p.published_at.isoformat() if p.published_at else None,
            }
            for p in posts
        ],
    }


async def _build_smart_search_result(db: AsyncSession, keyword: Keyword) -> dict:
    """Bangun response lengkap dari data DB yang sudah ada."""
    # Videos (terbaru 20)
    posts = list((await db.scalars(
        select(Post)
        .where(Post.keyword_id == keyword.id, Post.platform == "youtube")
        .order_by(Post.collected_at.desc())
        .limit(20)
    )).all())

    videos = []
    for p in posts:
        meta = p.metadata_ or {}
        videos.append({
            "id": str(p.id),
            "video_id": p.external_id,
            "url": p.url or f"https://youtube.com/watch?v={p.external_id}",
            "title": p.content,
            "channel": p.author,
            "view_count": meta.get("views", meta.get("view_count", 0)),
            "thumbnail_url": meta.get("thumbnail", meta.get("thumbnail_url", "")),
            "collected_at": p.collected_at.isoformat() if p.collected_at else None,
        })

    # Sample komentar + sentimen (20 terbaru)
    rows = (await db.execute(
        select(Comment, LexiconAnalysis)
        .join(Post, Comment.post_id == Post.id)
        .outerjoin(LexiconAnalysis, LexiconAnalysis.comment_id == Comment.id)
        .where(Post.keyword_id == keyword.id)
        .order_by(Comment.created_at.desc())
        .limit(20)
    )).all()

    sample_comments = [
        {
            "id": str(comment.id),
            "content": comment.content,
            "author": comment.author,
            # final_label (mayoritas Sentiment Agent) dipakai kalau sudah
            # direview, jatuh ke label lexicon asli kalau belum (2026-07-18).
            "sentiment": (analysis.final_label or analysis.label) if analysis else None,
            "sentiment_source": ("llm_reviewed" if analysis and analysis.final_label else "lexicon_only") if analysis else None,
            "score": round(analysis.score, 3) if analysis else None,
            "created_at": comment.created_at.isoformat() if comment.created_at else None,
        }
        for comment, analysis in rows
    ]

    # Distribusi sentimen -- pakai final_label kalau ada, jatuh ke lexicon
    label_rows = list((await db.execute(
        select(func.coalesce(LexiconAnalysis.final_label, LexiconAnalysis.label))
        .where(LexiconAnalysis.keyword_id == keyword.id)
    )).scalars().all())
    counter = Counter(label_rows)
    total_analyzed = sum(counter.values())

    total_videos = (await db.scalar(
        select(func.count(Post.id)).where(
            Post.keyword_id == keyword.id, Post.platform == "youtube"
        )
    )) or 0

    total_comments = (await db.scalar(
        select(func.count(Comment.id))
        .join(Post, Comment.post_id == Post.id)
        .where(Post.keyword_id == keyword.id)
    )) or 0

    sentiment = {
        lbl: {
            "count": counter.get(lbl, 0),
            "percentage": round(counter.get(lbl, 0) / total_analyzed * 100, 1) if total_analyzed else 0.0,
        }
        for lbl in ["positif", "negatif", "netral"]
    }

    return {
        "status": "ready",
        "keyword_id": str(keyword.id),
        "keyword": keyword.keyword,
        "stats": {
            "total_videos": total_videos,
            "total_comments": total_comments,
            "total_analyzed": total_analyzed,
            "coverage_pct": round(total_analyzed / total_comments * 100, 1) if total_comments else 0.0,
        },
        "sentiment": {**sentiment, "dominant": counter.most_common(1)[0][0] if counter else "netral"},
        "videos": videos,
        "sample_comments": sample_comments,
    }


# ─────────────────────────────────────────────────────────────────────────────
# TRENDING PUBLIC DASHBOARD -- tanpa auth, untuk di-share sbg link publik
# (mirip /monitor-public). Data GLOBAL (sama utk semua orang), jadi cukup
# satu URL tetap -- tidak perlu sistem token per-share seperti Maltego,
# karena isinya bukan dashboard personal per-user.
# ─────────────────────────────────────────────────────────────────────────────

TRENDING_PUBLIC_TOP_VIDEOS = 5  # video terpopuler yg ditampilkan per topik


async def get_trending_public_dashboard(
    db: AsyncSession, geo: str = "ID", days: int = 7,
) -> dict:
    """
    Trending topic YouTube 7 hari terakhir (hari ini s/d `days`-1 hari lalu),
    dikelompokkan per tanggal, tiap topik disertai video terpopulernya
    (urut views terbanyak, maks TRENDING_PUBLIC_TOP_VIDEOS).

    Keyword<->TrendingTopic TIDAK ada FK -- dicocokkan lewat teks
    (Keyword.keyword == TrendingTopic.title), sesuai cara
    fetch_and_store_trending() membuat Keyword di atas. Topik yang belum
    sempat dapat video (baru fetched, belum kepilih scraping) tetap
    ditampilkan dengan video_count=0/top_videos=[] -- bukan dihilangkan.
    """
    today = date.today()
    start_date = today - timedelta(days=days - 1)

    topics = list((await db.scalars(
        select(TrendingTopic)
        .where(
            TrendingTopic.geo == geo,
            func.date(TrendingTopic.fetched_at) >= start_date,
            func.date(TrendingTopic.fetched_at) <= today,
        )
        .order_by(TrendingTopic.fetched_at.asc(), TrendingTopic.rank.asc())
    )).all())

    # ── Cocokkan topik -> keyword_id lewat judul (batch, 1 query) ────────────
    titles = list({t.title for t in topics})
    title_to_kwid: dict[str, uuid.UUID] = {}
    if titles:
        rows = (await db.execute(
            select(Keyword.keyword, Keyword.id).where(Keyword.keyword.in_(titles))
        )).all()
        title_to_kwid = {row[0]: row[1] for row in rows}

    # ── Top-N video per keyword_id + total video_count, 1 query (window fn) ──
    videos_by_kwid: dict[str, list[dict]] = {}
    count_by_kwid: dict[str, int] = {}
    kw_ids = list({str(kwid) for kwid in title_to_kwid.values()})
    if kw_ids:
        rows = (await db.execute(text("""
            WITH ranked AS (
                SELECT
                    keyword_id, content AS title, url, author AS channel,
                    (metadata->>'thumbnail') AS thumbnail,
                    COALESCE((metadata->>'views')::bigint, 0) AS views,
                    COALESCE((metadata->>'likes')::bigint, 0) AS likes,
                    published_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY keyword_id
                        ORDER BY COALESCE((metadata->>'views')::bigint, 0) DESC
                    ) AS rn,
                    COUNT(*) OVER (PARTITION BY keyword_id) AS video_count
                FROM posts
                WHERE platform = 'youtube' AND keyword_id = ANY(:kw_ids)
            )
            SELECT * FROM ranked WHERE rn <= :top_n ORDER BY keyword_id, rn
        """), {"kw_ids": kw_ids, "top_n": TRENDING_PUBLIC_TOP_VIDEOS})).mappings().all()

        for r in rows:
            kwid = str(r["keyword_id"])
            count_by_kwid[kwid] = r["video_count"]
            videos_by_kwid.setdefault(kwid, []).append({
                "title":        r["title"],
                "url":          r["url"],
                "channel":      r["channel"],
                "thumbnail":    r["thumbnail"],
                "views":        r["views"],
                "likes":        r["likes"],
                "published_at": r["published_at"].isoformat() if r["published_at"] else None,
            })

    # ── Susun per hari ─────────────────────────────────────────────────────
    days_map: dict[str, list[dict]] = {}
    for t in topics:
        day_key = t.fetched_at.date().isoformat()
        kwid = title_to_kwid.get(t.title)
        kwid_str = str(kwid) if kwid else None
        days_map.setdefault(day_key, []).append({
            "rank":        t.rank,
            "title":       t.title,
            "traffic":     t.traffic,
            "description": t.description,
            "fetched_at":  t.fetched_at.isoformat(),
            "video_count": count_by_kwid.get(kwid_str, 0),
            "top_videos":  videos_by_kwid.get(kwid_str, []),
        })

    day_list = [(today - timedelta(days=i)) for i in range(days - 1, -1, -1)]  # oldest -> terbaru
    return {
        "geo": geo,
        "days": [
            {"date": d.isoformat(), "topics": days_map.get(d.isoformat(), [])}
            for d in day_list
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _apply_date_filter(query, col, date_from: date | None, date_to: date | None, hour: int | None = None):
    """Terapkan filter tanggal/jam ke query. Data tidak pernah dihapus — gunakan filter ini."""
    if date_from:
        query = query.where(col >= datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc))
    if date_to:
        end = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59, tzinfo=timezone.utc)
        query = query.where(col <= end)
    if hour is not None:
        query = query.where(extract("hour", col) == hour)
    return query


def _get_comment_id(c: dict) -> str:
    props = c.get("properties") or {}
    return props.get("commentId") or c.get("commentId") or c.get("id") or ""


def _get_comment_text(c: dict) -> str | None:
    props = c.get("properties") or {}
    content = props.get("content") or {}
    if isinstance(content, dict):
        text = content.get("content") or "".join(
            r.get("text", "") for r in content.get("runs", [])
        )
        if text:
            return text
    return props.get("text") or c.get("text") or None


def _get_comment_author(c: dict) -> str | None:
    author = c.get("author") or {}
    return author.get("displayName") or author.get("channelId") or None


def _parse_count(raw: str) -> int:
    """Parse '260K' → 260000, '1.2M' → 1200000, '123' → 123."""
    if not raw:
        return 0
    raw = str(raw).strip().replace(",", "")
    try:
        if raw.endswith("K"):
            return int(float(raw[:-1]) * 1_000)
        if raw.endswith("M"):
            return int(float(raw[:-1]) * 1_000_000)
        return int(float(raw))
    except (ValueError, AttributeError):
        return 0
