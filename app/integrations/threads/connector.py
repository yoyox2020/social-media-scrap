"""
Threads connector — wraps EnsembleData Threads endpoints.
"""
from typing import Any

from app.integrations.ensemble_data.client import EnsembleDataClient
from app.integrations.ensemble_data.endpoints import ThreadsEndpoints

PLATFORM = "threads"


class ThreadsConnector:
    def __init__(self, client: EnsembleDataClient):
        self.client = client

    async def search_by_keyword(self, keyword: str, cursor: str | None = None) -> dict[str, Any]:
        """Cari post Threads berdasarkan keyword."""
        params: dict[str, Any] = {"keyword": keyword}
        if cursor:
            params["cursor"] = cursor
        return await self.client.get(ThreadsEndpoints.KEYWORD_SEARCH.path, params=params)

    async def get_user_posts(self, username: str, cursor: str | None = None) -> dict[str, Any]:
        """Ambil post dari username Threads."""
        params: dict[str, Any] = {"username": username}
        if cursor:
            params["cursor"] = cursor
        return await self.client.get(ThreadsEndpoints.USER_POSTS.path, params=params)

    def extract_cursor(self, raw: dict[str, Any]) -> str | None:
        data = raw.get("data", {})
        return data.get("cursor") or data.get("next_cursor")

    def extract_posts(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        data = raw.get("data", {})
        return data.get("threads", []) or data.get("posts", []) or data.get("items", [])
