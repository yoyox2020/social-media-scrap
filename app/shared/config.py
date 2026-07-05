from pydantic_settings import BaseSettings, SettingsConfigDict


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
    ensemble_data_api_token: str = ""
    ensemble_data_timeout: int = 30
    ensemble_data_max_retries: int = 3

    # Apify (pengganti EnsembleData untuk scraping Instagram)
    apify_api_token: str = ""
    apify_actor_id: str = "ycQuEFDDZmgX7BAsL"  # social-media-sentiment-analysis-tool

    # Instagram search provider — cari & scrape profil by username, dengan
    # auto-fallback antar provider (lihat app/services/instagram/providers/)
    instagram_search_provider_order: str = "apify,ensembledata"  # urutan fallback, config-only
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
    # anthropic | openai | ollama). CATATAN: cuma Claude yang punya web_search
    # bawaan — OpenAI/Ollama tidak bisa browsing sama sekali, jadi hasilnya
    # dari pengetahuan training model (bisa basi), bukan data hari ini yang
    # sebenarnya. Gunakan Claude untuk hasil akurat.
    ai_discovery_provider: str = "anthropic"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-8"

    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    viral_discovery_max_topics: int = 10

    # Jadwal Celery Beat (WIB) — bisa diganti via .env tanpa ubah kode.
    # Default: viral discovery jalan 2 jam sebelum scrape supaya topik yang
    # ditemukan punya kesempatan discrape di hari yang sama.
    viral_discovery_schedule_hour: int = 7
    viral_discovery_schedule_minute: int = 0
    instagram_trend_scrape_schedule_hour: int = 9
    instagram_trend_scrape_schedule_minute: int = 0

    # YouTube Data API v3 (fallback saat EnsembleData quota habis)
    youtube_data_api_key: str = ""

    # Facebook / Meta Graph API — token resmi cuma bisa akses Page yang
    # dikelola sendiri (terverifikasi live 05 Juli 2026, lihat
    # docs/flow scrape/flow-scrap-facebook.md), dipakai GET /facebook/posts
    facebook_access_token: str = ""

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

    # Instagram session (dari browser cookies — untuk scraping tanpa EnsembleData)
    instagram_session_id: str = ""
    instagram_csrf_token: str = ""

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

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"


settings = Settings()


def get_settings() -> Settings:
    return settings
