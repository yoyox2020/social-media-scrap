"""
Instagram Thumbnail Backfill Agent -- 2026-07-20, permintaan user
("buat jagaan satu khusus worker pengisi data lama instagram mirip agent")
setelah ketahuan post Instagram LAMA (di-scrape sebelum fix
[[reference_instagram_post_scraper_actor]]) genuinely tidak punya
photo_url dan TIDAK otomatis diperbaiki oleh fix itu (fix cuma berlaku
scrape BARU).

Cara kerja:
1. Cari akun Instagram yang punya post dgn photo_url KOSONG, urut dari yg
   PALING BANYAK post rusak (paling worth di-backfill duluan).
2. Utk tiap akun (dibatasi `daily_budget`/run -- kendali biaya), PILIH
   ACAK provider "apify_post_scraper" ATAU "ensembledata" (permintaan user
   "gabungan saja ensemble dan apify random" -- supaya beban kuota
   TERSEBAR ke 2 pihak ketiga, bukan cuma satu terus-menerus). Provider
   "apify" (lama) SENGAJA tidak pernah dipilih di sini -- sudah TERBUKTI
   tidak pernah kirim foto, percuma dicoba ulang.
3. Cocokkan hasil scrape (by shortcode) ke post yg SUDAH ADA di DB yg
   photo_url-nya kosong -- update HANYA field photo_url/media, TIDAK
   menyentuh field lain (caption/likes/dll TETAP nilai lama, ini murni
   backfill foto bukan re-sync data).
4. Post yg TIDAK ketemu di hasil scrape baru (mis. sudah dihapus di
   Instagram) dibiarkan apa adanya -- akan dicoba lagi run berikutnya.
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.domain.posts.models import Post
from app.domain.scrape_runs.models import ScrapeRun
from app.services.instagram_thumbnail_backfill import config as cfg
from app.services.processing.normalizer import _media_list

logger = logging.getLogger(__name__)

MAX_POSTS_PER_ACCOUNT = 50  # dinaikkan 2026-07-20 (dari 12) -- ditemukan live: post LAMA yg
# fotonya kosong sering "terdorong" keluar dari batch 12-post-terbaru krn akun sudah
# posting konten baru sejak terakhir discrape (mis. akun 'adidas'), jadi backfill re-scrape
# sukses tapi 0 post ke-update. 50 jauh lebih mungkin menjangkau post lama itu, trade-off
# panggilan Apify sedikit lebih mahal per akun.
_CANDIDATE_PROVIDERS = ("apify_post_scraper", "ensembledata")


async def get_accounts_missing_photo(db: AsyncSession, limit: int) -> list[tuple[str, int]]:
    """Akun Instagram dgn post yg photo_url-nya kosong, urut PALING BANYAK
    post rusak duluan (paling worth di-backfill)."""
    rows = (await db.execute(text("""
        SELECT author, count(*) AS missing_count
        FROM posts
        WHERE platform = 'instagram'
          AND author IS NOT NULL AND author != ''
          AND (metadata->>'photo_url' IS NULL OR metadata->>'photo_url' = '')
        GROUP BY author
        ORDER BY missing_count DESC
        LIMIT :limit
    """), {"limit": limit})).all()
    return [(r[0], r[1]) for r in rows]


async def _backfill_one_account(db: AsyncSession, username: str) -> dict:
    """Scrape ulang SATU akun (provider ACAK Apify-baru/EnsembleData),
    update photo_url/media post yg SUDAH ADA & kosong fotonya. Return
    ringkasan (utk ScrapeRun + log)."""
    from app.services.instagram.providers.registry import PROVIDERS

    provider_name = random.choice(_CANDIDATE_PROVIDERS)
    provider_cls = PROVIDERS[provider_name]

    started_at = datetime.now(timezone.utc)
    scrape_run = ScrapeRun(
        keyword_text=f"backfill:{username}", platform="instagram_thumbnail_backfill",
        api_source=provider_name, status="running", triggered_by="celery_beat", started_at=started_at,
    )
    db.add(scrape_run)
    await db.commit()

    result = {"username": username, "provider": provider_name, "posts_updated": 0, "error": None}
    try:
        rows = await provider_cls().search_profile(username, max_posts=MAX_POSTS_PER_ACCOUNT, max_comments=0)
    except Exception as exc:
        logger.warning("ig_thumbnail_backfill: gagal scrape %s via %s: %s", username, provider_name, exc)
        scrape_run.status = "failed"
        scrape_run.error_message = str(exc)[:1000]
        scrape_run.finished_at = datetime.now(timezone.utc)
        scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()
        await db.commit()
        result["error"] = str(exc)
        return result

    # ── Kumpulkan shortcode -> photoUrl dari hasil scrape (dedup, ambil yg pertama non-kosong) ──
    photo_by_shortcode: dict[str, str] = {}
    for row in rows:
        photo_url = row.get("photoUrl")
        if not photo_url:
            continue
        post_url = row.get("postUrl") or ""
        match = post_url.rstrip("/").split("/")
        shortcode = match[-1] if match else ""
        if shortcode and shortcode not in photo_by_shortcode:
            photo_by_shortcode[shortcode] = photo_url

    posts_updated = 0
    if photo_by_shortcode:
        existing_posts = (await db.scalars(
            select(Post).where(
                Post.platform == "instagram",
                Post.author == username,
                Post.external_id.in_(list(photo_by_shortcode.keys())),
            )
        )).all()
        for post in existing_posts:
            current_photo = (post.metadata_ or {}).get("photo_url")
            if current_photo:
                continue  # sudah ada foto (mis. dari backfill run sebelumnya), skip
            new_photo = photo_by_shortcode.get(post.external_id)
            if not new_photo:
                continue
            post.metadata_["photo_url"] = new_photo
            flag_modified(post, "metadata_")
            post.media = _media_list(new_photo)
            posts_updated += 1

    scrape_run.status = "success"
    scrape_run.videos_fetched = len(rows)
    scrape_run.videos_new = posts_updated
    scrape_run.finished_at = datetime.now(timezone.utc)
    scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()
    await db.commit()

    result["posts_updated"] = posts_updated
    return result


async def run_thumbnail_backfill(db: AsyncSession) -> dict:
    """Entry point dipanggil worker Celery. Return ringkasan run."""
    if not await cfg.get_enabled():
        logger.info("ig_thumbnail_backfill: dimatikan (tombol OFF), skip")
        return {"skipped": "disabled"}

    budget = await cfg.get_daily_budget()
    accounts = await get_accounts_missing_photo(db, limit=budget)

    if not accounts:
        logger.info("ig_thumbnail_backfill: tidak ada akun dgn post kosong foto")
        await cfg.set_last_run_at(datetime.now(timezone.utc).isoformat())
        return {"accounts_checked": 0, "posts_updated": 0, "results": []}

    results = []
    total_updated = 0
    for username, missing_count in accounts:
        res = await _backfill_one_account(db, username)
        res["missing_count_before"] = missing_count
        results.append(res)
        total_updated += res["posts_updated"]

    await cfg.set_last_run_at(datetime.now(timezone.utc).isoformat())
    summary = {"accounts_checked": len(accounts), "posts_updated": total_updated, "results": results}
    logger.info("ig_thumbnail_backfill: %s", summary)
    return summary


async def get_backfill_status(db: AsyncSession, recent_limit: int = 10) -> dict:
    """Ringkasan status utk monitoring -- berapa akun/post yg masih perlu
    dibackfill + riwayat run terakhir."""
    accounts_remaining = (await db.execute(text("""
        SELECT count(DISTINCT author) FROM posts
        WHERE platform = 'instagram' AND author IS NOT NULL AND author != ''
          AND (metadata->>'photo_url' IS NULL OR metadata->>'photo_url' = '')
    """))).scalar_one()
    posts_remaining = (await db.execute(text("""
        SELECT count(*) FROM posts
        WHERE platform = 'instagram'
          AND (metadata->>'photo_url' IS NULL OR metadata->>'photo_url' = '')
    """))).scalar_one()

    recent_runs = (await db.scalars(
        select(ScrapeRun)
        .where(ScrapeRun.platform == "instagram_thumbnail_backfill")
        .order_by(ScrapeRun.started_at.desc())
        .limit(recent_limit)
    )).all()

    return {
        "enabled": await cfg.get_enabled(),
        "daily_budget": await cfg.get_daily_budget(),
        "last_run_at": await cfg.get_last_run_at(),
        "accounts_still_missing_photo": accounts_remaining,
        "posts_still_missing_photo": posts_remaining,
        "recent_runs": [
            {
                "username": r.keyword_text.replace("backfill:", "", 1),
                "provider": r.api_source, "status": r.status,
                "posts_fetched": r.videos_fetched, "posts_updated": r.videos_new,
                "error_message": r.error_message,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "duration_seconds": r.duration_seconds,
            }
            for r in recent_runs
        ],
    }
