from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ── Override layer utk credential third-party (halaman "Kelola API Key",
# permintaan user 2026-07-18) -- 9 credential di bawah ini ORIGINALNYA
# cuma bisa diubah lewat .env + rebuild image. Field pydantic aslinya
# di-rename jadi "<nama>_env" (tetap baca env var ASLI via validation_alias,
# TIDAK breaking .env yg sudah ada), lalu properti `<nama>` publik cek
# override Redis dulu SEBELUM jatuh ke nilai .env -- supaya 69 titik kode yg
# SUDAH baca `settings.<nama>` di seluruh project TIDAK PERLU diubah SAMA
# SEKALI, cukup ganti nilai lewat dashboard baru & langsung kepakai run
# berikutnya (async ATAU sync context, krn property access biasa bukan
# `await`). Redis client di sini SENGAJA sync (bukan redis.asyncio) supaya
# properti ini bisa diakses dari mana saja tanpa perlu `await`.
import redis as _redis_sync

_sync_redis_client: "_redis_sync.Redis | None" = None
_credential_test_overrides: dict[str, str] = {}
OVERRIDABLE_CREDENTIAL_NAMES = (
    "apify_api_token", "ensemble_data_api_token", "anthropic_api_key",
    "openai_api_key", "firecrawl_api_key", "tavily_api_key",
    "youtube_data_api_key", "facebook_access_token",
    "instagram_session_id", "instagram_csrf_token",
)


def _get_sync_redis():
    global _sync_redis_client
    if _sync_redis_client is None:
        _sync_redis_client = _redis_sync.from_url(
            settings.redis_url, decode_responses=True,
            socket_connect_timeout=1, socket_timeout=1,
        )
    return _sync_redis_client


def _resolve_credential(name: str, env_value: str) -> str:
    if name in _credential_test_overrides:
        return _credential_test_overrides[name]
    try:
        val = _get_sync_redis().get(f"credentials:{name}")
    except Exception:
        val = None
    return val if val else env_value


def _set_credential_test_override(name: str, value: str) -> None:
    """Dipakai HANYA oleh property setter (test manual mock/restore
    settings.xxx = "..." spt sebelumnya) -- TIDAK menulis ke Redis produksi."""
    _credential_test_overrides[name] = value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_name: str = "social-intelligence"
    app_env: str = "development"
    app_debug: bool = True
    app_secret_key: str = "change-me-in-production"

    # Database
    database_url: str = "postgresql+asyncpg://social_intelligence:password@localhost:5432/social_intelligence_db"
    postgres_pool_size: int = 10
    postgres_max_overflow: int = 20

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Elasticsearch
    elasticsearch_url: str = "http://localhost:9200"

    # JWT
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 43200  # 30 days
    jwt_refresh_token_expire_days: int = 30

    # EnsembleData API
    ensemble_data_base_url: str = "https://ensembledata.com/apis"
    ensemble_data_api_token_env: str = Field(default="", validation_alias="ENSEMBLE_DATA_API_TOKEN")
    ensemble_data_timeout: int = 30
    ensemble_data_max_retries: int = 3

    # Apify (pengganti EnsembleData untuk scraping Instagram)
    apify_api_token_env: str = Field(default="", validation_alias="APIFY_API_TOKEN")
    apify_actor_id: str = "ycQuEFDDZmgX7BAsL"  # social-media-sentiment-analysis-tool

    # Apify — Facebook SEARCH by keyword (beda dari apify_actor_id di atas yang
    # cuma bisa scrape profil yang SUDAH diketahui namanya). Dipakai
    # POST /facebook/discover untuk cari topik+akun Facebook langsung tanpa
    # AI menebak. Pay-per-result (~$0.003/hasil), pakai apify_api_token yang sama.
    facebook_search_actor_id: str = "danek/facebook-search-ppr"

    # Apify — Instagram SEARCH by keyword (beda dari apify_actor_id yang cuma
    # bisa scrape profil yang SUDAH diketahui usernamenya). Dipakai
    # GET /instagram/posts/search tingkat 3 untuk cari POST secara LANGSUNG
    # by keyword -- beda arsitektur dari Facebook (cari akun dulu baru scrape
    # akunnya): actor Instagram ini mengembalikan post nyata langsung,
    # sudah lengkap caption/hashtag/author/likes/comments. Diverifikasi live
    # 2026-07-09 (lihat docs/analisa-gap-instagram.md bagian C). Pay-per-event
    # ~$2.60/1000 hasil (cek pricing terbaru di
    # apify.com/apify/instagram-hashtag-scraper), pakai apify_api_token yang sama.
    instagram_search_actor_id: str = "apify/instagram-hashtag-scraper"

    # Apify — TikTok. SATU actor untuk scrape profil MAUPUN search by
    # keyword/hashtag (beda dari Facebook yang butuh 2 actor terpisah) — lihat
    # app/integrations/apify/tiktok.py. Pay-per-result (~$0.0037/hasil di tier
    # free, lebih murah di tier berbayar), pakai apify_api_token yang sama.
    tiktok_actor_id: str = "clockworks/tiktok-scraper"

    # Instagram search provider — cari & scrape profil by username, dengan
    # auto-fallback antar provider (lihat app/services/instagram/providers/)
    # 2026-07-20: apify_post_scraper jadi PRIMARY (fix gap thumbnail, actor
    # "apify" lama TERBUKTI tidak pernah kirim foto sama sekali) -- "apify"
    # lama tetap fallback kalau primary gagal/kuota habis, lihat registry.py
    instagram_search_provider_order: str = "apify_post_scraper,apify,ensembledata"  # urutan fallback, config-only
    instagram_search_daily_min: int = 3       # minimal panggilan search dijamin tersedia/hari
    instagram_shared_daily_budget: int = 10   # total kuota harian: search + panggilan Instagram ad-hoc lain

    # Instagram trend-recommendation scraping (lihat docs/trend-recommendations.md)
    # Nominal ini SENGAJA mudah diubah (env var, tanpa perlu ubah kode) — nilai
    # sekarang (5) adalah titik awal pasca-testing, bisa disesuaikan lagi lewat
    # INSTAGRAM_TREND_DAILY_BUDGET di .env kalau ketentuan berubah nanti.
    instagram_trend_daily_budget: int = 5   # maks topik trend_recommendations di-scrape/hari (Apify berbayar)
    instagram_trend_posts_per_topic: int = 3   # post diambil per akun, bisa diubah tanpa ubah kode
    instagram_trend_comments_per_post: int = 10

    # Viral discovery harian (lihat app/ai/llm/viral_discovery_service.py) —
    # provider AI bisa diganti via .env TANPA ubah kode (ai_discovery_provider:
    # anthropic | openai | ollama). Claude punya web_search bawaan (server-side,
    # infrastruktur Anthropic sendiri). Ollama browsing lewat tool_search custom
    # (Tavily, lihat tavily_api_key) — OpenAI belum diberi tool ini.
    ai_discovery_provider: str = "anthropic"

    anthropic_api_key_env: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    anthropic_model: str = "claude-opus-4-8"

    openai_api_key_env: str = Field(default="", validation_alias="OPENAI_API_KEY")
    openai_model: str = "gpt-4o"

    # Web search untuk provider Ollama (model lokal tidak punya browsing
    # bawaan, jadi butuh tool search eksternal) — auto-switch: Firecrawl
    # dicoba dulu (hasil lebih relevan/spesifik per tes), fallback ke Tavily
    # kalau Firecrawl gagal/limit/key kosong. Isi minimal salah satu.
    firecrawl_api_key_env: str = Field(default="", validation_alias="FIRECRAWL_API_KEY")   # daftar di firecrawl.dev
    tavily_api_key_env: str = Field(default="", validation_alias="TAVILY_API_KEY")      # daftar di tavily.com

    viral_discovery_max_topics: int = 5   # maks topik/hari dari AI discovery, ubah via .env

    # News Fase 2 — pipeline MANDIRI (app/services/news/trend_scrape_service.py),
    # TIDAK terikat/tergantung AI viral discovery Instagram dkk sama sekali
    # (murni search+scrape Firecrawl langsung, tanpa LLM). maks artikel BARU
    # (belum ada di DB) yang di-scrape penuh (Firecrawl /v1/scrape, berbayar)
    # per run harian.
    news_discovery_daily_budget: int = 10
    news_discovery_schedule_hour: int = 13
    news_discovery_schedule_minute: int = 0

    # Multi-Signal Trend Discovery — pipeline MANDIRI (app/services/trends/),
    # TIDAK menyentuh app/ai/llm/viral_discovery_service.py atau kode
    # platform manapun yang sudah ada. Tujuannya ganti "AI menebak viral"
    # dengan sinyal OBJEKTIF: native trending TikTok/Twitter (kalau ada) +
    # sapuan hashtag independen + Google Trends + silang-cek YouTube
    # TrendingTopic (baca-saja) -> topik yang dikonfirmasi >1 sumber baru
    # disubmit ke trend_recommendations (source='multi_signal_trending').
    twitter_trends_actor_id: str = "automation-lab/twitter-trends-scraper"  # native Trends X, diverifikasi live 2026-07-10
    trends_geo: str = "ID"
    trends_max_per_source: int = 10  # maks item diambil per sumber per run

    # Query sapuan generik utk TikTok/Instagram (TIDAK ada actor trending
    # native yang reliable per verifikasi live 2026-07-10 -- TikTok Trends
    # actor gagal 2x, Instagram memang tidak punya halaman trending publik)
    trends_sweep_queries: list[str] = [
        "viral hari ini",
        "trending Indonesia",
        "lagi rame",
    ]

    # Jadwal (WIB) -- 3 sumber jalan dulu, gabungan/triangulasi PALING
    # TERAKHIR (butuh data hari itu dari ketiganya + Google Trends)
    twitter_trends_schedule_hour: int = 14
    twitter_trends_schedule_minute: int = 0
    tiktok_trends_schedule_hour: int = 14
    tiktok_trends_schedule_minute: int = 15
    instagram_trends_schedule_hour: int = 14
    instagram_trends_schedule_minute: int = 30
    trends_combined_schedule_hour: int = 15
    trends_combined_schedule_minute: int = 0

    # Jadwal Celery Beat (WIB) — bisa diganti via .env tanpa ubah kode.
    # Default: viral discovery jalan 2 jam sebelum scrape supaya topik yang
    # ditemukan punya kesempatan discrape di hari yang sama.
    viral_discovery_schedule_hour: int = 7
    viral_discovery_schedule_minute: int = 0
    instagram_trend_scrape_schedule_hour: int = 9
    instagram_trend_scrape_schedule_minute: int = 0

    # Smart Search (app/services/search_topics/) — jadwal pemindaian ulang
    # harian (lihat app/services/search_topics/rescan_service.py) + cooldown
    # per platform sebelum boleh panggil Apify/Firecrawl lagi utk keyword yang
    # sama. cooldown_hours_expensive dipakai Instagram/News (tidak ada jalur
    # "refresh akun murah" seperti Facebook/TikTok/Twitter).
    search_topic_rescan_schedule_hour: int = 6
    search_topic_rescan_schedule_minute: int = 0
    search_topic_rescan_cooldown_hours: int = 24
    search_topic_rescan_cooldown_hours_expensive: int = 72

    # Jatah minimum terjamin utk submission Smart Search (source diawali
    # "smart_search_") di trend_recommendations, dari 20 slot/hari yang sama
    # dipakai AI viral-discovery + pencarian interaktif tiap platform. Lihat
    # _pick_eviction_candidate() di app/services/trend_recommendations/service.py.
    smart_search_reserved_slots: int = 5

    # Smart Search AI-context discovery (app/services/search_topics/ai_discovery_service.py)
    # -- AI dipandu SearchTopic recurring (name+keywords) utk cari perkembangan
    # BARU terkait, BEDA dari blind sweep (viral_discovery_max_topics) DAN
    # literal tier-3 (discovery.py, tanpa AI). Dua budget terpisah: berapa
    # TOPIK dapat panggilan AI/hari (kontrol biaya utama) vs berapa sub-topik
    # baru/panggilan. Jadwal jalan SETELAH rescan literal (06:00) & blind sweep
    # (07:00) supaya cek "sudah tercover" lihat hasil keduanya, tapi masih
    # sebelum konsumer harian Instagram (09:00) supaya sempat kepilih hari itu.
    smart_search_ai_discovery_max_topics_per_run: int = 5
    smart_search_ai_discovery_max_subtopics_per_topic: int = 3
    smart_search_ai_discovery_schedule_hour: int = 8
    smart_search_ai_discovery_schedule_minute: int = 0

    # YouTube Data API v3 (fallback saat EnsembleData quota habis)
    youtube_data_api_key_env: str = Field(default="", validation_alias="YOUTUBE_DATA_API_KEY")

    # Facebook / Meta Graph API — token resmi cuma bisa akses Page yang
    # dikelola sendiri (terverifikasi live 05 Juli 2026, lihat
    # docs/flow scrape/flow-scrap-facebook.md), dipakai GET /facebook/posts
    facebook_access_token_env: str = Field(default="", validation_alias="FACEBOOK_ACCESS_TOKEN")

    # Facebook — provider abstraction untuk pipeline trend_recommendations
    # (Subsistem B khusus Facebook, terpisah dari Instagram). Apify satu-
    # satunya provider aktif sekarang (Meta Graph API TIDAK bisa untuk akun
    # publik sembarangan, cuma Page milik sendiri) — slot provider lain
    # (mis. Meta app Business terverifikasi nanti) tinggal ditambah ke
    # FACEBOOK_SEARCH_PROVIDER_ORDER tanpa ubah kode pemanggil.
    facebook_search_provider_order: str = "apify"
    facebook_trend_daily_budget: int = 5
    facebook_trend_posts_per_topic: int = 3
    facebook_trend_comments_per_post: int = 10
    facebook_trend_scrape_schedule_hour: int = 10
    facebook_trend_scrape_schedule_minute: int = 0

    # TikTok — provider abstraction untuk pipeline trend_recommendations
    # (Subsistem B khusus TikTok, terpisah dari Instagram/Facebook), jadwal
    # 11:00 WIB (1 jam setelah Facebook) supaya tidak rebutan resource/tumpang
    # tindih waktu proses.
    tiktok_trend_daily_budget: int = 5
    tiktok_trend_posts_per_topic: int = 3
    tiktok_trend_comments_per_post: int = 10
    tiktok_trend_scrape_schedule_hour: int = 11
    tiktok_trend_scrape_schedule_minute: int = 0

    # Threads (2026-07-19) -- budget KECIL sengaja krn EnsembleData berbayar
    # & kuota harian TERBUKTI kecil saat live test (habis dari ~10 panggilan
    # uji coba). posts_per_topic dibatasi kecil krn search TIDAK terbukti
    # bisa pagination (1x panggilan = 1 batch tetap dari provider). Jadwal
    # 12:00 WIB (beda jam dari TikTok 11:00) biar tidak numpuk.
    threads_trend_daily_budget: int = 3
    threads_trend_posts_per_topic: int = 10
    threads_trend_comments_top_n: int = 3
    threads_trend_scrape_schedule_hour: int = 12
    threads_trend_scrape_schedule_minute: int = 0

    # Apify — Twitter/X. SATU actor untuk scrape profil, search by keyword,
    # DAN reply/comment (mode "responses" via post_id) — lihat
    # app/integrations/apify/twitter.py.
    twitter_actor_id: str = "danek/twitter-scraper"

    # Twitter — provider abstraction untuk pipeline trend_recommendations
    # (Subsistem B khusus Twitter, terpisah dari Facebook/TikTok/Instagram),
    # jadwal 12:00 WIB (1 jam setelah TikTok) supaya tidak rebutan resource.
    # max_comments per topic dibatasi lebih rendah dari platform lain karena
    # tiap balasan butuh actor call TERPISAH per tweet (lihat
    # app/integrations/apify/twitter.py) — lebih mahal per unit.
    twitter_trend_daily_budget: int = 5
    twitter_trend_posts_per_topic: int = 3
    twitter_trend_comments_per_post: int = 5
    twitter_trend_scrape_schedule_hour: int = 12
    twitter_trend_scrape_schedule_minute: int = 0

    # Instagram session (dari browser cookies — untuk scraping tanpa EnsembleData)
    instagram_session_id_env: str = Field(default="", validation_alias="INSTAGRAM_SESSION_ID")
    instagram_csrf_token_env: str = Field(default="", validation_alias="INSTAGRAM_CSRF_TOKEN")

    # Celery
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # Collector
    collector_max_pages: int = 5
    collector_default_platforms: str = "tiktok,youtube"

    # AI Models
    indobert_model_name: str = "mdhugol/indonesia-bert-sentiment-classification"
    bge_m3_model_name: str = "BAAI/bge-m3"
    gliner_model_name: str = "urchade/gliner_multi-v2.1"
    models_cache_dir: str = "/app/models_cache"
    ai_batch_size: int = 16

    # Ollama (Qwen3 8B)
    ollama_base_url: str = "http://ollama:11434"
    ollama_model_name: str = "qwen3:8b"
    ollama_timeout: int = 120

    # Reports
    report_output_dir: str = "/app/reports"

    # YouTube Pipeline
    youtube_default_project_id: str = ""        # auto-detect jika kosong
    youtube_trending_geo: str = "ID"
    youtube_trending_period: str = "24h"
    youtube_trending_limit: int = 10
    youtube_max_pages_per_keyword: int = 1      # limit EnsembleData: max 5 unit per search
    youtube_max_comment_pages: int = 1          # limit EnsembleData: max 5 unit per video
    youtube_max_comments_per_video: int = 5     # hemat token: 5 komentar/video
    youtube_max_videos_per_search: int = 2      # hemat token: 2 video per pencarian

    # Rate Limiting
    rate_limit_agents_max_requests: int = 10
    rate_limit_agents_window_seconds: int = 60

    # Rate limiting endpoint publik (tanpa login, key berbasis IP bukan user.id)
    # -- dipakai GET /youtube/trending-public, lihat app/infrastructure/rate_limit/ip_limiter.py
    rate_limit_public_max_requests: int = 30
    rate_limit_public_window_seconds: int = 60

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"

    # ── Properti credential yg bisa di-override Redis (lihat blok
    # _resolve_credential() di atas) -- masing2 baca field "_env"-nya sbg
    # fallback default kalau belum ada override tersimpan.
    @property
    def apify_api_token(self) -> str:
        return _resolve_credential("apify_api_token", self.apify_api_token_env)

    @apify_api_token.setter
    def apify_api_token(self, value: str) -> None:
        _set_credential_test_override("apify_api_token", value)

    @property
    def ensemble_data_api_token(self) -> str:
        return _resolve_credential("ensemble_data_api_token", self.ensemble_data_api_token_env)

    @ensemble_data_api_token.setter
    def ensemble_data_api_token(self, value: str) -> None:
        _set_credential_test_override("ensemble_data_api_token", value)

    @property
    def anthropic_api_key(self) -> str:
        return _resolve_credential("anthropic_api_key", self.anthropic_api_key_env)

    @anthropic_api_key.setter
    def anthropic_api_key(self, value: str) -> None:
        _set_credential_test_override("anthropic_api_key", value)

    @property
    def openai_api_key(self) -> str:
        return _resolve_credential("openai_api_key", self.openai_api_key_env)

    @openai_api_key.setter
    def openai_api_key(self, value: str) -> None:
        _set_credential_test_override("openai_api_key", value)

    @property
    def firecrawl_api_key(self) -> str:
        return _resolve_credential("firecrawl_api_key", self.firecrawl_api_key_env)

    @firecrawl_api_key.setter
    def firecrawl_api_key(self, value: str) -> None:
        _set_credential_test_override("firecrawl_api_key", value)

    @property
    def tavily_api_key(self) -> str:
        return _resolve_credential("tavily_api_key", self.tavily_api_key_env)

    @tavily_api_key.setter
    def tavily_api_key(self, value: str) -> None:
        _set_credential_test_override("tavily_api_key", value)

    @property
    def youtube_data_api_key(self) -> str:
        return _resolve_credential("youtube_data_api_key", self.youtube_data_api_key_env)

    @youtube_data_api_key.setter
    def youtube_data_api_key(self, value: str) -> None:
        _set_credential_test_override("youtube_data_api_key", value)

    @property
    def facebook_access_token(self) -> str:
        return _resolve_credential("facebook_access_token", self.facebook_access_token_env)

    @facebook_access_token.setter
    def facebook_access_token(self, value: str) -> None:
        _set_credential_test_override("facebook_access_token", value)

    @property
    def instagram_session_id(self) -> str:
        return _resolve_credential("instagram_session_id", self.instagram_session_id_env)

    @instagram_session_id.setter
    def instagram_session_id(self, value: str) -> None:
        _set_credential_test_override("instagram_session_id", value)

    @property
    def instagram_csrf_token(self) -> str:
        return _resolve_credential("instagram_csrf_token", self.instagram_csrf_token_env)

    @instagram_csrf_token.setter
    def instagram_csrf_token(self, value: str) -> None:
        _set_credential_test_override("instagram_csrf_token", value)


settings = Settings()


def get_settings() -> Settings:
    return settings
