"""
YouTube connector — wraps EnsembleData YouTube endpoints.
"""
from typing import Any

from app.integrations.ensemble_data.client import EnsembleDataClient
from app.integrations.ensemble_data.endpoints import YouTubeEndpoints

PLATFORM = "youtube"


class YouTubeConnector:
    def __init__(self, client: EnsembleDataClient):
        self.client = client

    async def search_by_keyword(self, keyword: str, next_page_token: str | None = None) -> dict[str, Any]:
        """Cari video YouTube berdasarkan keyword."""
        params: dict[str, Any] = {"keyword": keyword}
        if next_page_token:
            params["next_page_token"] = next_page_token
        return await self.client.get(YouTubeEndpoints.KEYWORD_SEARCH.path, params=params)

    async def search_by_hashtag(self, hashtag: str) -> dict[str, Any]:
        """Cari video berdasarkan hashtag."""
        return await self.client.get(YouTubeEndpoints.HASHTAG_SEARCH.path, params={"hashtag": hashtag})

    async def get_video_comments(self, video_id: str, next_page_token: str | None = None) -> dict[str, Any]:
        """Ambil komentar video."""
        params: dict[str, Any] = {"video_id": video_id}
        if next_page_token:
            params["next_page_token"] = next_page_token
        return await self.client.get(YouTubeEndpoints.VIDEO_COMMENTS.path, params=params)

    async def get_video_details(self, video_id: str) -> dict[str, Any]:
        """Ambil detail satu video."""
        return await self.client.get(YouTubeEndpoints.VIDEO_DETAILS.path, params={"video_id": video_id})

    async def get_channel_videos(self, channel_id: str, next_page_token: str | None = None) -> dict[str, Any]:
        """Ambil video dari channel."""
        params: dict[str, Any] = {"channel_id": channel_id}
        if next_page_token:
            params["next_page_token"] = next_page_token
        return await self.client.get(YouTubeEndpoints.CHANNEL_VIDEOS.path, params=params)

    def extract_cursor(self, raw: dict[str, Any]) -> str | None:
        """Ambil next_page_token. None jika sudah halaman terakhir."""
        return raw.get("data", {}).get("next_page_token") or raw.get("data", {}).get("nextPageToken")

    def extract_posts(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        """Ambil list video dari response API."""
        data = raw.get("data", {})
        return data.get("videos", []) or data.get("items", [])
