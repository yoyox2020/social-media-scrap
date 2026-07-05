"""Provider Apify — wrapper tipis di atas scrape_instagram_via_apify() yang sudah ada."""
from __future__ import annotations

from typing import Any

from app.services.instagram.providers.base import BaseInstagramSearchProvider


class ApifyInstagramProvider(BaseInstagramSearchProvider):
    name = "apify"

    async def search_profile(
        self, username: str, max_posts: int, max_comments: int
    ) -> list[dict[str, Any]]:
        from app.integrations.apify.instagram import scrape_instagram_via_apify

        return await scrape_instagram_via_apify(
            username, latest_posts=max_posts, latest_comments=max_comments
        )
