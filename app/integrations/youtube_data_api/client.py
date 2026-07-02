"""
YouTube Data API v3 client — dipakai sebagai fallback saat EnsembleData quota habis (HTTP 495).

Endpoint: GET https://www.googleapis.com/youtube/v3/search
"""
from typing import Any

import httpx

from app.shared.exceptions import ExternalAPIError

_BASE_URL = "https://www.googleapis.com/youtube/v3"
_SOURCE_MARKER = "youtube_data_api"


async def _get(path: str, params: dict[str, Any]) -> httpx.Response:
    """GET dengan error YouTube Data API v3 dibungkus jadi ExternalAPIError (bukan crash mentah)."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{_BASE_URL}/{path}", params=params)
            resp.raise_for_status()
            return resp
    except httpx.HTTPStatusError as exc:
        raise ExternalAPIError(
            service="YouTubeDataAPI",
            message=f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
        )
    except httpx.RequestError as exc:
        raise ExternalAPIError(service="YouTubeDataAPI", message=str(exc))


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
        resp = await _get("search", params)
        items = resp.json().get("items") or []
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

        category_id yang tidak valid (bukan digit, misal placeholder Swagger "string")
        diabaikan saja daripada bikin request ke Google gagal 400.
        """
        params: dict[str, Any] = {
            "part": "snippet,contentDetails,statistics",
            "chart": "mostPopular",
            "regionCode": region_code,
            "maxResults": min(max_results, 50),
            "key": self.api_key,
        }
        if category_id and category_id.strip().isdigit():
            params["videoCategoryId"] = category_id.strip()

        resp = await _get("videos", params)
        return resp.json()

    async def search_videos_by_channel(
        self,
        channel_id: str,
        max_results: int = 10,
        order: str = "date",
    ) -> dict[str, Any]:
        """
        Ambil video terbaru dari channel spesifik via YouTube Data API v3.
        Dipakai sebagai fallback viral tracking ketika EnsembleData tidak tersedia.
        order: date | viewCount | relevance | rating
        """
        params = {
            "part": "snippet",
            "channelId": channel_id,
            "type": "video",
            "order": order,
            "maxResults": min(max_results, 50),
            "key": self.api_key,
        }
        resp = await _get("search", params)
        items = resp.json().get("items") or []
        return {
            "_source": _SOURCE_MARKER,
            "data": {"items": items},
        }

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

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{_BASE_URL}/commentThreads", params=params)
                if resp.status_code == 403:
                    # Komentar dimatikan untuk video ini — bukan error, kembalikan kosong
                    return {"_source": _SOURCE_MARKER, "data": {"items": [], "nextPageToken": None}}
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise ExternalAPIError(
                service="YouTubeDataAPI",
                message=f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except httpx.RequestError as exc:
            raise ExternalAPIError(service="YouTubeDataAPI", message=str(exc))

        return {
            "_source": _SOURCE_MARKER,
            "data": {
                "items": data.get("items") or [],
                "nextPageToken": data.get("nextPageToken"),
            },
        }
