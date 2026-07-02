"""
Instagram connector — wraps EnsembleData Instagram endpoints.

Semua param sesuai EnsembleData docs:
  - user/info          : username
  - user/basic-info    : user_id (int)
  - user/posts         : user_id (int), depth (int, ~12 post/depth)
  - user/reels         : user_id (int), depth (int)
  - post/details       : code (shortcode), n_comments_to_fetch (int)
  - post/comments      : media_id (str), cursor (str), sorting (popular|recent)
  - search             : text (str)
"""
import logging
from typing import Any

from app.integrations.ensemble_data.client import EnsembleDataClient
from app.integrations.ensemble_data.endpoints import InstagramEndpoints

logger = logging.getLogger(__name__)

PLATFORM = "instagram"


class InstagramConnector:
    def __init__(self, client: EnsembleDataClient):
        self.client = client

    # ── User ─────────────────────────────────────────────────────────────────

    async def get_user_info(self, username: str) -> dict[str, Any]:
        """Profil user (username → user_id/pk, bio, followers, dll)."""
        logger.info("[Instagram] get_user_info: username=%s", username)
        return await self.client.get(
            InstagramEndpoints.USER_INFO.path,
            params={"username": username},
        )

    async def get_user_basic_info(self, user_id: int | str) -> dict[str, Any]:
        """Statistik dasar: followers, following, posts count."""
        return await self.client.get(
            InstagramEndpoints.USER_BASIC_INFO.path,
            params={"user_id": user_id},
        )

    async def get_user_posts(self, user_id: int | str, depth: int = 1) -> dict[str, Any]:
        """Post dari user. depth=1 → ~12 post."""
        logger.info("[Instagram] get_user_posts: user_id=%s depth=%d", user_id, depth)
        return await self.client.get(
            InstagramEndpoints.USER_POSTS.path,
            params={"user_id": user_id, "depth": depth},
        )

    async def get_user_reels(self, user_id: int | str, depth: int = 1) -> dict[str, Any]:
        """Reels dari user."""
        return await self.client.get(
            InstagramEndpoints.USER_REELS.path,
            params={"user_id": user_id, "depth": depth},
        )

    # ── Post ─────────────────────────────────────────────────────────────────

    async def get_post_details(self, code: str, n_comments: int = 0) -> dict[str, Any]:
        """
        Detail post via shortcode (code).
        n_comments_to_fetch=0 → hanya post info, komentar diambil terpisah via get_post_comments.
        """
        logger.info("[Instagram] get_post_details: code=%s n_comments=%d", code, n_comments)
        return await self.client.get(
            InstagramEndpoints.POST_DETAILS.path,
            params={"code": code, "n_comments_to_fetch": n_comments},
        )

    async def get_post_comments(
        self,
        media_id: str,
        cursor: str = "",
        sorting: str = "popular",
    ) -> dict[str, Any]:
        """Komentar post via media_id. sorting: popular | recent."""
        logger.info("[Instagram] get_post_comments: media_id=%s", media_id)
        return await self.client.get(
            InstagramEndpoints.POST_COMMENTS.path,
            params={"media_id": media_id, "cursor": cursor, "sorting": sorting},
        )

    # ── Search ───────────────────────────────────────────────────────────────

    async def search(self, text: str) -> dict[str, Any]:
        """Search Instagram (user/hashtag/keyword)."""
        return await self.client.get(
            InstagramEndpoints.SEARCH.path,
            params={"text": text},
        )

    # ── Extractors ───────────────────────────────────────────────────────────

    def extract_user_info(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Ambil user info dari response get_user_info."""
        return raw.get("data") or {}

    def extract_posts(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        """Ambil list post dari response get_user_posts."""
        data = raw.get("data") or {}
        return (
            data.get("collector")
            or data.get("posts")
            or data.get("items")
            or []
        )

    def extract_comments(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        """Ambil list komentar dari response get_post_comments."""
        data = raw.get("data") or {}
        return (
            data.get("collector")
            or data.get("comments")
            or data.get("items")
            or []
        )

    def extract_post_id(self, post: dict[str, Any]) -> str:
        """Ambil media_id (pk) dari satu post item."""
        return str(post.get("pk") or post.get("id") or "").split("_")[0]

    def extract_shortcode(self, post: dict[str, Any]) -> str:
        """Ambil shortcode dari satu post item."""
        return post.get("shortcode") or post.get("code") or ""
