"""
Twitter/X Trend-Recommendation Scrape Service — Fase 1.

Batch harian trend_recommendations (Subsistem B, mirroring
app/services/facebook/trend_scrape_service.py) BELUM dibangun — menyusul
Fase 2. Yang ada baru discover_twitter_topic_by_keyword(), dibutuhkan SEJAK
AWAL oleh tingkat-3 GET /twitter/posts/search (search langsung ke Twitter
kalau keyword genuinely baru, tidak ada di DB maupun trend_recommendations).
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

DISCOVER_DEFAULT_SCORE = 0.9  # sama seperti Facebook/TikTok, lihat komentar di sana


async def discover_twitter_topic_by_keyword(db: AsyncSession, keyword: str, max_results: int = 10) -> dict:
    """
    Search Twitter/X LANGSUNG by keyword (Apify `danek/twitter-scraper`, mode
    search) — TIDAK ada AI menebak akun. Akun diambil langsung dari field
    top-level `screen_name` (data terstruktur), sesederhana TikTok (beda
    dengan Facebook yang harus extract identifier dari URL post).

    CATATAN SKEMA (diverifikasi live 08 Juli 2026): mode search punya bentuk
    BEDA dari mode profil (app/integrations/apify/twitter.py) — identifier
    ada di `screen_name` TOP-LEVEL, BUKAN nested `author.screen_name` (field
    `author` tidak ada sama sekali di hasil search, diganti `user_info`).

    Hasil disubmit ke trend_recommendations (source='manual_twitter_search')
    lewat submit_recommendations() yang SUDAH ADA — topiknya ikut antrian
    budget harian kalau nanti Fase 2 (batch harian) sudah dibangun. Dipakai
    oleh tingkat-3 search_twitter_posts(), BUKAN endpoint manual terpisah
    (POST /twitter/discover menyusul Fase 2).
    """
    from app.integrations.apify.twitter import search_twitter_by_keyword
    from app.domain.trend_recommendations.schemas import TrendRecommendationBatchCreate, TrendRecommendationItem
    from app.services.trend_recommendations.service import submit_recommendations

    try:
        raw_posts = await search_twitter_by_keyword(keyword, max_results=max_results)
    except Exception as exc:
        logger.error("discover_twitter_topic_by_keyword: search gagal untuk keyword=%r: %s", keyword, exc)
        return {"keyword": keyword, "posts_found": 0, "accounts_found": [], "submitted": None, "error": str(exc)}

    seen: set[str] = set()
    accounts: list[dict] = []
    sample_posts: list[dict] = []
    for post in raw_posts:
        identifier = post.get("screen_name")
        if identifier and identifier not in seen:
            seen.add(identifier)
            accounts.append({"platform": "twitter", "username": identifier})
        tweet_id = post.get("tweet_id")
        user_info = post.get("user_info") or {}
        sample_posts.append({
            "text": (post.get("text") or "")[:200],
            "author": user_info.get("name") or identifier,
            "url": f"https://x.com/{identifier}/status/{tweet_id}" if identifier and tweet_id else "",
            "identifier_extracted": identifier,
        })

    if not accounts:
        return {
            "keyword": keyword, "posts_found": len(raw_posts), "accounts_found": [],
            "submitted": None, "sample_posts": sample_posts,
            "message": "Tweet ditemukan tapi tidak ada author.screen_name — cek sample_posts.",
        }

    body = TrendRecommendationBatchCreate(
        items=[TrendRecommendationItem(topic=keyword, score=DISCOVER_DEFAULT_SCORE, related_accounts=accounts)],
        source="manual_twitter_search",
    )
    result = await submit_recommendations(db, body)

    logger.info(
        "discover_twitter_topic_by_keyword: keyword=%r posts=%d akun=%d submitted=%s",
        keyword, len(raw_posts), len(accounts), result,
    )
    return {
        "keyword": keyword, "posts_found": len(raw_posts), "accounts_found": accounts,
        "submitted": result, "sample_posts": sample_posts[:5],
    }
