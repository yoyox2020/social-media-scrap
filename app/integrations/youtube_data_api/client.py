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

        # Wrap dalam format yang dikenal connector — tandai _source agar extract_posts tahu
        items = data.get("items") or []
        return {
            "_source": _SOURCE_MARKER,
            "data": {"items": items},
        }
