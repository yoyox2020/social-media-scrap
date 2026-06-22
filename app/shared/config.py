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
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    # EnsembleData API
    ensemble_data_base_url: str = "https://ensembledata.com/apis"
    ensemble_data_api_token: str = ""
    ensemble_data_timeout: int = 30
    ensemble_data_max_retries: int = 3

    # Celery
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # Collector
    collector_max_pages: int = 5
    collector_default_platforms: str = "tiktok,youtube,instagram"

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

    # Rate Limiting
    rate_limit_agents_max_requests: int = 10
    rate_limit_agents_window_seconds: int = 60

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"


settings = Settings()


def get_settings() -> Settings:
    return settings
