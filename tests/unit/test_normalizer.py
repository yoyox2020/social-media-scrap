"""Unit tests untuk normalizer — tidak butuh DB atau network."""
import uuid

import pytest

from app.services.processing.normalizer import (
    TikTokNormalizer,
    YouTubeNormalizer,
    InstagramNormalizer,
    RedditNormalizer,
    get_normalizer,
)


KW_ID = uuid.uuid4()


# ── TikTok ────────────────────────────────────────────────────────────────────

def test_tiktok_normalizer_basic():
    items = [
        {
            "aweme_id": "7123456789",
            "desc": "Video keren #viral",
            "author": {"unique_id": "user123", "nickname": "User Keren"},
            "statistics": {"digg_count": 1000, "comment_count": 50, "share_count": 20, "play_count": 50000},
            "create_time": 1700000000,
        }
    ]
    posts = TikTokNormalizer().normalize(items, KW_ID)
    assert len(posts) == 1
    p = posts[0]
    assert p.external_id == "7123456789"
    assert p.platform == "tiktok"
    assert p.author == "user123"
    assert p.content == "Video keren #viral"
    assert "tiktok.com" in (p.url or "")
    assert p.metadata_["likes"] == 1000
    assert p.metadata_["views"] == 50000
    assert p.published_at is not None
    assert p.keyword_id == KW_ID


def test_tiktok_normalizer_skips_item_without_aweme_id():
    items = [{"desc": "no id here", "author": {}}]
    posts = TikTokNormalizer().normalize(items, KW_ID)
    assert posts == []


def test_tiktok_normalizer_handles_missing_fields():
    items = [{"aweme_id": "999"}]
    posts = TikTokNormalizer().normalize(items, KW_ID)
    assert len(posts) == 1
    assert posts[0].content == ""
    assert posts[0].metadata_["likes"] == 0


# ── YouTube ───────────────────────────────────────────────────────────────────

def test_youtube_normalizer_basic():
    items = [
        {
            "video_id": "abc123",
            "title": "Tutorial Python",
            "channel_name": "ProgrammerChannel",
            "view_count": 100000,
            "like_count": 5000,
            "comment_count": 300,
            "published_at": "2024-01-15T10:00:00Z",
        }
    ]
    posts = YouTubeNormalizer().normalize(items, KW_ID)
    assert len(posts) == 1
    p = posts[0]
    assert p.external_id == "abc123"
    assert p.platform == "youtube"
    assert p.author == "ProgrammerChannel"
    assert p.content == "Tutorial Python"
    assert "youtube.com/watch?v=abc123" in (p.url or "")
    assert p.metadata_["views"] == 100000
    assert p.published_at is not None


# ── Instagram ─────────────────────────────────────────────────────────────────

def test_instagram_normalizer_basic():
    items = [
        {
            "id": "IG12345",
            "shortcode": "BxABCDEF",
            "caption": "Foto bagus #sunset",
            "owner": {"username": "fotografer"},
            "like_count": 200,
            "comments_count": 15,
            "taken_at": 1700000000,
        }
    ]
    posts = InstagramNormalizer().normalize(items, KW_ID)
    assert len(posts) == 1
    p = posts[0]
    assert p.external_id == "IG12345"
    assert p.platform == "instagram"
    assert p.author == "fotografer"
    assert "instagram.com/p/" in (p.url or "")


# ── Reddit ────────────────────────────────────────────────────────────────────

def test_reddit_normalizer_nested_data():
    items = [
        {
            "data": {
                "id": "reddit123",
                "title": "Pertanyaan tentang Python",
                "selftext": "Bagaimana cara membuat API?",
                "author": "redditor_user",
                "permalink": "/r/Python/comments/reddit123/",
                "score": 150,
                "num_comments": 25,
                "subreddit": "Python",
                "created_utc": 1700000000,
            }
        }
    ]
    posts = RedditNormalizer().normalize(items, KW_ID)
    assert len(posts) == 1
    p = posts[0]
    assert p.external_id == "reddit123"
    assert p.platform == "reddit"
    assert "reddit.com/r/Python" in (p.url or "")
    assert p.metadata_["subreddit"] == "Python"


# ── Registry ──────────────────────────────────────────────────────────────────

def test_get_normalizer_returns_correct_instance():
    assert isinstance(get_normalizer("tiktok"), TikTokNormalizer)
    assert isinstance(get_normalizer("youtube"), YouTubeNormalizer)
    assert isinstance(get_normalizer("instagram"), InstagramNormalizer)
    assert isinstance(get_normalizer("reddit"), RedditNormalizer)


def test_get_normalizer_raises_for_unknown_platform():
    with pytest.raises(ValueError, match="No normalizer"):
        get_normalizer("unknown_platform")
