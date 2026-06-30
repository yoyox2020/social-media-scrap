"""
YouTube Data API v3 client — dipakai sebagai fallback saat EnsembleData quota habis (HTTP 495).

Endpoint: GET https://www.googleapis.com/youtube/v3/search
"""
from typing import Any

import httpx

_BASE_URL = "https://www.googleapis.com/youtube/v3"
_SOURCE_MARKER = "youtube_data_api"


class YouTubeDataAPIClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    async def search_videos(self, keyword: str, max_results: int = 50) -> dict[str, Any]:
        params = {
            "part": "snippet",
            "q": keyword,
            "type": "video",
            "key": self.api_key,
            "maxResults": min(max_results, 50),
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{_BASE_URL}/search", params=params)
            resp.raise_for_status()
            data = resp.json()

        items = data.get("items") or []
        return {
            "_source": _SOURCE_MARKER,
            "data": {"items": items},
        }

    async def fetch_popular(
        self,
        region_code: str = "ID",
        max_results: int = 20,
        category_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Ambil video paling populer (mostPopular chart) dari YouTube Data API v3.
        GET https://www.googleapis.com/youtube/v3/videos?chart=mostPopular&regionCode=ID
        """
        params: dict[str, Any] = {
            "part": "snippet,contentDetails,statistics",
            "chart": "mostPopular",
            "regionCode": region_code,
            "maxResults": min(max_results, 50),
            "key": self.api_key,
        }
        if category_id:
            params["videoCategoryId"] = category_id

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{_BASE_URL}/videos", params=params)
            resp.raise_for_status()
            return resp.json()
