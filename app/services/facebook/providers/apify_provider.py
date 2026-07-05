from __future__ import annotations

from app.services.facebook.providers.base import BaseFacebookSearchProvider


class ApifyFacebookProvider(BaseFacebookSearchProvider):
    name = "apify"

    async def search_profile(
        self, identifier: str, max_posts: int, max_comments: int
    ) -> list[dict]:
        from app.integrations.apify.facebook import scrape_facebook_via_apify
        return await scrape_facebook_via_apify(identifier, max_posts, max_comments)
