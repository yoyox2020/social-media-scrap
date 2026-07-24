"""Status scraping per-platform (2026-07-23, permintaan user "status
monitor di kartunya harus ada informasi terkait, jadi ketika ada agent
baru terbentuk akan otomatis menyesuaikan" + "biarkan diperiksa sama
agent, dan memberikan laporan"). GENERIK -- baca platform LANGSUNG
dari data `scrape_runs` yg SUDAH ADA (bukan hardcode daftar platform),
jadi platform baru otomatis ikut muncul begitu ada run pertamanya,
TANPA kode baru di sini."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.comments.models import Comment
from app.domain.posts.models import Post
from app.domain.scrape_runs.models import ScrapeRun


async def get_monitoring_summary(db: AsyncSession) -> list[dict]:
    platforms_result = await db.execute(select(ScrapeRun.platform).distinct())
    platforms = sorted(row[0] for row in platforms_result.all())

    since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    summary: list[dict] = []

    for platform in platforms:
        last_run = await db.scalar(
            select(ScrapeRun).where(ScrapeRun.platform == platform).order_by(ScrapeRun.started_at.desc()).limit(1)
        )

        stats_row = (await db.execute(
            select(
                func.count().filter(ScrapeRun.status == "success").label("success"),
                func.count().filter(ScrapeRun.status == "failed").label("failed"),
                func.count().filter(ScrapeRun.status == "running").label("running"),
                func.count().label("total"),
                func.sum(ScrapeRun.videos_new).label("videos_new_24h"),
            ).where(ScrapeRun.platform == platform, ScrapeRun.started_at >= since_24h)
        )).one()

        # Run yg statusnya "running" tapi sudah > 15 menit -- kemungkinan
        # macet/orphan (proses worker mati tanpa sempat update status,
        # lihat insiden 2026-07-23 topik "korupsi" macet 2 jam) --
        # ditandai jelas di laporan, bukan disembunyikan sbg "lagi jalan".
        stuck_threshold = datetime.now(timezone.utc) - timedelta(minutes=15)
        stuck_count = await db.scalar(
            select(func.count()).select_from(ScrapeRun).where(
                ScrapeRun.platform == platform, ScrapeRun.status == "running",
                ScrapeRun.started_at < stuck_threshold,
            )
        )

        summary.append({
            "platform": platform,
            "last_run": {
                "status": last_run.status if last_run else None,
                "keyword_text": last_run.keyword_text if last_run else None,
                "triggered_by": last_run.triggered_by if last_run else None,
                "started_at": last_run.started_at.isoformat() if last_run and last_run.started_at else None,
                "finished_at": last_run.finished_at.isoformat() if last_run and last_run.finished_at else None,
                "videos_new": last_run.videos_new if last_run else None,
                "error_message": last_run.error_message if last_run else None,
            } if last_run else None,
            "last_24h": {
                "success": stats_row.success or 0,
                "failed": stats_row.failed or 0,
                "running": stats_row.running or 0,
                "total": stats_row.total or 0,
                "videos_new": int(stats_row.videos_new_24h or 0),
            },
            "stuck_runs": stuck_count or 0,
        })

    return summary


async def get_completeness_summary(db: AsyncSession) -> list[dict]:
    """Persentase kelengkapan metadata per-platform (2026-07-24, permintaan
    user "pastikan ada agent yang selalu memonitor dan mengupdatenya") --
    jawaban VISIBLE ke pertanyaan itu: dashboard ini yg jadi "mata"
    memantau hasil kerja SEMUA agent backfill/completeness (youtube.
    audit_completeness, youtube.backfill_missing_comments, tiktok.
    backfill_author_followers, facebook.backfill_metadata, instagram.
    backfill_metadata) -- baca LANGSUNG dari tabel `posts` (bukan
    hardcode daftar platform), jadi platform baru otomatis ikut muncul."""
    platforms_result = await db.execute(select(Post.platform).distinct())
    platforms = sorted(row[0] for row in platforms_result.all())

    summary: list[dict] = []
    for platform in platforms:
        row = (await db.execute(
            select(
                func.count().label("total"),
                func.count().filter(Post.metadata_["trend_score"].astext.is_not(None)).label("have_score"),
                func.count().filter(Post.metadata_["audience_size"].astext.is_not(None)).label("have_audience_size"),
            ).where(Post.platform == platform)
        )).one()
        total = row.total or 0

        # Komentar (2026-07-24, permintaan user "sediakan agent yg memantau
        # komentar belum discrape") -- "seharusnya py komentar" =
        # metrics.comments>0. "benar2 tersimpan" HARUS subset dari itu (BUKAN
        # dihitung terpisah -- bug awal: post dgn metrics.comments=NULL/0 tapi
        # SUDAH py komentar lama tersimpan bikin coverage >100%, krn 2 kondisi
        # dihitung independen, tidak di-AND-kan jadi 1 kriteria yg sama).
        posts_with_comments_subq = select(Comment.post_id).distinct().subquery()
        should_have_expr = func.coalesce(cast(Post.metrics["comments"].astext, Integer), 0) > 0
        has_saved_expr = Post.id.in_(select(posts_with_comments_subq.c.post_id))
        comment_row = (await db.execute(
            select(
                func.count().filter(should_have_expr).label("should_have_comments"),
                func.count().filter(should_have_expr, has_saved_expr).label("have_any_comment_saved"),
            ).where(Post.platform == platform)
        )).one()
        should_have = comment_row.should_have_comments or 0
        have_saved = comment_row.have_any_comment_saved or 0

        summary.append({
            "platform": platform,
            "total_posts": total,
            "score_coverage_pct": round((row.have_score or 0) / total * 100, 1) if total else 0,
            "audience_size_coverage_pct": round((row.have_audience_size or 0) / total * 100, 1) if total else 0,
            "posts_missing_score": total - (row.have_score or 0),
            "posts_missing_audience_size": total - (row.have_audience_size or 0),
            "posts_expected_to_have_comments": should_have,
            "posts_with_comments_saved": have_saved,
            "comment_coverage_pct": round(have_saved / should_have * 100, 1) if should_have else None,
            "posts_missing_comments": max(should_have - have_saved, 0),
        })
    return summary
