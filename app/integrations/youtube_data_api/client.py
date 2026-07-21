"""
YouTube Data API v3 client — dipakai sebagai fallback saat EnsembleData quota habis (HTTP 495).

Endpoint: GET https://www.googleapis.com/youtube/v3/search
"""
from datetime import datetime
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

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

    async def search_recent(
        self,
        keyword: str,
        published_after: datetime,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """
        Cari video TERBARU berdasarkan keyword, dari `published_after` s/d
        SEKARANG (`publishedAfter` resmi search.list, TANPA `publishedBefore`
        -- sengaja TIDAK exclude video yang baru saja diupload, video umur
        1 jam pun HARUS tetap ketemu/muncul paling atas). Beda dari
        search_videos() biasa yg default order="relevance" (tidak
        mengutamakan kebaruan sama sekali).

        order="date" DESCENDING (bawaan Google utk order=date: terbaru
        duluan) -- video yg paling baru diupload SELALU di posisi teratas
        hasil, itulah yg bikin "langsung bisa diketahui" tanpa perlu scroll/
        filter manual. Lihat
        app/services/youtube/pipeline_service.py::search_recent_uploads().

        published_after HARUS timezone-aware (UTC) -- di-format ke RFC 3339
        (mis. "2026-07-16T10:00:00Z") sesuai spek resmi Google, format lain
        (naive datetime) akan ditolak API dgn HTTP 400.
        """
        params = {
            "part": "snippet",
            "q": keyword,
            "type": "video",
            "order": "date",
            "publishedAfter": published_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
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

    async def get_videos_statistics(self, video_ids: list[str]) -> dict[str, dict[str, int]]:
        """
        Ambil views/likes/comments utk banyak video sekaligus
        (`videos.list?part=statistics`, maks 50 ID per call -- batasan resmi
        YouTube Data API v3). Dipakai utk ENRICH hasil `search.list`/EnsembleData
        videoRenderer (yang secara STRUKTURAL tidak menyertakan statistics sama
        sekali -- beda endpoint dari `videos.list`), lihat
        app/services/processing/normalizer.py::YouTubeNormalizer yang
        sebelumnya hardcode likes/comments=0 karena ini.

        Return {video_id: {"views": int, "likes": int, "comments": int}} --
        video yang sudah dihapus/di-private/komentar dimatikan otomatis TIDAK
        muncul di dict hasil (bukan exception), pemanggil cukup `.get(id, {})`.

        Tiap chunk di-retry maks 3x (exponential backoff) -- ditemukan
        2026-07-16: ~15-20% panggilan gagal gara-gara rate-limit SESAAT (banyak
        Celery worker manggil bersamaan), BUKAN kuota harian habis (metric
        kuota beda dari search.list yang benar2 exhausted -- lihat memory
        project_youtube_quota_incident_2026_07). Tanpa retry, kegagalan sesaat
        ini bikin views/likes/comments post itu PERMANEN 0 (enrichment cuma
        dipanggil sekali saat post baru disimpan, tidak pernah dicoba ulang).
        """
        stats_by_id: dict[str, dict[str, int]] = {}
        for i in range(0, len(video_ids), 50):
            chunk = video_ids[i:i + 50]
            stats_by_id.update(await self._fetch_statistics_chunk(chunk))
        return stats_by_id

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    async def _fetch_statistics_chunk(self, chunk: list[str]) -> dict[str, dict[str, int]]:
        params = {
            "part": "statistics",
            "id": ",".join(chunk),
            "key": self.api_key,
        }
        resp = await _get("videos", params)
        result: dict[str, dict[str, int]] = {}
        for item in resp.json().get("items") or []:
            stats = item.get("statistics") or {}
            result[item["id"]] = {
                "views": int(stats.get("viewCount", 0) or 0),
                "likes": int(stats.get("likeCount", 0) or 0),
                "comments": int(stats.get("commentCount", 0) or 0),
            }
        return result

    async def get_videos_full_details(self, video_ids: list[str]) -> dict[str, dict[str, Any]]:
        """
        Ambil detail LENGKAP video (snippet+contentDetails+statistics+
        topicDetails) -- dipakai Metadata Agent
        (app/services/youtube_metadata/agent.py), BEDA dari
        get_videos_statistics() yg cuma part=statistics (dipakai enrichment
        views/likes/comments post biasa). Max 50 ID per chunk (batasan resmi
        API, sama spt get_videos_statistics()).

        Return {video_id: {raw video.list item dict}} -- parsing ke bentuk
        akhir dilakukan pemanggil (Metadata Agent), fungsi ini cuma agregasi
        chunk + retry.
        """
        result: dict[str, dict[str, Any]] = {}
        for i in range(0, len(video_ids), 50):
            chunk = video_ids[i:i + 50]
            result.update(await self._fetch_full_details_chunk(chunk))
        return result

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    async def _fetch_full_details_chunk(self, chunk: list[str]) -> dict[str, dict[str, Any]]:
        params = {
            "part": "snippet,contentDetails,statistics,topicDetails",
            "id": ",".join(chunk),
            "key": self.api_key,
        }
        resp = await _get("videos", params)
        return {item["id"]: item for item in resp.json().get("items") or []}

    async def get_channels_details(self, channel_ids: list[str]) -> dict[str, dict[str, Any]]:
        """
        Ambil detail channel (snippet+statistics) -- subscriber count,
        negara, tanggal channel dibuat. Dipakai Metadata Agent utk lengkapi
        info channel per video. Max 50 ID per chunk. Channel yg sudah
        dihapus/di-suspend TIDAK muncul di hasil (bukan exception).
        """
        result: dict[str, dict[str, Any]] = {}
        unique_ids = list(dict.fromkeys(channel_ids))  # dedup, banyak video bisa 1 channel sama
        for i in range(0, len(unique_ids), 50):
            chunk = unique_ids[i:i + 50]
            result.update(await self._fetch_channels_chunk(chunk))
        return result

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    async def _fetch_channels_chunk(self, chunk: list[str]) -> dict[str, dict[str, Any]]:
        params = {
            "part": "snippet,statistics",
            "id": ",".join(chunk),
            "key": self.api_key,
        }
        resp = await _get("channels", params)
        return {item["id"]: item for item in resp.json().get("items") or []}

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
