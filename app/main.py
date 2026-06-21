from contextlib import asynccontextmanager

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
from app.infrastructure.database.connection import engine
from app.infrastructure.logging.logger import setup_logging
from app.infrastructure.redis.connection import close_redis
from app.shared.config import settings
from app.shared.exceptions import AppException
from app.shared.utils import build_error_response


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    yield
    await close_redis()
    await engine.dispose()


app = FastAPI(
    title="Social Intelligence Platform",
    description="Sentiment AI - Social Media Analytics",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.app_debug else None,
    redoc_url="/redoc" if settings.app_debug else None,
)

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
    return JSONResponse(
        status_code=500,
        content=build_error_response("INTERNAL_ERROR", "An unexpected error occurred"),
    )


# ── Health Check ───────────────────────────────────────────────────────────────
@app.get("/health", tags=["health"])
async def health_check():
    return {"success": True, "data": {"status": "ok", "version": "1.0.0"}}


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
