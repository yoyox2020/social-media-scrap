"""
Provider Apify (`apify/instagram-post-scraper`) -- 2026-07-20, PENGGANTI UTAMA
utk fix gap thumbnail Instagram (lihat instagram_post_scraper.py utk detail
kenapa). Normalisasi output actor (1 baris = 1 POST, komentar nested) ke
bentuk kanonik BaseInstagramSearchProvider (1 baris = 1 pasangan
post+comment, SAMA persis dgn actor lama) supaya pipeline_service.py TIDAK
perlu tahu/berubah sama sekali soal provider mana yg dipakai.

Post TANPA komentar tetap dapat SATU baris (meta-only, tanpa commentText)
supaya post itu sendiri tidak hilang dari hasil (lihat cara
search_profile_with_fallback() -> pipeline_service.py mengelompokkan baris
per postUrl -- baris PERTAMA per post yg jadi sumber field meta).
"""
from __future__ import annotations

from typing import Any

from app.services.instagram.providers.base import BaseInstagramSearchProvider


class ApifyPostScraperInstagramProvider(BaseInstagramSearchProvider):
    name = "apify_post_scraper"

    async def search_profile(
        self, username: str, max_posts: int, max_comments: int
    ) -> list[dict[str, Any]]:
        from app.integrations.apify.instagram_post_scraper import scrape_instagram_posts_via_apify

        items = await scrape_instagram_posts_via_apify(username, results_limit=max_posts)

        rows: list[dict[str, Any]] = []
        for item in items:
            shortcode = item.get("shortCode") or ""
            post_url = item.get("url") or (f"https://www.instagram.com/p/{shortcode}/" if shortcode else "")
            if not post_url:
                continue

            meta = {
                "postUrl": post_url,
                "postDescription": item.get("caption") or "",
                "postTimestamp": item.get("timestamp"),
                "postLikesCount": item.get("likesCount", 0),
                "postCommentsCount": item.get("commentsCount", 0),
                "photoUrl": item.get("displayUrl"),
            }

            comments = (item.get("latestComments") or [])[:max_comments]
            if not comments:
                rows.append(dict(meta))
                continue

            for cmt in comments:
                rows.append({
                    **meta,
                    "commentText": cmt.get("text", ""),
                    "commentAuthor": cmt.get("ownerUsername", ""),
                    "commentTimestamp": cmt.get("timestamp"),
                    "commentLikesCount": cmt.get("likesCount", 0),
                })

        return rows
