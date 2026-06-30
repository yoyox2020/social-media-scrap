"""
Normalizer: konversi raw response EnsembleData → model Post.

Setiap platform punya struktur JSON yang berbeda. Normalizer
mengekstrak field yang relevan dan menyimpan raw_data secara utuh.
"""
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.domain.posts.models import Post


def _parse_relative_time(text: str, reference: datetime | None = None) -> datetime | None:
    """
    Konversi relative time YouTube ('3 months ago', 'Streamed 1 year ago') → datetime UTC.
    reference: titik waktu acuan (default: now). Untuk backfill, gunakan collected_at.
    """
    if not text:
        return None
    now = reference or datetime.now(timezone.utc)
    t = text.strip().lower()

    _UNITS = {
        "second": lambda n: timedelta(seconds=n),
        "minute": lambda n: timedelta(minutes=n),
        "hour":   lambda n: timedelta(hours=n),
        "day":    lambda n: timedelta(days=n),
        "week":   lambda n: timedelta(weeks=n),
        "month":  lambda n: timedelta(days=n * 30),
        "year":   lambda n: timedelta(days=n * 365),
    }
    m = re.search(r"(\d+)\s+(second|minute|hour|day|week|month|year)", t)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return now - _UNITS[unit](n)


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
        # videoRenderer dari /youtube/search
        return item.get("videoId") or item.get("video_id") or ""

    @staticmethod
    def _runs_to_text(obj: dict | None) -> str:
        """Ambil teks dari format {runs: [{text: ...}]} milik YouTube."""
        if not obj:
            return ""
        runs = obj.get("runs") or []
        return "".join(r.get("text", "") for r in runs)

    def _to_post(self, item: dict, keyword_id: uuid.UUID) -> Post:
        video_id = self._get_video_id(item)
        collected = datetime.now(timezone.utc)

        if item.get("_yt_api"):
            # ── YouTube Data API v3 format ──────────────────────────────────
            snippet = item.get("snippet") or {}
            title = snippet.get("title", "")
            channel = snippet.get("channelTitle", "")
            description = snippet.get("description", "")
            thumbs = snippet.get("thumbnails") or {}
            thumb_url = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
            published_at = _utc_from_iso(snippet.get("publishedAt"))
            return Post(
                id=uuid.uuid4(),
                keyword_id=keyword_id,
                external_id=video_id,
                platform=self.PLATFORM,
                content=title,
                author=channel,
                url=f"https://www.youtube.com/watch?v={video_id}" if video_id else None,
                metadata_={
                    "views": 0,
                    "likes": 0,
                    "comments": 0,
                    "description": description,
                    "thumbnail": thumb_url,
                    "source": "youtube_data_api",
                },
                raw_data=item,
                published_at=published_at,
                collected_at=collected,
            )

        # ── EnsembleData videoRenderer format ──────────────────────────────
        title = self._runs_to_text(item.get("title"))
        channel = self._runs_to_text(item.get("ownerText") or item.get("shortBylineText"))

        views_raw = (item.get("viewCountText") or {}).get("simpleText", "0 views")
        views = _safe_int("".join(c for c in views_raw if c.isdigit()))

        thumbs = (item.get("thumbnail") or {}).get("thumbnails", [])
        thumb_url = thumbs[-1].get("url", "") if thumbs else ""

        desc_runs = (
            item.get("descriptionSnippet")
            or item.get("detailedMetadataSnippets", [{}])[0].get("snippetText")
            or {}
        )
        description = self._runs_to_text(desc_runs if isinstance(desc_runs, dict) else {})

        published_text = (item.get("publishedTimeText") or {}).get("simpleText", "")

        return Post(
            id=uuid.uuid4(),
            keyword_id=keyword_id,
            external_id=video_id,
            platform=self.PLATFORM,
            content=title,
            author=channel,
            url=f"https://www.youtube.com/watch?v={video_id}" if video_id else None,
            metadata_={
                "views": views,
                "likes": 0,
                "comments": 0,
                "description": description,
                "thumbnail": thumb_url,
                "published_text": published_text,
                "duration": (item.get("lengthText") or {}).get("simpleText", ""),
            },
            raw_data=item,
            published_at=_parse_relative_time(published_text, reference=collected),
            collected_at=collected,
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
