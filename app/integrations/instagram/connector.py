"""
Instagram connector — wraps EnsembleData Instagram endpoints.
"""
from typing import Any

from app.integrations.ensemble_data.client import EnsembleDataClient
from app.integrations.ensemble_data.endpoints import InstagramEndpoints

PLATFORM = "instagram"


class InstagramConnector:
    def __init__(self, client: EnsembleDataClient):
        self.client = client

    async def search(self, keyword: str) -> dict[str, Any]:
        """Cari konten Instagram berdasarkan keyword/hashtag."""
        return await self.client.get(InstagramEndpoints.SEARCH.path, params={"keyword": keyword})

    async def get_user_posts(self, username: str, cursor: str | None = None) -> dict[str, Any]:
        """Ambil post dari username Instagram."""
        params: dict[str, Any] = {"username": username}
        if cursor:
            params["cursor"] = cursor
        return await self.client.get(InstagramEndpoints.USER_POSTS.path, params=params)

    async def get_post_comments(self, post_id: str, cursor: str | None = None) -> dict[str, Any]:
        """Ambil komentar dari post."""
        params: dict[str, Any] = {"post_id": post_id}
        if cursor:
            params["cursor"] = cursor
        return await self.client.get(InstagramEndpoints.POST_COMMENTS.path, params=params)

    async def get_post_with_comments(self, post_id: str) -> dict[str, Any]:
        """Ambil post beserta komentarnya sekaligus."""
        return await self.client.get(InstagramEndpoints.POST_INFO_COMMENTS.path, params={"post_id": post_id})

    async def get_user_reels(self, username: str, cursor: str | None = None) -> dict[str, Any]:
        """Ambil reels dari username."""
        params: dict[str, Any] = {"username": username}
        if cursor:
            params["cursor"] = cursor
        return await self.client.get(InstagramEndpoints.USER_REELS.path, params=params)

    def extract_cursor(self, raw: dict[str, Any]) -> str | None:
        """Ambil cursor halaman berikutnya."""
        data = raw.get("data", {})
        return data.get("next_cursor") or data.get("cursor") or data.get("end_cursor")

    def extract_posts(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        """Ambil list post dari response API."""
        data = raw.get("data", {})
        return data.get("collector", []) or data.get("posts", []) or data.get("items", [])
