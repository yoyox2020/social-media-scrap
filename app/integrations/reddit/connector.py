"""
Reddit connector — wraps EnsembleData Reddit endpoints.
"""
from typing import Any

from app.integrations.ensemble_data.client import EnsembleDataClient
from app.integrations.ensemble_data.endpoints import RedditEndpoints

PLATFORM = "reddit"


class RedditConnector:
    def __init__(self, client: EnsembleDataClient):
        self.client = client

    async def search_by_keyword(self, keyword: str, after: str | None = None) -> dict[str, Any]:
        """Cari post Reddit berdasarkan keyword."""
        params: dict[str, Any] = {"keyword": keyword}
        if after:
            params["after"] = after
        return await self.client.get(RedditEndpoints.KEYWORD_SEARCH.path, params=params)

    async def get_subreddit_posts(self, subreddit: str, after: str | None = None) -> dict[str, Any]:
        """Ambil post dari subreddit tertentu."""
        params: dict[str, Any] = {"subreddit": subreddit}
        if after:
            params["after"] = after
        return await self.client.get(RedditEndpoints.SUBREDDIT_POSTS.path, params=params)

    async def get_post_comments(self, post_id: str) -> dict[str, Any]:
        """Ambil komentar dari sebuah post Reddit."""
        return await self.client.get(RedditEndpoints.POST_COMMENTS.path, params={"post_id": post_id})

    def extract_cursor(self, raw: dict[str, Any]) -> str | None:
        """Ambil cursor 'after' untuk halaman berikutnya."""
        data = raw.get("data", {})
        return data.get("after") or data.get("next_cursor")

    def extract_posts(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        """Ambil list post dari response API."""
        data = raw.get("data", {})
        return data.get("posts", []) or data.get("children", []) or data.get("items", [])
