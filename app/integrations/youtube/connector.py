"""
YouTube connector — wraps EnsembleData YouTube endpoints.

Catatan parameter berdasarkan docs EnsembleData resmi:
  - keyword search  : keyword, depth (int, jumlah halaman per call)
  - video comments  : id (video_id), cursor (str, "" untuk halaman pertama)
  - hashtag search  : name (nama hashtag), depth, only_shorts
  - channel info    : browseId (channel ID)
  - video details   : id (video_id)
"""
import logging
from typing import Any

from app.integrations.ensemble_data.client import EnsembleDataClient
from app.integrations.ensemble_data.endpoints import YouTubeEndpoints

logger = logging.getLogger(__name__)

PLATFORM = "youtube"
MAX_DEPTH = 5       # hard cap — 1 depth = ~20 video = 1 EnsembleData unit
MAX_PAGES = 5       # hard cap komentar per video


class YouTubeConnector:
    def __init__(self, client: EnsembleDataClient):
        self.client = client

    # ── Video / Keyword ───────────────────────────────────────────────────────

    async def search_by_keyword(
        self,
        keyword: str,
        depth: int = 1,
    ) -> dict[str, Any]:
        """
        Cari video YouTube berdasarkan keyword.
        Jika EnsembleData quota habis (HTTP 495), otomatis fallback ke YouTube Data API v3.
        """
        from app.shared.exceptions import ExternalAPIError

        logger.info("[YouTube] search_by_keyword: keyword=%r depth=%d", keyword, min(depth, MAX_DEPTH))
        try:
            result = await self.client.get(
                YouTubeEndpoints.KEYWORD_SEARCH.path,
                params={"keyword": keyword, "depth": min(depth, MAX_DEPTH)},
            )
            posts = (result.get("data") or {}).get("posts") or []
            logger.info("[YouTube] search_by_keyword: %d item diterima dari EnsembleData", len(posts))
            return result
        except ExternalAPIError as exc:
            if "495" not in str(exc):
                raise
            logger.warning("[YouTube] EnsembleData quota habis (495), fallback ke YouTube Data API v3")
            # Quota EnsembleData habis — coba YouTube Data API v3
            from app.shared.config import settings
            from app.integrations.youtube_data_api.client import YouTubeDataAPIClient

            if not settings.youtube_data_api_key:
                raise  # Tidak ada fallback key, teruskan error asli

            yt_client = YouTubeDataAPIClient(api_key=settings.youtube_data_api_key)
            return await yt_client.search_videos(keyword, max_results=50)

    async def search_by_hashtag(
        self,
        hashtag: str,
        depth: int = 1,
        only_shorts: bool = False,
    ) -> dict[str, Any]:
        """
        Cari video berdasarkan hashtag.
        Jika EnsembleData quota habis (HTTP 495), fallback ke keyword search via YouTube Data API v3.

        Args:
            hashtag:     Nama hashtag (tanpa #)
            depth:       Jumlah halaman
            only_shorts: Hanya ambil Shorts
        """
        from app.shared.exceptions import ExternalAPIError

        try:
            return await self.client.get(
                YouTubeEndpoints.HASHTAG_SEARCH.path,
                params={"name": hashtag, "depth": min(depth, MAX_DEPTH), "only_shorts": only_shorts},
            )
        except ExternalAPIError as exc:
            if "495" not in str(exc):
                raise
            logger.warning("[YouTube] EnsembleData quota habis (495) untuk hashtag=%r, fallback ke YouTube Data API v3", hashtag)
            from app.shared.config import settings
            from app.integrations.youtube_data_api.client import YouTubeDataAPIClient

            if not settings.youtube_data_api_key:
                raise
            yt_client = YouTubeDataAPIClient(api_key=settings.youtube_data_api_key)
            return await yt_client.search_videos(f"#{hashtag}", max_results=50)

    async def get_featured_categories(self, keyword: str) -> dict[str, Any]:
        """Ambil kategori unggulan berdasarkan keyword."""
        return await self.client.get(
            YouTubeEndpoints.FEATURED_CATEGORIES.path,
            params={"name": keyword},
        )

    # ── Video detail & comments ───────────────────────────────────────────────

    async def get_video_details(self, video_id: str) -> dict[str, Any]:
        """Ambil detail satu video. Param: id (bukan video_id)."""
        return await self.client.get(
            YouTubeEndpoints.VIDEO_DETAILS.path,
            params={"id": video_id},
        )

    async def get_video_comments(
        self,
        video_id: str,
        cursor: str = "",
    ) -> dict[str, Any]:
        """
        Ambil komentar video.
        Jika EnsembleData quota habis (HTTP 495), otomatis fallback ke YouTube Data API v3
        (commentThreads.list) — cursor dipakai ulang sebagai pageToken.

        Args:
            video_id: YouTube video ID (contoh: 'cKkb5tperxc')
            cursor:   Token halaman berikutnya. "" untuk halaman pertama.
        """
        from app.shared.exceptions import ExternalAPIError

        page_label = "halaman pertama" if not cursor else f"cursor={cursor[:20]}…"
        logger.info("[YouTube] get_video_comments: video_id=%s (%s)", video_id, page_label)
        try:
            result = await self.client.get(
                YouTubeEndpoints.VIDEO_COMMENTS.path,
                params={"id": video_id, "cursor": cursor},
            )
            comments = (result.get("data") or {}).get("comments") or []
            next_cur  = (result.get("data") or {}).get("nextCursor") or ""
            logger.info(
                "[YouTube] get_video_comments: %d komentar, next_cursor=%s",
                len(comments),
                repr(next_cur[:30]) if next_cur else "None (halaman terakhir)",
            )
            return result
        except ExternalAPIError as exc:
            if "495" not in str(exc):
                raise
            logger.warning("[YouTube] EnsembleData quota habis (495), fallback ke YouTube Data API v3 untuk komentar")
            from app.shared.config import settings
            from app.integrations.youtube_data_api.client import YouTubeDataAPIClient

            if not settings.youtube_data_api_key:
                raise  # Tidak ada fallback key, teruskan error asli

            yt_client = YouTubeDataAPIClient(api_key=settings.youtube_data_api_key)
            return await yt_client.list_comment_threads(video_id, page_token=cursor or None)

    # ── Channel ───────────────────────────────────────────────────────────────

    async def get_channel_detailed_info(
        self,
        browse_id: str,
        from_url: bool = False,
        get_additional_info: bool = False,
    ) -> dict[str, Any]:
        """
        Ambil detail lengkap channel.

        Args:
            browse_id:            Channel ID (format: UCxxxxxxxxxxxxxxx)
            from_url:             Jika True, browse_id dianggap URL channel
            get_additional_info:  Ambil info tambahan (lebih lambat)
        """
        return await self.client.get(
            YouTubeEndpoints.CHANNEL_INFO_DETAILED.path,
            params={
                "browseId": browse_id,
                "from_url": from_url,
                "get_additional_info": get_additional_info,
            },
        )

    async def get_channel_followers(self, browse_id: str) -> dict[str, Any]:
        """Ambil jumlah subscriber channel. Param: browseId."""
        return await self.client.get(
            YouTubeEndpoints.CHANNEL_FOLLOWERS.path,
            params={"browseId": browse_id},
        )

    async def get_channel_videos(
        self,
        browse_id: str,
        cursor: str = "",
        depth: int = 1,
    ) -> dict[str, Any]:
        """
        Ambil video dari channel.

        Args:
            browse_id: Channel ID (UCxxxxxxxxxxxxxxx)
            cursor:    Token halaman berikutnya, "" untuk halaman pertama
            depth:     Jumlah halaman yang diambil (1 = ~20 video, min 1)
        """
        return await self.client.get(
            YouTubeEndpoints.CHANNEL_VIDEOS.path,
            params={"browseId": browse_id, "cursor": cursor, "depth": min(depth, MAX_DEPTH)},
        )

    async def get_channel_id_to_name(self, browse_id: str) -> dict[str, Any]:
        """Konversi channel browseId ke username. Param: browseId."""
        return await self.client.get(
            YouTubeEndpoints.CHANNEL_ID_TO_NAME.path,
            params={"browseId": browse_id},
        )

    async def get_channel_username_to_id(self, username: str) -> dict[str, Any]:
        """Konversi username ke channel browseId."""
        return await self.client.get(
            YouTubeEndpoints.CHANNEL_USERNAME_TO_ID.path,
            params={"username": username},
        )

    # ── Cursor helpers ────────────────────────────────────────────────────────

    def extract_cursor(self, raw: dict[str, Any]) -> str | None:
        """
        Ambil cursor untuk halaman berikutnya.

        Keyword search: depth menggantikan cursor → kembalikan None.
        Comments: field nextCursor di data (EnsembleData) atau nextPageToken (YouTube Data API v3).
        """
        data = raw.get("data") or {}
        if raw.get("_source") == "youtube_data_api":
            token = data.get("nextPageToken")
            return str(token) if token else None
        cursor = data.get("nextCursor") or data.get("cursor") or data.get("next_cursor")
        return str(cursor) if cursor else None

    def extract_posts(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Ambil list video dari response pencarian.
        Mendukung dua sumber: EnsembleData (videoRenderer) dan YouTube Data API v3 (snippet).
        """
        # YouTube Data API v3 fallback
        if raw.get("_source") == "youtube_data_api":
            extracted = []
            for item in raw.get("data", {}).get("items", []):
                video_id = (item.get("id") or {}).get("videoId") if isinstance(item.get("id"), dict) else None
                if video_id:
                    extracted.append({"videoId": video_id, "_yt_api": True, "snippet": item.get("snippet") or {}})
            return extracted

        # EnsembleData format
        data = raw.get("data") or {}
        posts = data.get("posts") or data.get("results") or data.get("videos") or data.get("items") or []
        extracted = []
        for item in posts:
            vr = item.get("videoRenderer")
            if not vr:
                # channel/videos returns richItemRenderer → content → videoRenderer
                rich = item.get("richItemRenderer") or {}
                vr = (rich.get("content") or {}).get("videoRenderer")
            if vr:
                extracted.append(vr)
            elif item.get("videoId"):
                extracted.append(item)
        return extracted

    def extract_comments(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Ambil list komentar dari response /youtube/video/comments.

        Struktur: data.comments[*].commentThreadRenderer.comment
        Kembalikan comment inner object agar helper di pipeline bisa parse.

        Jika sumbernya YouTube Data API v3 (commentThreads.list), normalisasi ke
        bentuk yang sama (properties/author/toolbar) agar helper parsing di
        pipeline_service.py tidak perlu tahu sumbernya.
        """
        data = raw.get("data") or {}

        if raw.get("_source") == "youtube_data_api":
            extracted = []
            for item in data.get("items") or []:
                top = (item.get("snippet") or {}).get("topLevelComment") or {}
                snippet = top.get("snippet") or {}
                comment_id = top.get("id") or item.get("id") or ""
                if not comment_id:
                    continue
                extracted.append({
                    "commentId": comment_id,
                    "properties": {
                        "commentId": comment_id,
                        "content": {"content": snippet.get("textDisplay") or snippet.get("textOriginal") or ""},
                        "publishedTime": snippet.get("publishedAt") or "",
                    },
                    "author": {
                        "displayName": snippet.get("authorDisplayName"),
                        "channelId": (snippet.get("authorChannelId") or {}).get("value"),
                    },
                    "toolbar": {
                        "likeCountNotliked": str(snippet.get("likeCount", 0)),
                        "replyCount": str((item.get("snippet") or {}).get("totalReplyCount", 0)),
                    },
                })
            return extracted

        raw_comments = data.get("comments") or []
        extracted = []
        for item in raw_comments:
            ctr = item.get("commentThreadRenderer") or {}
            comment = ctr.get("comment")
            if comment:
                extracted.append(comment)
        return extracted
