"""
EnsembleData API endpoint registry.

Tambah endpoint baru di sini saja — client.py tidak perlu diubah.
Semua path relatif terhadap ENSEMBLE_DATA_BASE_URL.

Referensi: https://ensembledata.com/apis/docs
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Endpoint:
    path: str
    method: str = "GET"
    description: str = ""


# ── Customer ──────────────────────────────────────────────────────────────────
class CustomerEndpoints:
    GET_USED_UNITS = Endpoint("/customer/get-used-units", description="Cek pemakaian unit per tanggal")
    GET_HISTORY = Endpoint("/customer/get-history", description="Riwayat pemakaian N hari terakhir")


# ── TikTok ────────────────────────────────────────────────────────────────────
class TikTokEndpoints:
    HASHTAG_POSTS = Endpoint("/tt/hashtag/posts", description="~20 post dari hashtag")
    HASHTAG_POSTS_FULL = Endpoint("/tt/hashtag/posts-full", description="Full hashtag search")
    KEYWORD_POSTS = Endpoint("/tt/keyword/posts", description="Search post by keyword")
    KEYWORD_POSTS_FULL = Endpoint("/tt/keyword/posts-full", description="Full keyword search")
    USER_POSTS = Endpoint("/tt/user/posts", description="Post dari username")
    USER_POSTS_SECUID = Endpoint("/tt/user/posts-secuid", description="Post dari secuid")
    USER_INFO = Endpoint("/tt/user/info", description="Profil user dari username")
    USER_INFO_SECUID = Endpoint("/tt/user/info-secuid", description="Profil user dari secuid")
    USER_SEARCH = Endpoint("/tt/user/search", description="Cari user")
    POST_INFO = Endpoint("/tt/post/info", description="Detail satu post")
    POST_INFO_MULTIPLE = Endpoint("/tt/post/info-multiple", description="Detail banyak post sekaligus")
    POST_COMMENTS = Endpoint("/tt/post/comments", description="Komentar sebuah post")
    POST_COMMENT_REPLIES = Endpoint("/tt/post/comment-replies", description="Balasan komentar")
    MUSIC_SEARCH = Endpoint("/tt/music/search", description="Cari musik")
    MUSIC_POSTS = Endpoint("/tt/music/posts", description="Post yang menggunakan musik")
    MUSIC_DETAILS = Endpoint("/tt/music/details", description="Detail musik")
    USER_FOLLOWERS = Endpoint("/tt/user/followers", description="Daftar follower")
    USER_FOLLOWINGS = Endpoint("/tt/user/followings", description="Daftar following")
    USER_LIKED_POSTS = Endpoint("/tt/user/liked-posts", description="Post yang dilike user")
    LIVES_SEARCH = Endpoint("/tt/lives/search", description="Cari live stream")


# ── Instagram ─────────────────────────────────────────────────────────────────
class InstagramEndpoints:
    USER_POSTS = Endpoint("/ig/user/posts", description="Post dari user")
    USER_STATS = Endpoint("/ig/user/stats", description="Statistik dasar user")
    USER_INFO = Endpoint("/ig/user/info", description="Profil user")
    USER_INFO_DETAILED = Endpoint("/ig/user/info-detailed", description="Profil lengkap user")
    USER_FOLLOWERS = Endpoint("/ig/user/followers", description="Jumlah follower")
    USER_REELS = Endpoint("/ig/user/reels", description="Reels dari user")
    USER_TAGGED_POSTS = Endpoint("/ig/user/tagged-posts", description="Post yang tag user")
    POST_INFO_COMMENTS = Endpoint("/ig/post/info-comments", description="Post + komentar")
    POST_COMMENTS = Endpoint("/ig/post/comments", description="Komentar post")
    MUSIC_POSTS = Endpoint("/ig/music/posts", description="Post yang pakai musik")
    SEARCH = Endpoint("/ig/search", description="Search umum Instagram")


# ── YouTube ───────────────────────────────────────────────────────────────────
class YouTubeEndpoints:
    KEYWORD_SEARCH = Endpoint("/yt/keyword/search", description="Search video by keyword")
    CATEGORIES_FEATURED = Endpoint("/yt/categories/featured", description="Kategori unggulan")
    HASHTAG_SEARCH = Endpoint("/yt/hashtag/search", description="Search by hashtag")
    CHANNEL_INFO_DETAILED = Endpoint("/yt/channel/info-detailed", description="Detail channel")
    CHANNEL_VIDEOS = Endpoint("/yt/channel/videos", description="Video dari channel")
    CHANNEL_SHORTS = Endpoint("/yt/channel/shorts", description="Shorts dari channel")
    CHANNEL_STREAMS = Endpoint("/yt/channel/streams", description="Live streams channel")
    VIDEO_DETAILS = Endpoint("/yt/video/details", description="Detail video/short")
    CHANNEL_SUBSCRIBER_COUNT = Endpoint("/yt/channel/subscriber-count", description="Jumlah subscriber")
    CHANNEL_USERNAME_TO_ID = Endpoint("/yt/channel/username-to-id", description="Konversi username ke ID")
    CHANNEL_ID_TO_USERNAME = Endpoint("/yt/channel/id-to-username", description="Konversi ID ke username")
    MUSIC_ID_TO_SHORTS = Endpoint("/yt/music/id-to-shorts", description="Shorts dari music ID")
    VIDEO_COMMENTS = Endpoint("/yt/video/comments", description="Komentar video")


# ── Reddit ────────────────────────────────────────────────────────────────────
class RedditEndpoints:
    KEYWORD_SEARCH = Endpoint("/reddit/keyword/search", description="Search post by keyword")
    SUBREDDIT_POSTS = Endpoint("/reddit/subreddit/posts", description="Post dari subreddit")
    POST_COMMENTS = Endpoint("/reddit/post/comments", description="Komentar post Reddit")


# ── Twitter ───────────────────────────────────────────────────────────────────
class TwitterEndpoints:
    USER_INFO = Endpoint("/twitter/user/info", description="Profil user Twitter")
    USER_TWEETS = Endpoint("/twitter/user/tweets", description="Tweet dari user")
    POST_INFO = Endpoint("/twitter/post/info", description="Detail tweet")


# ── Threads ───────────────────────────────────────────────────────────────────
class ThreadsEndpoints:
    KEYWORD_SEARCH = Endpoint("/threads/keyword/search", description="Search Threads by keyword")
    USER_SEARCH = Endpoint("/threads/user/search", description="Cari user Threads")
    USER_INFO = Endpoint("/threads/user/info", description="Profil user Threads")
    USER_POSTS = Endpoint("/threads/user/posts", description="Post dari user Threads")
    POST_INFO_REPLIES = Endpoint("/threads/post/info-replies", description="Post + balasan")


# ── Twitch ────────────────────────────────────────────────────────────────────
class TwitchEndpoints:
    KEYWORD_SEARCH = Endpoint("/twitch/keyword/search", description="Search Twitch")
    USER_FOLLOWERS = Endpoint("/twitch/user/followers", description="Follower Twitch")


# ── Snapchat ──────────────────────────────────────────────────────────────────
class SnapchatEndpoints:
    USER_INFO = Endpoint("/snapchat/user/info", description="Profil user Snapchat")


# ── Registry: platform slug → endpoint class ──────────────────────────────────
PLATFORM_ENDPOINTS: dict[str, type] = {
    "tiktok": TikTokEndpoints,
    "instagram": InstagramEndpoints,
    "youtube": YouTubeEndpoints,
    "reddit": RedditEndpoints,
    "twitter": TwitterEndpoints,
    "threads": ThreadsEndpoints,
    "twitch": TwitchEndpoints,
    "snapchat": SnapchatEndpoints,
}

SUPPORTED_COLLECTION_PLATFORMS = ["tiktok", "youtube", "instagram", "reddit", "threads"]
