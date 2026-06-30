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

    async def search_videos(
        self,
        keyword: str,
        max_results: int = 50,
        order: str = "relevance",
    ) -> dict[str, Any]:
        """
        Cari video YouTube berdasarkan keyword.
        order: relevance | viewCount | date | rating | title
        """
        params = {
            "part": "snippet",
            "q": keyword,
            "type": "video",
            "order": order,
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

    async def list_comment_threads(
        self,
        video_id: str,
        max_results: int = 50,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """
        Ambil komentar top-level video. Fallback saat EnsembleData quota habis (HTTP 495).
        GET https://www.googleapis.com/youtube/v3/commentThreads
        """
        params: dict[str, Any] = {
            "part": "snippet",
            "videoId": video_id,
            "maxResults": min(max_results, 100),
            "order": "relevance",
            "textFormat": "plainText",
            "key": self.api_key,
        }
        if page_token:
            params["pageToken"] = page_token

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{_BASE_URL}/commentThreads", params=params)
            if resp.status_code == 403:
                # Komentar dimatikan untuk video ini — bukan error, kembalikan kosong
                return {"_source": _SOURCE_MARKER, "data": {"items": [], "nextPageToken": None}}
            resp.raise_for_status()
            data = resp.json()

        return {
            "_source": _SOURCE_MARKER,
            "data": {
                "items": data.get("items") or [],
                "nextPageToken": data.get("nextPageToken"),
            },
        }
