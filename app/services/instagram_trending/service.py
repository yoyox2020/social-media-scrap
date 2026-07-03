"""
Instagram Trending Service.

Orkestrasi:
  1. discover()  — cari trending usernames via provider (pluggable)
  2. score()     — hitung trending_score dari data di DB
  3. scrape()    — auto-scrape top 5: 2 post + 5 komentar per akun

Provider registry — tambah third-party baru di sini:
  PROVIDERS = {
      "ensembledata": EnsembleDataDiscovery,
      "rapidapi":     RapidAPIDiscovery,   # contoh, belum ada
  }
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.instagram_trending.models import InstagramTrendingAccount
from app.services.instagram_trending.providers.ensemble_data import EnsembleDataDiscovery
from app.services.instagram_trending.scorer import calculate as calc_score

logger = logging.getLogger(__name__)

MAX_TRENDING_ACCOUNTS = 5
POSTS_PER_ACCOUNT     = 2
COMMENTS_PER_POST     = 5

# ── Provider registry ─────────────────────────────────────────────────────────
PROVIDERS = {
    "ensembledata": EnsembleDataDiscovery,
    # "rapidapi": RapidAPIDiscovery,   ← colok di sini kalau sudah ada
}


def get_provider(name: str = "ensembledata"):
    cls = PROVIDERS.get(name)
    if not cls:
        raise ValueError(f"Provider '{name}' tidak dikenal. Tersedia: {list(PROVIDERS)}")
    return cls()


# ─────────────────────────────────────────────────────────────────────────────
# 1. DISCOVER
# ─────────────────────────────────────────────────────────────────────────────

async def run_discovery(
    db: AsyncSession,
    provider_name: str = "ensembledata",
    hashtags: list[str] | None = None,
) -> list[InstagramTrendingAccount]:
    """
    Jalankan discovery trending username, simpan / update ke DB.
    Return daftar akun yang ditemukan (baru maupun yang sudah ada).
    """
    provider = get_provider(provider_name)
    discovered = await provider.discover(hashtags=hashtags, limit=30)

    if not discovered:
        logger.warning("run_discovery: tidak ada hasil dari provider=%s", provider_name)
        return []

    results: list[InstagramTrendingAccount] = []
    for item in discovered:
        username = item["username"]
        if not username:
            continue

        # Upsert: kalau sudah ada, update; kalau belum, buat baru
        existing = await db.scalar(
            select(InstagramTrendingAccount).where(
                InstagramTrendingAccount.username == username,
                InstagramTrendingAccount.status == "active",
            )
        )

        hint_log = {
            "type":          "discovery_hint",
            "date":          datetime.now(timezone.utc).date().isoformat(),
            "likes_hint":    item.get("likes_hint", 0),
            "comments_hint": item.get("comments_hint", 0),
            "views_hint":    item.get("views_hint", 0),
            "discovered_via": item.get("discovered_via"),
        }

        if existing:
            existing.followers      = item.get("followers", existing.followers)
            existing.discovered_via = item.get("discovered_via", existing.discovered_via)
            existing.scrape_logs    = (existing.scrape_logs or []) + [hint_log]
            results.append(existing)
        else:
            account = InstagramTrendingAccount(
                username=username,
                display_name=item.get("display_name", ""),
                source=provider_name,
                discovered_via=item.get("discovered_via"),
                followers=item.get("followers", 0),
                status="active",
                scrape_logs=[hint_log],
            )
            db.add(account)
            results.append(account)

    await db.commit()
    logger.info("run_discovery: %d akun ditemukan/diperbarui", len(results))
    return results


# ─────────────────────────────────────────────────────────────────────────────
# 2. SCORE & RANK
# ─────────────────────────────────────────────────────────────────────────────

async def run_scoring(db: AsyncSession) -> list[InstagramTrendingAccount]:
    """
    Hitung trending_score untuk semua akun aktif dari data post di DB.
    Assign rank 1–N. Return top MAX_TRENDING_ACCOUNTS.
    """
    accounts = (await db.scalars(
        select(InstagramTrendingAccount).where(InstagramTrendingAccount.status == "active")
    )).all()

    if not accounts:
        return []

    scored = []
    for account in accounts:
        # Ambil metadata post dari DB
        rows = (await db.execute(text("""
            SELECT metadata FROM posts
            WHERE platform = 'instagram' AND author = :username
            ORDER BY published_at DESC NULLS LAST
            LIMIT 10
        """), {"username": account.username})).mappings().all()

        posts_meta = [r["metadata"] or {} for r in rows]

        # Bootstrap fallback: gunakan discovery hints jika belum ada post di DB
        if not posts_meta:
            hint = next(
                (log for log in reversed(account.scrape_logs or [])
                 if log.get("type") == "discovery_hint"),
                None,
            )
            if hint:
                posts_meta = [{
                    "likes":    hint.get("likes_hint", 0),
                    "comments": hint.get("comments_hint", 0),
                    "views":    hint.get("views_hint", 0),
                }]
                logger.debug("run_scoring: %s pakai bootstrap hint (belum ada post di DB)", account.username)

        score = calc_score(posts_meta, account.followers)

        account.engagement_rate = score.engagement_rate
        account.virality_score  = score.virality_score
        account.trending_score  = score.trending_score
        scored.append(account)

    # Rank berdasarkan trending_score
    scored.sort(key=lambda a: a.trending_score, reverse=True)
    for i, account in enumerate(scored):
        account.rank = i + 1

    await db.commit()
    return scored[:MAX_TRENDING_ACCOUNTS]


# ─────────────────────────────────────────────────────────────────────────────
# 3. AUTO-SCRAPE
# ─────────────────────────────────────────────────────────────────────────────

async def run_scrape_account(
    db: AsyncSession,
    account: InstagramTrendingAccount,
) -> dict:
    """
    Scrape POSTS_PER_ACCOUNT post + COMMENTS_PER_POST komentar untuk satu akun.
    Skip jika sudah di-scrape hari ini.
    """
    today = date.today()
    if account.last_scraped_date == today:
        return {"username": account.username, "skipped": True, "reason": "sudah di-scrape hari ini"}

    from app.services.instagram.pipeline_service import scrape_instagram_posts
    try:
        result = await scrape_instagram_posts(
            db=db,
            username=account.username,
            max_posts=POSTS_PER_ACCOUNT,
            max_comments=COMMENTS_PER_POST,
            keyword_id=None,
        )
        posts_new = result.get("posts_saved", 0)
        errors    = result.get("errors", [])

        account.posts_collected  += posts_new
        account.last_scraped_date = today
        account.scrape_logs = (account.scrape_logs or []) + [{
            "date":       today.isoformat(),
            "posts_new":  posts_new,
            "errors":     errors[:3],
        }]
        await db.commit()

        return {"username": account.username, "posts_new": posts_new, "errors": errors}

    except Exception as exc:
        logger.error("run_scrape_account %s error: %s", account.username, exc)
        return {"username": account.username, "error": str(exc)}


async def run_daily_trending(
    db: AsyncSession,
    provider_name: str = "ensembledata",
    hashtags: list[str] | None = None,
) -> dict:
    """
    Entry point harian:
      discover → score → scrape top 5
    """
    # 1. Discover
    await run_discovery(db, provider_name=provider_name, hashtags=hashtags)

    # 2. Score & rank
    top_accounts = await run_scoring(db)

    # 3. Scrape
    scrape_results = []
    for account in top_accounts:
        res = await run_scrape_account(db, account)
        scrape_results.append(res)

    return {
        "provider":       provider_name,
        "top_accounts":   [a.username for a in top_accounts],
        "scrape_results": scrape_results,
    }
