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
from app.services.processing.cleaner import default_cleaner
from app.services.processing.text_normalizer import default_normalizer

_HASHTAG_RE = re.compile(r"#(\w+)")


def _extract_hashtags(text: str | None) -> list[str]:
    """Ekstrak hashtag literal ("#kata") dari teks post/caption -- MVP,
    bukan dari field tags terstruktur API (kebanyakan platform di pipeline
    ini tidak expose itu di endpoint yg kita pakai)."""
    if not text:
        return []
    return list(dict.fromkeys(_HASHTAG_RE.findall(text)))  # dedup, urutan tetap


def _media_list(url: str | None, kind: str = "image") -> list[dict]:
    """Bangun list media dari 1 URL (thumbnail/gambar) yg sudah ada --
    MVP cuma 1 item, belum tangkap multi-gambar/carousel per platform."""
    return [{"type": kind, "url": url}] if url else []


def _detect_lang(text: str | None) -> str:
    """Deteksi bahasa post saat ini juga (saat normalisasi/create), BUKAN
    lewat ProcessingService.process_keyword() terpisah -- itu pipeline lama
    yg cuma jalan kalau dipicu manual (POST /processing/trigger), jadi
    posts.language selalu NULL di praktiknya. Reuse heuristik yg sama
    (TextNormalizer.detect_language), cuma dipanggil lebih awal."""
    return default_normalizer.detect_language(default_cleaner.clean(text or ""))


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

        content = item.get("desc", "")
        likes = _safe_int(stats.get("digg_count"))
        comments = _safe_int(stats.get("comment_count"))
        shares = _safe_int(stats.get("share_count"))
        views = _safe_int(stats.get("play_count"))

        return Post(
            id=uuid.uuid4(),
            keyword_id=keyword_id,
            external_id=aweme_id,
            platform=self.PLATFORM,
            content=content,
            author=username,
            url=f"https://www.tiktok.com/@{username}/video/{aweme_id}" if aweme_id else None,
            tags=_extract_hashtags(content),
            media=[],  # TikTok: cover/thumbnail belum diekstrak dari raw response (gap terpisah)
            metrics={"views": views, "likes": likes, "comments": comments, "shares": shares},
            language=_detect_lang(content),
            metadata_={
                "likes": likes,
                "comments": comments,
                "shares": shares,
                "views": views,
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
                title=title,
                tags=_extract_hashtags(title + " " + description),
                media=_media_list(thumb_url),
                metrics={"views": 0, "likes": 0, "comments": 0, "shares": 0},  # diisi enrich_youtube_statistics()
                language=_detect_lang(title + " " + description),
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
            title=title,
            tags=_extract_hashtags(title + " " + description),
            media=_media_list(thumb_url),
            metrics={"views": views, "likes": 0, "comments": 0, "shares": 0},  # likes/comments diisi enrich_youtube_statistics()
            language=_detect_lang(title + " " + description),
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


async def enrich_youtube_statistics(posts: list[Post]) -> None:
    """
    Isi views/likes/comments video YouTube yang SEBELUMNYA selalu 0 -- baik
    dari hasil search EnsembleData (videoRenderer) maupun YouTube Data API v3
    (search.list), DUA-DUANYA endpoint search yang secara STRUKTURAL tidak
    menyertakan statistics sama sekali (beda dari endpoint videos.list).
    Root cause asli: pipeline ini cuma pernah pakai endpoint search, tidak
    pernah manggil videos.list?part=statistics sama sekali -- bukan
    keterbatasan provider (lihat YouTubeDataAPIClient.get_videos_statistics()).

    Mutasi `posts` IN PLACE (aman -- objek ini belum di-add/commit ke session
    manapun saat dipanggil dari CollectorService, jadi tidak ada isu
    SQLAlchemy change-tracking). No-op diam-diam kalau YOUTUBE_DATA_API_KEY
    belum di-set atau panggilannya gagal -- ini ENRICHMENT, kegagalannya
    TIDAK boleh menggagalkan penyimpanan post itu sendiri (post tetap
    tersimpan, cuma likes/comments-nya tetap 0 seperti sebelumnya).
    """
    from app.shared.config import settings

    if not settings.youtube_data_api_key or not posts:
        return

    import logging

    from app.integrations.youtube_data_api.client import YouTubeDataAPIClient

    logger = logging.getLogger(__name__)
    video_ids = [p.external_id for p in posts if p.external_id]
    if not video_ids:
        return

    try:
        client = YouTubeDataAPIClient(api_key=settings.youtube_data_api_key)
        stats_by_id = await client.get_videos_statistics(video_ids)
    except Exception as exc:
        logger.warning("enrich_youtube_statistics: gagal ambil statistics (%s), likes/comments tetap 0", exc)
        return

    for post in posts:
        stats = stats_by_id.get(post.external_id)
        if not stats:
            continue
        post.metadata_["views"] = stats["views"]
        post.metadata_["likes"] = stats["likes"]
        post.metadata_["comments"] = stats["comments"]
        # metrics HARUS ikut di-update di sini juga -- kalau tidak, metrics
        # akan diam-diam nyangkut di nilai awal (views asli tapi likes/
        # comments selalu 0) sementara metadata_ sudah benar, bikin 2 sumber
        # data post yg sama saling beda nilai.
        if post.metrics is not None:
            post.metrics["views"] = stats["views"]
            post.metrics["likes"] = stats["likes"]
            post.metrics["comments"] = stats["comments"]


# ── Instagram ─────────────────────────────────────────────────────────────────

class InstagramNormalizer:
    PLATFORM = "instagram"

    def normalize(self, items: list[dict], keyword_id: uuid.UUID | None) -> list[Post]:
        return [self._to_post(item, keyword_id) for item in items if self._get_post_id(item)]

    def _get_post_id(self, item: dict) -> str:
        raw = str(item.get("pk") or item.get("id") or item.get("shortcode") or "")
        return raw.split("_")[0]  # pk kadang format "123456_userid"

    def _to_post(self, item: dict, keyword_id: uuid.UUID | None) -> Post:
        post_id = self._get_post_id(item)
        owner = item.get("user") or item.get("owner") or {}
        username = owner.get("username", "") or item.get("username", "")
        shortcode = item.get("shortcode") or item.get("code") or ""

        # caption bisa nested {"text": "..."} atau string langsung
        caption_raw = item.get("caption") or {}
        if isinstance(caption_raw, dict):
            caption = caption_raw.get("text", "")
        else:
            caption = str(caption_raw) if caption_raw else ""
        caption = caption or item.get("accessibility_caption", "")

        # thumbnail
        img = item.get("image_versions2") or {}
        candidates = img.get("candidates") or []
        thumbnail = candidates[0].get("url", "") if candidates else item.get("thumbnail_url", "")

        is_video = bool(item.get("is_video") or item.get("media_type") == 2)
        likes = _safe_int(item.get("like_count"))
        comments = _safe_int(item.get("comment_count") or item.get("comments_count"))
        views = _safe_int(item.get("view_count") or item.get("play_count"))

        return Post(
            id=uuid.uuid4(),
            keyword_id=keyword_id,
            external_id=post_id,
            platform=self.PLATFORM,
            content=caption,
            author=username,
            url=item.get("permalink") or (f"https://www.instagram.com/p/{shortcode}/" if shortcode else None),
            tags=_extract_hashtags(caption),
            media=_media_list(thumbnail, "video" if is_video else "image"),
            metrics={"views": views, "likes": likes, "comments": comments, "shares": 0},
            language=_detect_lang(caption),
            metadata_={
                "likes":       likes,
                "comments":    comments,
                "media_type":  item.get("media_type", ""),
                "is_video":    is_video,
                "thumbnail":   thumbnail,
                "shortcode":   shortcode,
                "views":       views,
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

        title = data.get("title", "")
        content = title + (" " + data.get("selftext", "") if data.get("selftext") else "")
        score = _safe_int(data.get("score"))
        comments = _safe_int(data.get("num_comments"))

        return Post(
            id=uuid.uuid4(),
            keyword_id=keyword_id,
            external_id=post_id,
            platform=self.PLATFORM,
            content=content,
            author=data.get("author", ""),
            url=f"https://www.reddit.com{data.get('permalink', '')}" if data.get("permalink") else None,
            title=title,
            tags=_extract_hashtags(content),
            media=[],  # Reddit: gambar post belum diekstrak dari raw response
            metrics={"views": 0, "likes": score, "comments": comments, "shares": 0},  # score = analog terdekat "likes"
            language=_detect_lang(content),
            metadata_={
                "score": score,
                "upvote_ratio": data.get("upvote_ratio", 0),
                "comments": comments,
                "subreddit": data.get("subreddit", ""),
                "is_self": data.get("is_self", True),
            },
            raw_data=item,
            published_at=_utc_from_timestamp(data.get("created_utc")),
            collected_at=datetime.now(timezone.utc),
        )


# ── Threads ───────────────────────────────────────────────────────────────────
# CATATAN 2026-07-19: normalizer INI SEBELUMNYA menebak bentuk raw response
# (item.get("id")/item.get("caption")/item.get("permalink") dst, semua flat)
# -- TERBUKTI SALAH TOTAL saat dites live ke EnsembleData asli. Bentuk asli
# `post` (hasil ThreadsConnector.extract_posts()/extract_replies()) adalah
# objek Instagram-family yang JAUH lebih dalam: `pk` (ID asli), `code`
# (shortcode utk URL), `user.username`, `caption.text` (teks LENGKAP sudah
# direkonstruksi API, tidak perlu gabung text_fragments manual),
# `like_count` (TOP-LEVEL, bukan di caption), `text_post_app_info.
# direct_reply_count/repost_count/quote_count`, `taken_at` (unix epoch
# detik), `image_versions2.candidates[]`/`video_versions[]` utk media.


class ThreadsNormalizer:
    PLATFORM = "threads"

    def normalize(self, items: list[dict], keyword_id: uuid.UUID) -> list[Post]:
        return [self._to_post(item, keyword_id) for item in items if item.get("pk")]

    def _to_post(self, item: dict, keyword_id: uuid.UUID) -> Post:
        post_pk = str(item.get("pk") or "")
        code = item.get("code") or ""
        user = item.get("user") or {}
        username = user.get("username") or ""

        # caption.text SUDAH teks lengkap direkonstruksi API (termasuk
        # @mention) -- fallback ke gabungan text_fragments kalau caption
        # kosong (ditemukan live: post reply kadang caption-nya null).
        caption = item.get("caption") or {}
        content = caption.get("text") if isinstance(caption, dict) else None
        if not content:
            app_info = item.get("text_post_app_info") or {}
            fragments = (app_info.get("text_fragments") or {}).get("fragments") or []
            if isinstance(fragments, list):
                content = "".join(f.get("plaintext") or "" for f in fragments if isinstance(f, dict))
        content = content or ""

        app_info = item.get("text_post_app_info") or {}
        likes = _safe_int(item.get("like_count"))
        replies = _safe_int(app_info.get("direct_reply_count"))
        reposts = _safe_int(app_info.get("repost_count"))
        quotes = _safe_int(app_info.get("quote_count"))

        media: list[dict] = []
        for cand in (item.get("image_versions2") or {}).get("candidates") or []:
            if cand.get("url"):
                media.append({"type": "image", "url": cand["url"]})
                break  # ambil resolusi pertama (biasanya terbesar) saja, cukup utk thumbnail
        video_versions = item.get("video_versions") or []
        if video_versions and video_versions[0].get("url"):
            media.append({"type": "video", "url": video_versions[0]["url"]})

        url = f"https://www.threads.net/@{username}/post/{code}" if username and code else None

        return Post(
            id=uuid.uuid4(),
            keyword_id=keyword_id,
            external_id=post_pk,
            platform=self.PLATFORM,
            content=content,
            author=username,
            url=url,
            tags=_extract_hashtags(content),
            media=media,
            metrics={"views": 0, "likes": likes, "comments": replies, "shares": reposts + quotes},
            language=_detect_lang(content),
            metadata_={
                "likes": likes,
                "replies": replies,
                "reposts": reposts,
                "quotes": quotes,
                "code": code,
                "source": "ensembledata",
            },
            raw_data=item,
            published_at=_utc_from_timestamp(item.get("taken_at")),
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
