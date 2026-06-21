"""
TikTok connector — wraps EnsembleData TikTok endpoints.

Setiap method mengembalikan raw dict dari API.
Normalisasi ke model Post dilakukan di normalizer.py.
"""
from typing import Any

from app.integrations.ensemble_data.client import EnsembleDataClient
from app.integrations.ensemble_data.endpoints import TikTokEndpoints

PLATFORM = "tiktok"


class TikTokConnector:
    def __init__(self, client: EnsembleDataClient):
        self.client = client

    async def search_by_keyword(self, keyword: str, cursor: int = 0) -> dict[str, Any]:
        """Cari post TikTok berdasarkan keyword. cursor=0 untuk halaman pertama."""
        return await self.client.get(
            TikTokEndpoints.KEYWORD_POSTS.path,
            params={"keyword": keyword, "cursor": cursor},
        )

    async def search_by_keyword_full(self, keyword: str, cursor: int = 0) -> dict[str, Any]:
        """Full keyword search — lebih banyak metadata."""
        return await self.client.get(
            TikTokEndpoints.KEYWORD_POSTS_FULL.path,
            params={"keyword": keyword, "cursor": cursor},
        )

    async def search_by_hashtag(self, hashtag: str, cursor: int = 0) -> dict[str, Any]:
        """Cari post berdasarkan hashtag (tanpa #)."""
        return await self.client.get(
            TikTokEndpoints.HASHTAG_POSTS.path,
            params={"name": hashtag, "cursor": cursor},
        )

    async def get_post_comments(self, aweme_id: str, cursor: int = 0) -> dict[str, Any]:
        """Ambil komentar dari sebuah post."""
        return await self.client.get(
            TikTokEndpoints.POST_COMMENTS.path,
            params={"aweme_id": aweme_id, "cursor": cursor},
        )

    async def get_user_posts(self, username: str, cursor: int = 0) -> dict[str, Any]:
        """Ambil post dari username TikTok."""
        return await self.client.get(
            TikTokEndpoints.USER_POSTS.path,
            params={"username": username, "cursor": cursor},
        )

    async def get_post_info(self, aweme_id: str) -> dict[str, Any]:
        """Ambil detail satu post."""
        return await self.client.get(
            TikTokEndpoints.POST_INFO.path,
            params={"aweme_id": aweme_id},
        )

    def extract_cursor(self, raw: dict[str, Any]) -> int | None:
        """Ambil cursor untuk halaman berikutnya. None jika tidak ada lagi."""
        data = raw.get("data", {})
        has_more = data.get("has_more", False)
        if not has_more:
            return None
        return data.get("cursor")

    def extract_posts(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        """Ambil list post dari response API."""
        return raw.get("data", {}).get("aweme_list", [])
