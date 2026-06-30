"""
YouTube connector — wraps EnsembleData YouTube endpoints.

Catatan parameter berdasarkan docs EnsembleData resmi:
  - keyword search  : keyword, depth (int, jumlah halaman per call)
  - video comments  : id (video_id), cursor (str, "" untuk halaman pertama)
  - hashtag search  : name (nama hashtag), depth, only_shorts
  - channel info    : browseId (channel ID)
  - video details   : id (video_id)
"""
from typing import Any

from app.integrations.ensemble_data.client import EnsembleDataClient
from app.integrations.ensemble_data.endpoints import YouTubeEndpoints

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

        Args:
            keyword: Kata kunci pencarian
            depth:   Jumlah halaman yang diambil dalam satu call (1 = ~20 video)
        """
        return await self.client.get(
            YouTubeEndpoints.KEYWORD_SEARCH.path,
            params={"keyword": keyword, "depth": min(depth, MAX_DEPTH)},
        )

    async def search_by_hashtag(
        self,
        hashtag: str,
        depth: int = 1,
        only_shorts: bool = False,
    ) -> dict[str, Any]:
        """
        Cari video berdasarkan hashtag.

        Args:
            hashtag:     Nama hashtag (tanpa #)
            depth:       Jumlah halaman
            only_shorts: Hanya ambil Shorts
        """
        return await self.client.get(
            YouTubeEndpoints.HASHTAG_SEARCH.path,
            params={"name": hashtag, "depth": min(depth, MAX_DEPTH), "only_shorts": only_shorts},
        )

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

        Args:
            video_id: YouTube video ID (contoh: 'cKkb5tperxc')
            cursor:   Token halaman berikutnya. "" untuk halaman pertama.
        """
        return await self.client.get(
            YouTubeEndpoints.VIDEO_COMMENTS.path,
            params={"id": video_id, "cursor": cursor},
        )

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
    ) -> dict[str, Any]:
        """
        Ambil video dari channel.

        Args:
            browse_id: Channel ID (UCxxxxxxxxxxxxxxx)
            cursor:    Token halaman berikutnya, "" untuk halaman pertama
        """
        return await self.client.get(
            YouTubeEndpoints.CHANNEL_VIDEOS.path,
            params={"browseId": browse_id, "cursor": cursor},
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
        Comments: field nextCursor di data.
        """
        data = raw.get("data") or {}
        cursor = data.get("nextCursor") or data.get("cursor") or data.get("next_cursor")
        return str(cursor) if cursor else None

    def extract_posts(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Ambil list video dari response /youtube/search.

        Struktur: data.posts[*].videoRenderer
        Kembalikan list videoRenderer langsung agar normalizer bisa parse.
        """
        data = raw.get("data") or {}
        posts = data.get("posts") or data.get("results") or data.get("videos") or data.get("items") or []
        extracted = []
        for item in posts:
            vr = item.get("videoRenderer")
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
        """
        data = raw.get("data") or {}
        raw_comments = data.get("comments") or []
        extracted = []
        for item in raw_comments:
            ctr = item.get("commentThreadRenderer") or {}
            comment = ctr.get("comment")
            if comment:
                extracted.append(comment)
        return extracted
