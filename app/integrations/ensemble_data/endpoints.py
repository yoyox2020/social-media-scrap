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
    USER_POSTS        = Endpoint("/instagram/user/posts",          description="Post dari user — param: user_id, depth")
    USER_BASIC_INFO   = Endpoint("/instagram/user/basic-info",     description="Statistik dasar — param: user_id")
    USER_INFO         = Endpoint("/instagram/user/info",           description="Profil user — param: username")
    USER_INFO_DETAILED= Endpoint("/instagram/user/detailed-info",  description="Profil lengkap — param: username")
    USER_FOLLOWERS    = Endpoint("/instagram/user/followers",      description="Jumlah follower — param: user_id")
    USER_REELS        = Endpoint("/instagram/user/reels",          description="Reels — param: user_id, depth")
    USER_TAGGED_POSTS = Endpoint("/instagram/user/tagged-posts",   description="Post tag user — param: user_id, cursor")
    POST_DETAILS      = Endpoint("/instagram/post/details",        description="Detail post + komentar inline — param: code, n_comments_to_fetch")
    POST_COMMENTS     = Endpoint("/instagram/post/comments",       description="Komentar post — param: media_id, cursor, sorting")
    SEARCH            = Endpoint("/instagram/search",              description="Search — param: text")


# ── YouTube ───────────────────────────────────────────────────────────────────
# Paths berdasarkan EnsembleData docs resmi (https://ensembledata.com/apis)
class YouTubeEndpoints:
    KEYWORD_SEARCH = Endpoint("/youtube/search", description="Search video by keyword (param: keyword, depth)")
    HASHTAG_SEARCH = Endpoint("/youtube/hashtag/search", description="Search by hashtag (param: name, depth, only_shorts)")
    FEATURED_CATEGORIES = Endpoint("/youtube/search/featured-categories", description="Kategori unggulan (param: name)")
    CHANNEL_INFO_DETAILED = Endpoint("/youtube/channel/detailed-info", description="Detail channel (param: browseId)")
    CHANNEL_VIDEOS = Endpoint("/youtube/channel/videos", description="Video dari channel (param: browseId)")
    CHANNEL_SHORTS = Endpoint("/youtube/channel/shorts", description="Shorts dari channel (param: browseId)")
    CHANNEL_STREAMS = Endpoint("/youtube/channel/streams", description="Live streams channel (param: browseId)")
    VIDEO_DETAILS = Endpoint("/youtube/video/details", description="Detail video (param: id)")
    CHANNEL_FOLLOWERS = Endpoint("/youtube/channel/followers", description="Jumlah subscriber (param: browseId)")
    CHANNEL_USERNAME_TO_ID = Endpoint("/youtube/channel/username-to-id", description="Konversi username ke browseId")
    CHANNEL_ID_TO_NAME = Endpoint("/youtube/channel/id-to-name", description="Konversi browseId ke username (param: browseId)")
    MUSIC_ID_TO_SHORTS = Endpoint("/youtube/music/id-to-shorts", description="Shorts dari music ID")
    VIDEO_COMMENTS = Endpoint("/youtube/video/comments", description="Komentar video (param: id, cursor)")


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

SUPPORTED_COLLECTION_PLATFORMS = ["tiktok", "youtube", "reddit", "threads"]
# Instagram dihapus dari daftar ini — Apify (pengganti EnsembleData) tidak
# punya fitur cari-by-keyword/hashtag yang dibutuhkan collector generik ini.
# Lihat docs/trend-recommendations.md untuk alur Instagram yang baru.
