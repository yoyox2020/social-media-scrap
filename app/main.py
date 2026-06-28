from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import (
    agents,
    auth,
    collectors,
    entities,
    keywords,
    processing,
    reports,
    search,
    sentiment,
    topics,
    trends,
)
# Import semua domain models agar SQLAlchemy mapper bisa resolve relationship
import app.domain.users.models  # noqa: F401
import app.domain.projects.models  # noqa: F401
import app.domain.keywords.models  # noqa: F401
import app.domain.posts.models  # noqa: F401
import app.domain.comments.models  # noqa: F401
import app.domain.sentiments.models  # noqa: F401
import app.domain.entities.models  # noqa: F401
import app.domain.topics.models  # noqa: F401
import app.domain.trends.models  # noqa: F401
import app.domain.reports.models  # noqa: F401
import app.domain.trending.models  # noqa: F401
import app.domain.youtube_analysis.models  # noqa: F401

from app.api.v1.youtube.router import router as youtube_router
from app.infrastructure.database.connection import engine
from app.infrastructure.logging.logger import get_logger, setup_logging
from app.infrastructure.middleware.request_id import RequestIDMiddleware
from app.infrastructure.redis.connection import close_redis, get_redis
from app.shared.config import settings
from app.shared.exceptions import AppException
from app.shared.utils import build_error_response

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("app_starting", env=settings.app_env, version="1.0.0")
    yield
    logger.info("app_stopping")
    await close_redis()
    await engine.dispose()


app = FastAPI(
    title="Social Intelligence Platform",
    description="Sentiment AI - Social Media Analytics",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.app_debug else None,
    redoc_url="/redoc" if settings.app_debug else None,
    swagger_ui_parameters={
        "persistAuthorization": True,   # token tersimpan di localStorage, tidak hilang saat refresh
        "displayRequestDuration": True, # tampilkan durasi request
        "tryItOutEnabled": True,        # tombol "Try it out" aktif by default
    },
)

app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=build_error_response(exc.code, exc.message),
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled_exception", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content=build_error_response("INTERNAL_ERROR", "An unexpected error occurred"),
    )


# ── Health Check ───────────────────────────────────────────────────────────────
@app.get("/health", tags=["health"])
async def health_check():
    """Cek konektivitas semua infrastruktur: DB, Redis, Ollama, Elasticsearch."""
    checks: dict[str, dict] = {}

    # Database
    try:
        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy", fromlist=["text"]).text("SELECT 1"))
        checks["database"] = {"status": "ok"}
    except Exception as exc:
        checks["database"] = {"status": "error", "detail": str(exc)}

    # Redis
    try:
        redis = await get_redis()
        await redis.ping()
        checks["redis"] = {"status": "ok"}
    except Exception as exc:
        checks["redis"] = {"status": "error", "detail": str(exc)}

    # Ollama
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            resp.raise_for_status()
        checks["ollama"] = {"status": "ok"}
    except Exception as exc:
        checks["ollama"] = {"status": "error", "detail": str(exc)}

    # Elasticsearch
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{settings.elasticsearch_url}/_cluster/health")
            resp.raise_for_status()
        checks["elasticsearch"] = {"status": "ok"}
    except Exception as exc:
        checks["elasticsearch"] = {"status": "error", "detail": str(exc)}

    overall = "ok" if all(v["status"] == "ok" for v in checks.values()) else "degraded"
    status_code = 200 if overall == "ok" else 207

    return JSONResponse(
        status_code=status_code,
        content={
            "success": True,
            "data": {
                "status": overall,
                "version": "1.0.0",
                "checks": checks,
            },
        },
    )


# ── API v1 Routers ─────────────────────────────────────────────────────────────
API_PREFIX = "/api/v1"

app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(keywords.router, prefix=API_PREFIX)
app.include_router(collectors.router, prefix=API_PREFIX)
app.include_router(processing.router, prefix=API_PREFIX)
app.include_router(sentiment.router, prefix=API_PREFIX)
app.include_router(topics.router, prefix=API_PREFIX)
app.include_router(entities.router, prefix=API_PREFIX)
app.include_router(trends.router, prefix=API_PREFIX)
app.include_router(search.router, prefix=API_PREFIX)
app.include_router(agents.router, prefix=API_PREFIX)
app.include_router(reports.router, prefix=API_PREFIX)
app.include_router(youtube_router, prefix=API_PREFIX)
