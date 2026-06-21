"""
Normalizer: konversi raw response EnsembleData → model Post.

Setiap platform punya struktur JSON yang berbeda. Normalizer
mengekstrak field yang relevan dan menyimpan raw_data secara utuh.
"""
import uuid
from datetime import datetime, timezone
from typing import Any

from app.domain.posts.models import Post


def _utc_from_timestamp(ts: Any) -> datetime | None:
    """Konversi unix timestamp (int/float) ke datetime UTC."""
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def _utc_from_iso(s: Any) -> datetime | None:
    """Konversi ISO string ke datetime UTC."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ── TikTok ────────────────────────────────────────────────────────────────────

class TikTokNormalizer:
    PLATFORM = "tiktok"

    def normalize(self, items: list[dict], keyword_id: uuid.UUID) -> list[Post]:
        return [self._to_post(item, keyword_id) for item in items if item.get("aweme_id")]

    def _to_post(self, item: dict, keyword_id: uuid.UUID) -> Post:
        author = item.get("author") or {}
        stats = item.get("statistics") or {}
        aweme_id = str(item.get("aweme_id", ""))
        username = author.get("unique_id", "") or author.get("uniqueId", "")

        return Post(
            id=uuid.uuid4(),
            keyword_id=keyword_id,
            external_id=aweme_id,
            platform=self.PLATFORM,
            content=item.get("desc", ""),
            author=username,
            url=f"https://www.tiktok.com/@{username}/video/{aweme_id}" if aweme_id else None,
            metadata_={
                "likes": _safe_int(stats.get("digg_count")),
                "comments": _safe_int(stats.get("comment_count")),
                "shares": _safe_int(stats.get("share_count")),
                "views": _safe_int(stats.get("play_count")),
                "nickname": author.get("nickname", ""),
            },
            raw_data=item,
            published_at=_utc_from_timestamp(item.get("create_time")),
            collected_at=datetime.now(timezone.utc),
        )


# ── YouTube ───────────────────────────────────────────────────────────────────

class YouTubeNormalizer:
    PLATFORM = "youtube"

    def normalize(self, items: list[dict], keyword_id: uuid.UUID) -> list[Post]:
        return [self._to_post(item, keyword_id) for item in items if self._get_video_id(item)]

    def _get_video_id(self, item: dict) -> str:
        return item.get("video_id") or item.get("videoId") or item.get("id", {}).get("videoId", "")

    def _to_post(self, item: dict, keyword_id: uuid.UUID) -> Post:
        video_id = self._get_video_id(item)
        snippet = item.get("snippet") or {}
        statistics = item.get("statistics") or {}
        channel = item.get("channel_name") or snippet.get("channelTitle", "")

        return Post(
            id=uuid.uuid4(),
            keyword_id=keyword_id,
            external_id=video_id,
            platform=self.PLATFORM,
            content=item.get("title") or snippet.get("title", ""),
            author=channel,
            url=f"https://www.youtube.com/watch?v={video_id}" if video_id else None,
            metadata_={
                "views": _safe_int(item.get("view_count") or statistics.get("viewCount")),
                "likes": _safe_int(item.get("like_count") or statistics.get("likeCount")),
                "comments": _safe_int(item.get("comment_count") or statistics.get("commentCount")),
                "description": item.get("description") or snippet.get("description", ""),
                "channel_id": item.get("channel_id") or snippet.get("channelId", ""),
            },
            raw_data=item,
            published_at=_utc_from_iso(
                item.get("published_at") or item.get("publishedAt") or snippet.get("publishedAt")
            ),
            collected_at=datetime.now(timezone.utc),
        )


# ── Instagram ─────────────────────────────────────────────────────────────────

class InstagramNormalizer:
    PLATFORM = "instagram"

    def normalize(self, items: list[dict], keyword_id: uuid.UUID) -> list[Post]:
        return [self._to_post(item, keyword_id) for item in items if self._get_post_id(item)]

    def _get_post_id(self, item: dict) -> str:
        return str(item.get("id") or item.get("pk") or item.get("shortcode") or "")

    def _to_post(self, item: dict, keyword_id: uuid.UUID) -> Post:
        post_id = self._get_post_id(item)
        owner = item.get("owner") or item.get("user") or {}
        username = owner.get("username", "") or item.get("username", "")
        shortcode = item.get("shortcode", "") or post_id

        return Post(
            id=uuid.uuid4(),
            keyword_id=keyword_id,
            external_id=post_id,
            platform=self.PLATFORM,
            content=item.get("caption") or item.get("accessibility_caption", ""),
            author=username,
            url=item.get("permalink") or (f"https://www.instagram.com/p/{shortcode}/" if shortcode else None),
            metadata_={
                "likes": _safe_int(item.get("like_count")),
                "comments": _safe_int(item.get("comments_count") or item.get("comment_count")),
                "media_type": item.get("media_type", ""),
                "is_video": item.get("is_video", False),
            },
            raw_data=item,
            published_at=_utc_from_timestamp(item.get("taken_at") or item.get("timestamp")),
            collected_at=datetime.now(timezone.utc),
        )


# ── Reddit ────────────────────────────────────────────────────────────────────

class RedditNormalizer:
    PLATFORM = "reddit"

    def normalize(self, items: list[dict], keyword_id: uuid.UUID) -> list[Post]:
        return [self._to_post(item, keyword_id) for item in items if self._get_post_id(item)]

    def _get_post_id(self, item: dict) -> str:
        data = item.get("data") or item
        return str(data.get("id") or data.get("name") or "")

    def _to_post(self, item: dict, keyword_id: uuid.UUID) -> Post:
        data = item.get("data") or item
        post_id = self._get_post_id(item)

        return Post(
            id=uuid.uuid4(),
            keyword_id=keyword_id,
            external_id=post_id,
            platform=self.PLATFORM,
            content=data.get("title", "") + (" " + data.get("selftext", "") if data.get("selftext") else ""),
            author=data.get("author", ""),
            url=f"https://www.reddit.com{data.get('permalink', '')}" if data.get("permalink") else None,
            metadata_={
                "score": _safe_int(data.get("score")),
                "upvote_ratio": data.get("upvote_ratio", 0),
                "comments": _safe_int(data.get("num_comments")),
                "subreddit": data.get("subreddit", ""),
                "is_self": data.get("is_self", True),
            },
            raw_data=item,
            published_at=_utc_from_timestamp(data.get("created_utc")),
            collected_at=datetime.now(timezone.utc),
        )


# ── Threads ───────────────────────────────────────────────────────────────────

class ThreadsNormalizer:
    PLATFORM = "threads"

    def normalize(self, items: list[dict], keyword_id: uuid.UUID) -> list[Post]:
        return [self._to_post(item, keyword_id) for item in items if item.get("id") or item.get("pk")]

    def _to_post(self, item: dict, keyword_id: uuid.UUID) -> Post:
        post_id = str(item.get("id") or item.get("pk") or "")
        user = item.get("user") or {}

        return Post(
            id=uuid.uuid4(),
            keyword_id=keyword_id,
            external_id=post_id,
            platform=self.PLATFORM,
            content=item.get("caption") or item.get("text", ""),
            author=user.get("username", ""),
            url=item.get("permalink"),
            metadata_={
                "likes": _safe_int(item.get("like_count")),
                "replies": _safe_int(item.get("reply_count")),
            },
            raw_data=item,
            published_at=_utc_from_timestamp(item.get("taken_at") or item.get("timestamp")),
            collected_at=datetime.now(timezone.utc),
        )


# ── Registry ──────────────────────────────────────────────────────────────────

_NORMALIZERS: dict[str, Any] = {
    "tiktok": TikTokNormalizer(),
    "youtube": YouTubeNormalizer(),
    "instagram": InstagramNormalizer(),
    "reddit": RedditNormalizer(),
    "threads": ThreadsNormalizer(),
}


def get_normalizer(platform: str) -> TikTokNormalizer | YouTubeNormalizer | InstagramNormalizer | RedditNormalizer | ThreadsNormalizer:
    normalizer = _NORMALIZERS.get(platform)
    if not normalizer:
        raise ValueError(f"No normalizer for platform: {platform}")
    return normalizer
