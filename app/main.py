from contextlib import asynccontextmanager
import json

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse


def _fix_leading_zeros(s: str) -> str:
    """
    Perbaiki leading zero pada angka JSON di luar string.
    Contoh: 05 → 5, 007 → 7. Tidak mengubah 0.5 atau string "007".
    """
    result = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == '"':
            # Salin string secara utuh — jangan ubah isi string
            result.append(c)
            i += 1
            while i < n:
                ch = s[i]
                result.append(ch)
                if ch == '\\' and i + 1 < n:
                    i += 1
                    result.append(s[i])
                elif ch == '"':
                    break
                i += 1
        elif c == '0' and i + 1 < n and s[i + 1].isdigit():
            # Leading zero di luar string — strip semua leading zero
            while i < n and s[i] == '0' and i + 1 < n and s[i + 1].isdigit():
                i += 1
            result.append(s[i])
        else:
            result.append(c)
        i += 1
    return ''.join(result)

from app.api.v1 import (
    agents,
    auth,
    collectors,
    entities,
    keywords,
    metrics,
    processing,
    reports,
    search,
    sentiment,
    topic_search,
    topics,
    trend_recommendations,
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
import app.domain.search_topics.models  # noqa: F401
import app.domain.scrape_runs.models  # noqa: F401
import app.domain.instagram_trending.models  # noqa: F401
import app.domain.trend_recommendations.models  # noqa: F401

from app.api.v1.youtube.router import router as youtube_router
from app.api.v1.instagram.router import router as instagram_router
from app.api.v1.facebook.router import router as facebook_router
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
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def sanitize_json_body(request: Request, call_next):
    """
    Auto-fix JSON body yang tidak valid sebelum FastAPI mem-parse-nya.
    Saat ini menangani: leading zeros pada angka (05 → 5, 007 → 7).
    Tidak mengubah request yang JSON-nya sudah valid.
    """
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type and request.method in ("POST", "PUT", "PATCH"):
        body = await request.body()
        if body:
            body_str = body.decode("utf-8", errors="replace")
            try:
                json.loads(body_str)
            except json.JSONDecodeError:
                fixed = _fix_leading_zeros(body_str)
                try:
                    json.loads(fixed)
                    request._body = fixed.encode("utf-8")
                except json.JSONDecodeError:
                    pass  # Masih invalid → biarkan FastAPI handle error aslinya
    return await call_next(request)


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


@app.get("/scraping-status", response_class=HTMLResponse, include_in_schema=False)
async def scraping_status_page():
    """Halaman monitoring scraping — tidak perlu login, bisa dibuka langsung di browser."""
    html = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Scraping Monitor</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; padding: 24px; }
  h1 { font-size: 1.5rem; font-weight: 700; margin-bottom: 4px; }
  .subtitle { color: #64748b; font-size: 0.85rem; margin-bottom: 20px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 24px; }
  .card { background: #1e293b; border-radius: 10px; padding: 16px; }
  .card .label { font-size: 0.75rem; color: #64748b; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 6px; }
  .card .value { font-size: 1.8rem; font-weight: 700; }
  .card .sub { font-size: 0.75rem; color: #94a3b8; margin-top: 2px; }
  .green { color: #4ade80; }
  .yellow { color: #fbbf24; }
  .red { color: #f87171; }
  .blue { color: #60a5fa; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 99px; font-size: 0.7rem; font-weight: 600; }
  .pill-success  { background: #14532d; color: #4ade80; }
  .pill-failed   { background: #450a0a; color: #f87171; }
  .pill-running  { background: #1e3a5f; color: #60a5fa; }
  .pill-fallback { background: #451a03; color: #fbbf24; }
  .pill-online   { background: #14532d; color: #4ade80; }
  .pill-offline  { background: #450a0a; color: #f87171; }
  table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
  th { text-align: left; padding: 8px 10px; color: #64748b; border-bottom: 1px solid #1e293b; font-weight: 600; font-size: 0.72rem; text-transform: uppercase; }
  td { padding: 9px 10px; border-bottom: 1px solid #1e293b; vertical-align: middle; }
  tr:hover td { background: #1e293b44; }
  .section-title { font-size: 0.85rem; font-weight: 600; color: #94a3b8; margin: 20px 0 10px; text-transform: uppercase; letter-spacing: .05em; }
  .refresh-bar { font-size: 0.75rem; color: #475569; margin-bottom: 16px; }
  #countdown { color: #60a5fa; }
  .error-text { color: #f87171; font-size: 0.75rem; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .alive-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; margin-right: 6px; }
  .alive-dot.on  { background: #4ade80; box-shadow: 0 0 8px #4ade80; }
  .alive-dot.off { background: #f87171; }
  .worker-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; margin-bottom: 24px; }
  .worker-card { background: #1e293b; border-radius: 10px; padding: 16px; border-left: 3px solid #4ade80; }
  .worker-card.offline { border-left-color: #f87171; }
  .worker-name { font-size: 0.85rem; font-weight: 600; margin-bottom: 10px; color: #e2e8f0; word-break: break-all; }
  .worker-meta { display: flex; gap: 16px; margin-bottom: 10px; flex-wrap: wrap; }
  .worker-meta-item { font-size: 0.75rem; color: #64748b; }
  .worker-meta-item span { color: #94a3b8; font-weight: 600; }
  .worker-pids { font-size: 0.72rem; color: #475569; margin-bottom: 8px; }
  .active-tasks { margin-top: 8px; }
  .active-tasks-title { font-size: 0.72rem; color: #64748b; text-transform: uppercase; margin-bottom: 4px; }
  .task-item { background: #0f172a; border-radius: 6px; padding: 6px 8px; margin-bottom: 4px; font-size: 0.75rem; }
  .task-name { color: #60a5fa; }
  .task-id { color: #475569; font-size: 0.68rem; }
  .no-tasks { font-size: 0.75rem; color: #475569; font-style: italic; }
  .no-workers { color: #475569; font-size: 0.82rem; font-style: italic; padding: 12px 0; }
  .vt-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin-bottom: 16px; }
  .vt-card { background: #1e293b; border-radius: 8px; padding: 14px; border-left: 3px solid #818cf8; }
  .vt-card .label { font-size: 0.72rem; color: #64748b; text-transform: uppercase; margin-bottom: 4px; }
  .vt-card .value { font-size: 1.5rem; font-weight: 700; color: #818cf8; }
  .vt-card .sub { font-size: 0.72rem; color: #64748b; margin-top: 2px; }
  .pill-viral    { background: #2e1065; color: #a78bfa; }
  .pill-flagged  { background: #450a0a; color: #fca5a5; }
  .pill-active   { background: #14532d; color: #4ade80; }
  .pill-completed { background: #1e293b; color: #64748b; }
  .pill-belum    { background: #1e293b; color: #64748b; }
  .pill-error-scrape { background: #431407; color: #fb923c; }
  .pill-ok-scrape    { background: #14532d; color: #4ade80; }
  tr.vt-belum td { border-left: 2px solid #334155; }
  tr.vt-error  td:first-child { border-left: 2px solid #f97316 !important; }
  tr.vt-ok     td:first-child { border-left: 2px solid #4ade80 !important; }
  tr.vt-error { background: rgba(249,115,22,0.04); }
  tr.vt-ok    { background: rgba(74,222,128,0.04); }
  .kt-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin-bottom: 16px; }
  .kt-card { background: #1e293b; border-radius: 8px; padding: 14px; border-left: 3px solid #34d399; }
  .kt-card .label { font-size: 0.72rem; color: #64748b; text-transform: uppercase; margin-bottom: 4px; }
  .kt-card .value { font-size: 1.5rem; font-weight: 700; color: #34d399; }
  .pill-keyword  { background: #064e3b; color: #34d399; }
  .pill-done     { background: #1e293b; color: #64748b; }
  .progress-bar  { background: #1e293b; border-radius: 99px; height: 6px; width: 100%; margin-top: 4px; }
  .progress-fill { background: #34d399; border-radius: 99px; height: 6px; }
  .retry-bar { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
  .retry-btn { background: #1d4ed8; border: none; color: #fff; padding: 6px 16px; border-radius: 6px; cursor: pointer; font-size: 0.8rem; font-weight: 600; transition: background 0.15s; }
  .retry-btn:hover { background: #1e40af; }
  .retry-btn:disabled { opacity: 0.5; cursor: default; }
  .retry-msg { font-size: 0.78rem; color: #60a5fa; }
  .legend { display: flex; gap: 14px; align-items: center; font-size: 0.75rem; color: #64748b; margin-left: auto; }
  .legend-item { display: flex; align-items: center; gap: 4px; }
  .legend-dot { width: 8px; height: 8px; border-radius: 50%; }
  .pagination { display: flex; align-items: center; gap: 10px; margin-top: 10px; justify-content: flex-end; }
  .page-btn { background: #1e293b; border: 1px solid #334155; color: #94a3b8; padding: 5px 14px; border-radius: 6px; cursor: pointer; font-size: 0.8rem; transition: background 0.15s; }
  .page-btn:hover:not([disabled]) { background: #334155; color: #e2e8f0; }
  .page-btn[disabled] { opacity: 0.35; cursor: default; }
  .page-info { font-size: 0.8rem; color: #64748b; }
  .page-total { font-size: 0.75rem; color: #475569; margin-right: auto; }
  /* EnsembleData banner */
  .ed-banner { padding: 10px 16px; border-radius: 8px; margin-bottom: 20px; display: flex; align-items: center; gap: 12px; font-size: 0.85rem; flex-wrap: wrap; }
  .ed-banner.expired { background: rgba(248,113,113,0.08); border: 1px solid #450a0a; }
  .ed-banner.active  { background: rgba(74,222,128,0.08);  border: 1px solid #14532d; }
  .ed-banner.unknown { background: #1e293b; border: 1px solid #334155; }
  .ed-banner-title { font-weight: 700; }
  /* Instagram section */
  .ig-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin-bottom: 16px; }
  .ig-card { background: #1e293b; border-radius: 8px; padding: 14px; border-left: 3px solid #e879f9; }
  .ig-card .label { font-size: 0.72rem; color: #64748b; text-transform: uppercase; margin-bottom: 4px; }
  .ig-card .value { font-size: 1.5rem; font-weight: 700; color: #e879f9; }
  .ig-card .sub { font-size: 0.72rem; color: #64748b; margin-top: 2px; }
  .pill-ig-rank { background: #3b0764; color: #e879f9; }
  .pill-waiting { background: #451a03; color: #fbbf24; }
  /* Pipeline flow — alur live Subsistem A (AI Discovery) -> Subsistem B (Scrape Worker),
     melacak batch topik nyata dari viral_discovery_trace, bukan status independen. */
  .pipeline-flow { display: flex; align-items: flex-start; gap: 0; margin-bottom: 6px; overflow-x: auto; padding: 12px 4px 4px; }
  .pf-node { display: flex; flex-direction: column; align-items: center; gap: 6px; min-width: 108px; flex-shrink: 0; }
  .pf-icon { width: 42px; height: 42px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
             font-size: 18px; font-weight: 700; border: 2px solid #334155; background: #1e293b; color: #475569; }
  .pf-icon.success { border-color: #4ade80; color: #4ade80; background: rgba(74,222,128,0.10); }
  .pf-icon.failed  { border-color: #f87171; color: #f87171; background: rgba(248,113,113,0.10); }
  .pf-icon.running { border-color: #60a5fa; color: #60a5fa; background: rgba(96,165,250,0.10); }
  .pf-icon.waiting { border-color: #fbbf24; color: #fbbf24; background: rgba(251,191,36,0.10); }
  .pf-icon.idle    { border-color: #334155; color: #475569; }
  .pf-label { font-size: 0.74rem; color: #cbd5e1; text-align: center; font-weight: 600; }
  .pf-sub   { font-size: 0.68rem; color: #64748b; text-align: center; max-width: 112px; }
  .pf-status-text { font-size: 0.66rem; text-align: center; font-weight: 600; text-transform: uppercase; letter-spacing: .03em; }
  .pf-status-text.success { color: #4ade80; }
  .pf-status-text.failed  { color: #f87171; }
  .pf-status-text.running { color: #60a5fa; }
  .pf-status-text.waiting { color: #fbbf24; }
  .pf-status-text.idle    { color: #475569; }
  .pf-connector { flex: 1; min-width: 26px; height: 3px; margin-top: 20px; border-radius: 2px;
                  background: repeating-linear-gradient(90deg, #334155 0 6px, transparent 6px 12px); }
  .pf-connector.flowing { background: repeating-linear-gradient(90deg, #4ade80 0 6px, transparent 6px 12px);
                           background-size: 12px 3px; animation: pf-flow 0.7s linear infinite; }
  .pf-connector.blocked { background: repeating-linear-gradient(90deg, #f87171 0 6px, transparent 6px 12px); }
  @keyframes pf-flow { from { background-position: 0 0; } to { background-position: -12px 0; } }
  /* Indikator "sedang berjalan sekarang" — trigger baru (frontend/manual/jadwal) */
  .live-dot { width: 9px; height: 9px; border-radius: 50%; background: #60a5fa; display: inline-block;
              margin-right: 6px; animation: live-pulse 1.4s ease-in-out infinite; }
  @keyframes live-pulse { 0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(96,165,250,0.5); }
                          50% { opacity: 0.6; box-shadow: 0 0 0 5px rgba(96,165,250,0); } }
  .pf-legend { display: flex; gap: 16px; flex-wrap: wrap; font-size: 0.7rem; color: #64748b; margin-bottom: 14px; }
  .pf-legend-item { display: flex; align-items: center; gap: 5px; }
  .pf-legend-dot { width: 10px; height: 10px; border-radius: 50%; border: 2px solid; }
  .pf-batch-table { font-size: 0.78rem; }
  .pf-empty-hint { color: #475569; font-style: italic; font-size: 0.8rem; padding: 10px 0 18px; }
</style>
</head>
<body>
<h1>Scraping Monitor</h1>
<div class="subtitle">Social Intelligence Platform &mdash; Auto-refresh tiap 15 detik</div>
<div class="refresh-bar">Terakhir update: <span id="last-update">-</span> &nbsp;|&nbsp; Refresh dalam <span id="countdown">15</span>s</div>

<div id="ed-banner" class="ed-banner unknown">
  <span class="alive-dot off" id="ed-dot"></span>
  <div>
    <span class="ed-banner-title">EnsembleData API &mdash;</span>
    <span id="ed-status-text" style="margin-left:4px">Memuat...</span>
    <span id="ed-status-detail" style="color:#64748b;font-size:0.8rem;margin-left:8px"></span>
  </div>
  <div style="margin-left:auto;text-align:right;font-size:0.75rem;color:#475569">
    <div id="ed-last-err">-</div>
    <div id="ed-last-ok" style="color:#4ade80"></div>
  </div>
</div>

<div class="grid">
  <div class="card"><div class="label">Status</div><div class="value" id="worker-status">-</div></div>
  <div class="card"><div class="label">Sedang Jalan</div><div class="value blue" id="running">-</div></div>
  <div class="card"><div class="label">Total Posts</div><div class="value" id="total-posts">-</div></div>
  <div class="card"><div class="label">Total Komentar</div><div class="value" id="total-comments">-</div></div>
  <div class="card"><div class="label">Keywords</div><div class="value" id="total-keywords">-</div></div>
  <div class="card"><div class="label">Run 24 Jam</div><div class="value green" id="runs-24h">-</div><div class="sub" id="videos-24h">-</div></div>
</div>

<div class="section-title">Celery Workers</div>
<div class="worker-grid" id="workers-grid"><div class="no-workers">Memuat...</div></div>

<div class="section-title">Viral Tracking</div>
<div class="vt-grid">
  <div class="vt-card"><div class="label">Tracker Aktif</div><div class="value" id="vt-active">-</div><div class="sub">channel dipantau</div></div>
  <div class="vt-card"><div class="label">Tracker Selesai</div><div class="value" id="vt-completed">-</div><div class="sub">7 hari berakhir</div></div>
  <div class="vt-card"><div class="label">Post Terkumpul</div><div class="value" id="vt-posts">-</div><div class="sub">via tracking</div></div>
  <div class="vt-card"><div class="label">Akun Diflag</div><div class="value" id="vt-flagged">-</div><div class="sub">komentar &gt;10x</div></div>
</div>
<div class="retry-bar">
  <button class="retry-btn" id="retry-btn" onclick="retryFailed()">&#9654; Retry Semua Gagal</button>
  <span class="retry-msg" id="retry-msg"></span>
  <div class="legend">
    <div class="legend-item"><div class="legend-dot" style="background:#64748b"></div>Belum scrape</div>
    <div class="legend-item"><div class="legend-dot" style="background:#f97316"></div>Error</div>
    <div class="legend-item"><div class="legend-dot" style="background:#4ade80"></div>Sukses</div>
  </div>
</div>
<table>
  <thead>
    <tr>
      <th>Channel</th>
      <th>Tipe</th>
      <th>Status</th>
      <th>Hasil Scrape</th>
      <th>Post</th>
      <th>Tgl Scrape</th>
      <th>Log Terakhir</th>
    </tr>
  </thead>
  <tbody id="vt-table"></tbody>
</table>
<div class="pagination" id="vt-pagination"></div>

<div class="section-title" style="margin-top:24px">Keyword Tracking (7 Hari)</div>
<div class="kt-grid">
  <div class="kt-card"><div class="label">Tracking Aktif</div><div class="value" id="kt-active">-</div></div>
  <div class="kt-card"><div class="label">Selesai (Done)</div><div class="value" id="kt-done">-</div></div>
  <div class="kt-card"><div class="label">Post Terkumpul</div><div class="value" id="kt-posts">-</div></div>
</div>
<div class="retry-bar">
  <button class="retry-btn" id="kt-retry-btn" onclick="retryAllKeywordTrackers()">&#9654; Retry Semua Keyword</button>
  <span class="retry-msg" id="kt-retry-msg"></span>
  <div class="legend">
    <div class="legend-item"><div class="legend-dot" style="background:#64748b"></div>Belum scrape</div>
    <div class="legend-item"><div class="legend-dot" style="background:#f97316"></div>Error</div>
    <div class="legend-item"><div class="legend-dot" style="background:#4ade80"></div>Sukses</div>
  </div>
</div>
<table>
  <thead>
    <tr>
      <th>Keyword Pencarian</th>
      <th>Status</th>
      <th>Hari ke-</th>
      <th>Progress</th>
      <th>Post</th>
      <th>Tgl Scrape</th>
      <th>Mulai</th>
      <th>Berakhir</th>
      <th>Log Terakhir</th>
      <th>Aksi</th>
    </tr>
  </thead>
  <tbody id="kt-table"></tbody>
</table>

<div class="section-title" style="margin-top:24px">Instagram Trending</div>
<div class="ig-grid">
  <div class="ig-card"><div class="label">Total Posts</div><div class="value" id="ig-posts">-</div><div class="sub">platform instagram</div></div>
  <div class="ig-card"><div class="label">Total Komentar</div><div class="value" id="ig-comments">-</div><div class="sub">platform instagram</div></div>
  <div class="ig-card"><div class="label">Akun Trending</div><div class="value" id="ig-accounts">-</div><div class="sub">terdaftar aktif</div></div>
  <div class="ig-card"><div class="label">Scrape Hari Ini</div><div class="value" id="ig-today">-</div><div class="sub">akun di-scrape</div></div>
  <div class="ig-card"><div class="label">Last Discovery</div><div class="value" style="font-size:0.95rem;margin-top:4px" id="ig-discovery">-</div><div class="sub">via EnsembleData</div></div>
  <div class="ig-card"><div class="label">Jadwal</div><div class="value" style="font-size:0.85rem;margin-top:4px">09:00</div><div class="sub">WIB daily (Beat)</div></div>
</div>
<table>
  <thead>
    <tr>
      <th>Rank</th>
      <th>Username</th>
      <th>Followers</th>
      <th>Trending Score</th>
      <th>Engagement</th>
      <th>Posts di DB</th>
      <th>Last Scrape</th>
      <th>Discovered Via</th>
      <th>Status Log</th>
    </tr>
  </thead>
  <tbody id="ig-table"></tbody>
</table>

<div class="section-title" style="margin-top:24px">Alur Pipeline Live — Subsistem A (AI Discovery) &rarr; Subsistem B (Scrape Worker)</div>
<div class="pf-legend">
  <div class="pf-legend-item"><span class="pf-legend-dot" style="border-color:#4ade80;background:rgba(74,222,128,0.15)"></span> Sukses</div>
  <div class="pf-legend-item"><span class="pf-legend-dot" style="border-color:#f87171;background:rgba(248,113,113,0.15)"></span> Gagal / Berhenti Di Sini</div>
  <div class="pf-legend-item"><span class="pf-legend-dot" style="border-color:#fbbf24;background:rgba(251,191,36,0.15)"></span> Menunggu</div>
  <div class="pf-legend-item"><span class="pf-legend-dot" style="border-color:#334155"></span> Belum Ada Data</div>
</div>
<div class="pipeline-flow" id="pipeline-flow"></div>
<table class="pf-batch-table" id="pf-batch-wrap" style="display:none">
  <thead>
    <tr>
      <th>Topik (batch AI terakhir)</th>
      <th>Status Sekarang</th>
      <th>Discrape Via</th>
      <th>Durasi</th>
      <th>Waktu Scrape</th>
      <th>Error</th>
    </tr>
  </thead>
  <tbody id="pf-batch-table"></tbody>
</table>
<div class="pf-empty-hint" id="pf-empty-hint" style="display:none">Belum ada run AI Viral Discovery tercatat — jalan otomatis jam 07:00 WIB, atau trigger manual untuk test.</div>

<div class="section-title" style="margin-top:24px">Sedang Berjalan Sekarang</div>
<table>
  <thead>
    <tr>
      <th>Platform</th>
      <th>Topik / Akun</th>
      <th>Dipicu Oleh</th>
      <th>Sumber</th>
      <th>Sudah Berjalan</th>
    </tr>
  </thead>
  <tbody id="its-running-table"></tbody>
</table>

<div class="section-title" style="margin-top:24px">Instagram Trend-Scrape (trend_recommendations)</div>
<div class="ig-grid">
  <div class="ig-card"><div class="label">Pending</div><div class="value" id="its-pending">-</div><div class="sub">nunggu giliran scrape</div></div>
  <div class="ig-card"><div class="label">Sudah Discrape</div><div class="value" id="its-used">-</div><div class="sub">status=used</div></div>
  <div class="ig-card"><div class="label">Dari AI Search (lama)</div><div class="value" id="its-ai-pending">-</div><div class="sub">riwayat keyword miss</div></div>
  <div class="ig-card"><div class="label">Dari AI Viral Discovery</div><div class="value" id="its-ai-viral-pending">-</div><div class="sub">sapuan harian 07:00 WIB</div></div>
  <div class="ig-card"><div class="label">Budget Harian</div><div class="value" id="its-budget">-</div><div class="sub">topik/hari (Apify)</div></div>
</div>
<table>
  <thead>
    <tr>
      <th>Topik</th>
      <th>Score</th>
      <th>Akun IG</th>
      <th>Sumber</th>
      <th>Dibuat</th>
    </tr>
  </thead>
  <tbody id="its-pending-table"></tbody>
</table>

<div class="section-title" style="margin-top:24px">Riwayat Scrape Instagram (trend_recommendations)</div>
<table>
  <thead>
    <tr>
      <th>Topik</th>
      <th>Status</th>
      <th>Sumber</th>
      <th>Post Baru</th>
      <th>Durasi</th>
      <th>Waktu Mulai</th>
      <th>Error</th>
    </tr>
  </thead>
  <tbody id="its-runs-table"></tbody>
</table>

<div class="section-title" style="margin-top:24px">Facebook Trend-Scrape (trend_recommendations)</div>
<div class="ig-grid">
  <div class="ig-card"><div class="label">Pending</div><div class="value" id="fts-pending">-</div><div class="sub">nunggu giliran scrape</div></div>
  <div class="ig-card"><div class="label">Sudah Discrape</div><div class="value" id="fts-used">-</div><div class="sub">status=used</div></div>
  <div class="ig-card"><div class="label">Budget Harian</div><div class="value" id="fts-budget">-</div><div class="sub">topik/hari (Apify)</div></div>
  <div class="ig-card"><div class="label">Jadwal</div><div class="value" id="fts-schedule" style="font-size:1rem">-</div><div class="sub">Celery Beat</div></div>
</div>
<table>
  <thead>
    <tr>
      <th>Topik</th>
      <th>Score</th>
      <th>Akun FB</th>
      <th>Sumber</th>
      <th>Dibuat</th>
    </tr>
  </thead>
  <tbody id="fts-pending-table"></tbody>
</table>

<div class="section-title" style="margin-top:24px">Riwayat Scrape Facebook (trend_recommendations)</div>
<table>
  <thead>
    <tr>
      <th>Topik</th>
      <th>Status</th>
      <th>Sumber</th>
      <th>Post Baru</th>
      <th>Durasi</th>
      <th>Waktu Mulai</th>
      <th>Error</th>
    </tr>
  </thead>
  <tbody id="fts-runs-table"></tbody>
</table>

<div class="section-title" style="margin-top:24px">Riwayat Scraping Keyword</div>
<table>
  <thead>
    <tr>
      <th>Keyword</th>
      <th>Status</th>
      <th>Trigger</th>
      <th>API</th>
      <th>Video</th>
      <th>Baru</th>
      <th>Komentar</th>
      <th>Durasi</th>
      <th>Waktu Mulai</th>
      <th>Error</th>
    </tr>
  </thead>
  <tbody id="runs-table"></tbody>
</table>
<div class="pagination" id="runs-pagination"></div>

<script>
let countdown = 15;
let vtPage = 1;
let runsPage = 1;
const PAGE_LIMIT = 20;

function statusPill(s) {
  const map = { success: 'pill-success', failed: 'pill-failed', running: 'pill-running', fallback: 'pill-fallback' };
  return `<span class="pill ${map[s] || ''}">${s}</span>`;
}

function fmt(dt) {
  if (!dt) return '-';
  const d = new Date(dt);
  return d.toLocaleString('id-ID', { timeZone: 'Asia/Jakarta', hour12: false,
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function pipelineIcon(status) {
  return { success: '✓', failed: '✕', running: '↻', waiting: '⏳', idle: '○' }[status] || '○';
}

// Melacak batch topik NYATA dari run AI Discovery terakhir ke status scrape
// masing-masing (viral_discovery_trace dari backend) — bukan status
// independen tiap subsistem, supaya kelihatan persis di mana alurnya berhenti.
function renderPipelineFlow(trace) {
  const flowEl = document.getElementById('pipeline-flow');
  const batchWrap = document.getElementById('pf-batch-wrap');
  const batchTbody = document.getElementById('pf-batch-table');
  const emptyHint = document.getElementById('pf-empty-hint');
  if (!flowEl) return;

  const aiRun = trace && trace.ai_run;
  const topics = (trace && trace.topics) || [];

  if (!aiRun) {
    flowEl.innerHTML = '';
    batchWrap.style.display = 'none';
    emptyHint.style.display = 'block';
    return;
  }
  emptyHint.style.display = 'none';

  const s1 = aiRun.status === 'success' ? 'success' : 'failed';
  const s2 = aiRun.status === 'success' && topics.length > 0 ? 'success'
           : aiRun.status === 'failed' ? 'failed' : 'idle';

  const pendingCount = topics.filter(t => t.current_status === 'pending').length;
  const usedCount = topics.filter(t => t.current_status === 'used').length;
  const s3 = topics.length === 0 ? 'idle' : (pendingCount > 0 ? 'waiting' : 'success');

  const attempted = topics.filter(t => t.scrape_attempt);
  const anyFailed = attempted.some(t => t.scrape_attempt.status === 'failed');
  const anySuccess = attempted.some(t => t.scrape_attempt.status === 'success');
  let s4;
  if (attempted.length === 0) s4 = topics.length > 0 ? 'waiting' : 'idle';
  else if (anyFailed) s4 = 'failed';   // ada yang gagal -> tandai perlu perhatian
  else if (anySuccess) s4 = 'success';
  else s4 = 'waiting';

  const s5 = topics.length === 0 ? 'idle' : (usedCount === topics.length ? 'success' : (usedCount > 0 ? 'waiting' : 'idle'));

  const nodes = [
    { label: 'AI Discovery',    sub: 'Claude web_search',      status: s1 },
    { label: 'Submit ke DB',    sub: `${topics.length} topik ditemukan`, status: s2 },
    { label: 'Antrian Pending', sub: `${pendingCount} nunggu`, status: s3 },
    { label: 'Scrape Worker',   sub: 'Apify/EnsembleData',      status: s4 },
    { label: 'Selesai',         sub: `${usedCount}/${topics.length} used`, status: s5 },
  ];

  flowEl.innerHTML = nodes.map((n, i) => {
    const node = `<div class="pf-node">
      <div class="pf-icon ${n.status}">${pipelineIcon(n.status)}</div>
      <div class="pf-label">${n.label}</div>
      <div class="pf-sub">${n.sub}</div>
      <div class="pf-status-text ${n.status}">${n.status}</div>
    </div>`;
    if (i === nodes.length - 1) return node;
    const connClass = n.status === 'failed' ? 'blocked' : ((n.status === 'success' || n.status === 'waiting') ? 'flowing' : '');
    return node + `<div class="pf-connector ${connClass}"></div>`;
  }).join('');

  if (topics.length === 0) {
    batchWrap.style.display = 'none';
  } else {
    batchWrap.style.display = '';
    batchTbody.innerHTML = topics.map(t => {
      const sa = t.scrape_attempt;
      const statusPillClass = t.current_status === 'used' ? 'pill-success' : 'pill-waiting';
      return `<tr>
        <td>${t.topic}</td>
        <td><span class="pill ${statusPillClass}">${t.current_status}</span></td>
        <td style="color:#94a3b8;font-size:.75rem">${sa ? (sa.api_source || '-') : '-'}</td>
        <td style="color:#94a3b8">${sa && sa.duration_seconds != null ? sa.duration_seconds + 's' : '-'}</td>
        <td style="color:#94a3b8;font-size:.75rem">${sa ? fmt(sa.started_at) : '-'}</td>
        <td class="error-text" title="${(sa && sa.error_message) || ''}">${(sa && sa.error_message) || '-'}</td>
      </tr>`;
    }).join('');
  }
}

function fmtTaskName(name) {
  if (!name) return '-';
  const parts = name.split('.');
  return parts[parts.length - 1];
}

function renderPagination(containerId, page, totalPages, total, navFn) {
  const el = document.getElementById(containerId);
  if (!el) return;
  if (totalPages <= 1) { el.innerHTML = ''; return; }
  el.innerHTML = `
    <span class="page-total">${total} data</span>
    <button class="page-btn" ${page <= 1 ? 'disabled' : ''} onclick="${navFn}(${page - 1})">&#8592; Prev</button>
    <span class="page-info">Hal ${page} / ${totalPages}</span>
    <button class="page-btn" ${page >= totalPages ? 'disabled' : ''} onclick="${navFn}(${page + 1})">Next &#8594;</button>
  `;
}

function navVT(p) { vtPage = p; load(); }
function navRuns(p) { runsPage = p; load(); }

async function retryFailed() {
  const btn = document.getElementById('retry-btn');
  const msg = document.getElementById('retry-msg');
  btn.disabled = true;
  msg.textContent = 'Mengirim...';
  try {
    const base = window.location.origin;
    const r = await fetch(base + '/api/v1/youtube/viral-tracking/retry-failed', {
      method: 'POST',
    });
    const json = await r.json();
    if (r.ok) {
      const n = json.data?.retried ?? 0;
      msg.style.color = '#4ade80';
      msg.textContent = n > 0 ? `${n} tracker di-queue. Refresh dalam 2-5 menit.` : 'Tidak ada tracker gagal.';
      setTimeout(load, 3000);
    } else {
      msg.style.color = '#f87171';
      msg.textContent = json.detail || 'Error. Pastikan sudah login di Swagger dulu.';
    }
  } catch(e) {
    msg.style.color = '#f87171';
    msg.textContent = 'Gagal terhubung ke server.';
  }
  setTimeout(() => { btn.disabled = false; msg.textContent = ''; msg.style.color = '#60a5fa'; }, 8000);
}

async function retryAllKeywordTrackers() {
  const btn = document.getElementById('kt-retry-btn');
  const msg = document.getElementById('kt-retry-msg');
  btn.disabled = true;
  msg.textContent = 'Mengirim...';
  msg.style.color = '#60a5fa';
  try {
    const r = await fetch(window.location.origin + '/api/v1/youtube/keyword-tracking/retry-all', { method: 'POST' });
    const json = await r.json();
    if (r.ok) {
      const n = json.data?.retried ?? 0;
      msg.style.color = '#4ade80';
      msg.textContent = n > 0 ? `${n} tracker di-queue. Refresh dalam 2-5 menit.` : 'Tidak ada tracker yang perlu di-retry.';
      if (n > 0) setTimeout(load, 4000);
    } else {
      msg.style.color = '#f87171';
      msg.textContent = json.detail || 'Error server.';
    }
  } catch(e) {
    msg.style.color = '#f87171';
    msg.textContent = 'Gagal terhubung ke server.';
  }
  setTimeout(() => { btn.disabled = false; msg.textContent = ''; }, 8000);
}

async function runKeywordTracker(trackerId, btnEl) {
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = '…'; }
  try {
    const r = await fetch(window.location.origin + '/api/v1/youtube/keyword-tracking/' + trackerId + '/run', { method: 'POST' });
    const json = await r.json();
    if (r.ok) {
      const sq = json.data?.search_query || trackerId.substring(0,8);
      const msg = document.getElementById('kt-retry-msg');
      msg.style.color = '#4ade80';
      msg.textContent = `"${sq}" di-queue. Refresh dalam 2-5 menit.`;
      setTimeout(() => { msg.textContent = ''; }, 8000);
      setTimeout(load, 4000);
    } else {
      alert(json.detail || 'Gagal trigger tracker.');
    }
  } catch(e) {
    alert('Gagal terhubung ke server.');
  }
  if (btnEl) { btnEl.disabled = false; btnEl.textContent = '▶'; }
}

function renderWorkers(workers) {
  const grid = document.getElementById('workers-grid');
  if (!workers || workers.length === 0) {
    grid.innerHTML = '<div class="no-workers">Tidak ada worker online</div>';
    return;
  }
  grid.innerHTML = workers.map(w => {
    const isOnline = w.status === 'online';
    const activeTasks = w.active_tasks || [];
    const pids = (w.processes || []).join(', ');
    return `<div class="worker-card ${isOnline ? '' : 'offline'}">
      <div class="worker-name">
        <span class="alive-dot ${isOnline ? 'on' : 'off'}"></span>${w.name}
      </div>
      <div class="worker-meta">
        <div class="worker-meta-item">Status: <span>${isOnline ? 'Online' : 'Offline'}</span></div>
        <div class="worker-meta-item">Concurrency: <span>${w.concurrency ?? '-'}</span></div>
        <div class="worker-meta-item">Task aktif: <span class="${activeTasks.length > 0 ? 'green' : ''}">${activeTasks.length}</span></div>
      </div>
      ${pids ? `<div class="worker-pids">PID: ${pids}</div>` : ''}
      <div class="active-tasks">
        <div class="active-tasks-title">Task Berjalan</div>
        ${activeTasks.length === 0
          ? '<div class="no-tasks">Idle — tidak ada task</div>'
          : activeTasks.map(t => `<div class="task-item">
              <div class="task-name">${fmtTaskName(t.name)}</div>
              <div class="task-id">ID: ${(t.id || '').substring(0, 16)}…</div>
            </div>`).join('')
        }
      </div>
    </div>`;
  }).join('');
}

async function load() {
  try {
    const base = window.location.origin;
    const url = `${base}/api/v1/youtube/monitor-public?vt_page=${vtPage}&vt_limit=${PAGE_LIMIT}&runs_page=${runsPage}&runs_limit=${PAGE_LIMIT}`;
    const r = await fetch(url);
    const json = await r.json();
    const d = json.data;

    document.getElementById('worker-status').innerHTML =
      `<span class="alive-dot ${d.worker_alive ? 'on' : 'off'}"></span>${d.worker_alive ? 'AKTIF' : 'MATI'}`;
    document.getElementById('running').textContent = d.currently_running;
    document.getElementById('total-posts').textContent = (d.totals?.posts || 0).toLocaleString();
    document.getElementById('total-comments').textContent = (d.totals?.comments || 0).toLocaleString();
    document.getElementById('total-keywords').textContent = (d.totals?.keywords || 0).toLocaleString();

    const s = d.last_24h?.success;
    const total24 = (s?.total || 0) + (d.last_24h?.failed?.total || 0) + (d.last_24h?.fallback?.total || 0);
    document.getElementById('runs-24h').textContent = total24;
    document.getElementById('videos-24h').textContent = s ? `${s.videos_new} video baru` : '-';

    renderWorkers(d.workers);

    // Viral tracking section
    const vt = d.viral_tracking || {};
    document.getElementById('vt-active').textContent    = vt.active_trackers ?? '-';
    document.getElementById('vt-completed').textContent = vt.completed_trackers ?? '-';
    document.getElementById('vt-posts').textContent     = vt.posts_in_db ?? '-';
    document.getElementById('vt-flagged').textContent   = vt.flagged_accounts ?? '-';

    const vtTbody = document.getElementById('vt-table');
    const vtRows = vt.recent_activity || [];
    if (vtRows.length === 0) {
      vtTbody.innerHTML = '<tr><td colspan="7" style="color:#475569;font-style:italic;padding:12px">Belum ada data tracker</td></tr>';
    } else {
      vtTbody.innerHTML = vtRows.map(r => {
        const ll = r.last_log || {};
        const hasLog = ll.posts_new !== undefined || ll.error !== undefined;
        const kondisi = !hasLog ? 'belum' : (ll.error ? 'error' : 'ok');

        const hasilPill = kondisi === 'belum'
          ? '<span class="pill pill-belum">Belum Scrape</span>'
          : kondisi === 'error'
            ? '<span class="pill pill-error-scrape">&#9888; Error</span>'
            : '<span class="pill pill-ok-scrape">&#10003; Sukses</span>';

        const logText = kondisi === 'error'
          ? `<span class="red" title="${ll.error || ''}">${(ll.error || '').substring(0,50)}${(ll.error||'').length>50?'...':''}</span>`
          : kondisi === 'ok'
            ? `<span class="green">+${ll.posts_new} baru</span>&nbsp;<span style="color:#475569">(skip:${ll.posts_skipped ?? 0})</span>`
            : '<span style="color:#475569">-</span>';

        const typePill = r.tracker_type === 'viral'
          ? '<span class="pill pill-viral">viral</span>'
          : '<span class="pill pill-flagged">flagged</span>';
        const statusPillVt = r.status === 'active'
          ? '<span class="pill pill-active">active</span>'
          : '<span class="pill pill-completed">completed</span>';

        return `<tr class="vt-${kondisi}">
          <td><b>${r.channel_name || '-'}</b><br><span style="color:#475569;font-size:.7rem">${r.tracker_id.substring(0,8)}...</span></td>
          <td>${typePill}</td>
          <td>${statusPillVt}</td>
          <td>${hasilPill}</td>
          <td class="${r.posts_collected > 0 ? 'green' : ''}">${r.posts_collected ?? 0}</td>
          <td style="color:#94a3b8;font-size:.75rem">${r.last_scraped_date || '-'}</td>
          <td style="font-size:.75rem">${logText}</td>
        </tr>`;
      }).join('');
    }

    const vtPag = vt.pagination || {};
    renderPagination('vt-pagination', vtPage, vtPag.total_pages || 1, vtPag.total || 0, 'navVT');

    // Keyword tracking section
    const kt = d.keyword_tracking || {};
    document.getElementById('kt-active').textContent = kt.active_trackers ?? '-';
    document.getElementById('kt-done').textContent   = kt.completed_trackers ?? '-';
    document.getElementById('kt-posts').textContent  = kt.posts_collected ?? '-';

    const ktTbody = document.getElementById('kt-table');
    const ktRows = kt.recent_activity || [];
    const edExpired = (d.ensemble_data || {}).status === 'expired';
    const todayStr = new Date().toISOString().split('T')[0]; // YYYY-MM-DD

    if (ktRows.length === 0) {
      ktTbody.innerHTML = '<tr><td colspan="10" style="color:#475569;font-style:italic;padding:12px">Belum ada keyword tracker — lakukan POST /videos/viral dengan parameter q= untuk memulai</td></tr>';
    } else {
      ktTbody.innerHTML = ktRows.map(r => {
        const daysDone = r.days_done || 0;
        const pct = Math.min(100, Math.round(daysDone / 7 * 100));
        const ll = r.last_log || {};
        const hasLog = ll.posts_new !== undefined || ll.error !== undefined;
        const is493 = (ll.error || '').includes('493') || (ll.error || '').toLowerCase().includes('subscription');
        const scrapedToday = r.last_scraped_date === todayStr;
        const kondisi = !hasLog ? 'belum' : (ll.error ? 'error' : 'ok');

        const statusPill = r.status === 'active'
          ? '<span class="pill pill-active">active</span>'
          : '<span class="pill pill-done">done</span>';

        // ── LOG TERAKHIR: cerdas tergantung kondisi ──
        let logText;
        if (kondisi === 'error' && is493) {
          // Error karena EnsembleData expired
          logText = `<span class="yellow">⏳ EnsembleData expired</span><br>
                     <span style="color:#475569;font-size:.7rem">Keyword search tidak ada fallback — menunggu renewal</span>`;
        } else if (kondisi === 'error') {
          logText = `<span class="red" title="${ll.error||''}">${(ll.error||'').substring(0,50)}${(ll.error||'').length>50?'...':''}</span>`;
        } else if (kondisi === 'ok' && !scrapedToday && edExpired && r.status === 'active') {
          // Sukses kemarin, tapi hari ini belum jalan karena API expired
          logText = `<span class="green">+${ll.posts_new} baru</span>
                     <span style="color:#475569;font-size:.68rem">(${ll.date||''})</span><br>
                     <span class="yellow" style="font-size:.72rem">⏳ Hari ini belum scrape — EnsembleData expired, tunggu 12:00 WIB besok</span>`;
        } else if (kondisi === 'ok' && !scrapedToday && r.status === 'active') {
          // Sukses sebelumnya, hari ini belum jalan (bukan karena API)
          logText = `<span class="green">+${ll.posts_new} baru</span>
                     <span style="color:#475569;font-size:.68rem">(${ll.date||''})</span><br>
                     <span style="color:#64748b;font-size:.72rem">🕐 Menunggu jadwal 12:00 WIB</span>`;
        } else if (kondisi === 'ok') {
          logText = `<span class="green">+${ll.posts_new} baru</span>
                     <span style="color:#475569;font-size:.68rem">(${ll.date||''})</span>`;
        } else {
          logText = edExpired
            ? `<span class="yellow">⏳ Menunggu EnsembleData aktif</span>`
            : '<span style="color:#475569">Belum pernah scrape</span>';
        }

        // ── TGL SCRAPE: tandai merah jika expired dan belum scrape hari ini ──
        const tglColor = (!scrapedToday && edExpired && r.status === 'active') ? '#fbbf24' : '#94a3b8';
        const tglScrape = r.last_scraped_date
          ? `<span style="color:${tglColor};font-size:.75rem">${r.last_scraped_date}</span>`
          : '<span style="color:#475569">-</span>';

        const canRun = r.status === 'active';
        const runBtn = canRun
          ? `<button class="retry-btn" style="padding:2px 8px;font-size:.7rem" onclick="runKeywordTracker('${r.tracker_id}', this)">&#9654;</button>`
          : `<span style="color:#475569;font-size:.7rem">selesai</span>`;

        return `<tr class="vt-${kondisi}">
          <td><span class="pill pill-keyword">#</span> <b>${r.search_query}</b><br>
              <span style="color:#475569;font-size:.7rem">${r.tracker_id.substring(0,8)}…</span></td>
          <td>${statusPill}</td>
          <td style="color:#94a3b8">${daysDone} / 7</td>
          <td><div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
              <span style="font-size:.7rem;color:#64748b">${pct}%</span></td>
          <td class="${r.posts_collected > 0 ? 'green' : ''}">${r.posts_collected ?? 0}</td>
          <td>${tglScrape}</td>
          <td style="color:#475569;font-size:.7rem">${fmt(r.started_at)}</td>
          <td style="color:#475569;font-size:.7rem">${fmt(r.ends_at)}</td>
          <td style="font-size:.75rem;line-height:1.5">${logText}</td>
          <td style="text-align:center">${runBtn}</td>
        </tr>`;
      }).join('');
    }

    // Keyword scraping runs
    const tbody = document.getElementById('runs-table');
    tbody.innerHTML = (d.runs || []).map(r => `<tr>
      <td><b>${r.keyword || '-'}</b></td>
      <td>${statusPill(r.status)}</td>
      <td style="color:#94a3b8">${r.triggered_by || '-'}</td>
      <td style="color:#94a3b8;font-size:.75rem">${r.api_source || '-'}</td>
      <td>${r.videos_fetched ?? '-'}</td>
      <td class="green">${r.videos_new ?? '-'}</td>
      <td>${r.comments_new ?? '-'}</td>
      <td style="color:#94a3b8">${r.duration_sec ? r.duration_sec + 's' : '-'}</td>
      <td style="color:#94a3b8;font-size:.75rem">${fmt(r.started_at)}</td>
      <td class="error-text" title="${r.error || ''}">${r.error || ''}</td>
    </tr>`).join('');

    const runsPag = d.runs_pagination || {};
    renderPagination('runs-pagination', runsPage, runsPag.total_pages || 1, runsPag.total || 0, 'navRuns');

    // ── EnsembleData status banner ─────────────────────────────────────────
    const ed = d.ensemble_data || {};
    const banner = document.getElementById('ed-banner');
    const edDot  = document.getElementById('ed-dot');
    const edText = document.getElementById('ed-status-text');
    const edDetail = document.getElementById('ed-status-detail');
    const edLastErr = document.getElementById('ed-last-err');
    const edLastOk  = document.getElementById('ed-last-ok');
    banner.className = `ed-banner ${ed.status || 'unknown'}`;
    if (ed.status === 'active') {
      edDot.className = 'alive-dot on';
      edText.innerHTML = '<span class="green">AKTIF</span>';
      edDetail.textContent = ed.message || '';
    } else if (ed.status === 'expired') {
      edDot.className = 'alive-dot off';
      edText.innerHTML = '<span class="red">EXPIRED / MENUNGGU RENEWAL</span>';
      edDetail.textContent = 'Semua scraping YouTube & Instagram ditahan sampai subscription diperbarui';
    } else {
      edDot.className = 'alive-dot off';
      edText.innerHTML = '<span style="color:#64748b">UNKNOWN</span>';
      edDetail.textContent = ed.message || 'Belum ada data scraping';
    }
    edLastErr.textContent = ed.last_error_at  ? `Error terakhir: ${fmt(ed.last_error_at)}` : '';
    edLastOk.textContent  = ed.last_success_at ? `Sukses terakhir: ${fmt(ed.last_success_at)}` : '';

    // ── Instagram trending section ─────────────────────────────────────────
    const ig = d.instagram || {};
    const igt = ig.trending || {};
    document.getElementById('ig-posts').textContent    = (ig.total_posts    || 0).toLocaleString();
    document.getElementById('ig-comments').textContent = (ig.total_comments || 0).toLocaleString();
    document.getElementById('ig-accounts').textContent = (igt.total_accounts || 0).toLocaleString();
    document.getElementById('ig-today').textContent    = (ig.accounts_scraped_today || 0).toLocaleString();
    document.getElementById('ig-discovery').textContent = igt.last_discovery
      ? fmt(igt.last_discovery + 'T00:00:00') : (ed.status === 'expired' ? '⏳ Menunggu' : '-');

    const igTbody = document.getElementById('ig-table');
    const igAccounts = igt.accounts || [];
    if (igAccounts.length === 0) {
      igTbody.innerHTML = '<tr><td colspan="9" style="color:#475569;font-style:italic;padding:12px">Belum ada akun trending — discovery berjalan jam 09:00 WIB saat EnsembleData aktif</td></tr>';
    } else {
      igTbody.innerHTML = igAccounts.map(acc => {
        const ll = acc.last_scrape_log || {};
        const hasErr = ll.errors && ll.errors.length > 0;
        const logText = hasErr
          ? `<span class="red" title="${ll.errors[0]||''}">${(ll.errors[0]||'').substring(0,40)}…</span>`
          : (ll.posts_new !== undefined
              ? `<span class="green">+${ll.posts_new} post</span>`
              : '<span style="color:#475569">-</span>');
        const waiting = ed.status === 'expired'
          ? '<span class="pill pill-waiting">⏳ waiting</span>' : '';
        return `<tr>
          <td><span class="pill pill-ig-rank">#${acc.rank ?? '-'}</span></td>
          <td><b>@${acc.username}</b>${waiting}</td>
          <td style="color:#94a3b8">${(acc.followers||0).toLocaleString()}</td>
          <td class="${(acc.trending_score||0)>5?'green':'yellow'}">${(acc.trending_score||0).toFixed(2)}</td>
          <td style="color:#94a3b8">${(acc.engagement_rate||0).toFixed(2)}%</td>
          <td class="${(acc.posts_collected||0)>0?'green':''}">${acc.posts_collected ?? 0}</td>
          <td style="color:#94a3b8;font-size:.75rem">${acc.last_scraped || '-'}</td>
          <td style="color:#475569;font-size:.75rem">${acc.discovered_via || '-'}</td>
          <td style="font-size:.75rem">${logText}</td>
        </tr>`;
      }).join('');
    }

    // ── Instagram trend-scrape (trend_recommendations + AI keyword search) ──
    const its = d.instagram_trend_scrape || {};
    const itsSummary = its.summary || {};
    renderPipelineFlow(its.viral_discovery_trace);
    document.getElementById('its-pending').textContent    = itsSummary.pending_with_instagram_account || 0;
    document.getElementById('its-used').textContent       = itsSummary.used_with_instagram_account || 0;
    document.getElementById('its-ai-pending').textContent = itsSummary.ai_keyword_search_pending || 0;
    document.getElementById('its-ai-viral-pending').textContent = itsSummary.ai_viral_discovery_pending || 0;
    document.getElementById('its-budget').textContent     = its.daily_budget ?? '-';

    const itsPendingTbody = document.getElementById('its-pending-table');
    const itsPending = its.pending_topics || [];
    if (itsPending.length === 0) {
      itsPendingTbody.innerHTML = '<tr><td colspan="5" style="color:#475569;font-style:italic;padding:12px">Tidak ada topik pending</td></tr>';
    } else {
      itsPendingTbody.innerHTML = itsPending.map(t => `<tr>
        <td>${t.topic}</td>
        <td>${(t.score||0).toFixed(2)}</td>
        <td>@${t.instagram_username || '-'}</td>
        <td>${t.is_ai_keyword_search ? '<span class="pill pill-waiting">AI Search</span>' : '<span style="color:#64748b;font-size:.72rem">manual</span>'}</td>
        <td style="color:#94a3b8;font-size:.75rem">${fmt(t.created_at)}</td>
      </tr>`).join('');
    }

    // ── Facebook trend-scrape (trend_recommendations) ────────────────────────
    const fts = d.facebook_trend_scrape || {};
    const ftsSummary = fts.summary || {};
    document.getElementById('fts-pending').textContent  = ftsSummary.pending_with_facebook_account || 0;
    document.getElementById('fts-used').textContent     = ftsSummary.used_with_facebook_account || 0;
    document.getElementById('fts-budget').textContent   = fts.daily_budget ?? '-';
    document.getElementById('fts-schedule').textContent = fts.schedule ?? '-';

    const ftsPendingTbody = document.getElementById('fts-pending-table');
    const ftsPending = fts.pending_topics || [];
    if (ftsPending.length === 0) {
      ftsPendingTbody.innerHTML = '<tr><td colspan="5" style="color:#475569;font-style:italic;padding:12px">Tidak ada topik pending</td></tr>';
    } else {
      ftsPendingTbody.innerHTML = ftsPending.map(t => `<tr>
        <td>${t.topic}</td>
        <td>${(t.score||0).toFixed(2)}</td>
        <td>@${t.facebook_identifier || '-'}</td>
        <td style="color:#64748b;font-size:.72rem">${t.source || '-'}</td>
        <td style="color:#94a3b8;font-size:.75rem">${fmt(t.created_at)}</td>
      </tr>`).join('');
    }

    const ftsRunsTbody = document.getElementById('fts-runs-table');
    const ftsRuns = fts.recent_runs || [];
    if (ftsRuns.length === 0) {
      ftsRunsTbody.innerHTML = '<tr><td colspan="7" style="color:#475569;font-style:italic;padding:12px">Belum ada riwayat scrape</td></tr>';
    } else {
      ftsRunsTbody.innerHTML = ftsRuns.map(r => {
        const pillClass = r.status === 'success' ? 'pill-success' : (r.status === 'failed' ? 'pill-failed' : 'pill-running');
        return `<tr>
          <td>${r.topic}</td>
          <td><span class="pill ${pillClass}">${r.status}</span></td>
          <td style="color:#64748b;font-size:.72rem">${r.api_source || '-'}</td>
          <td class="${(r.videos_new||0)>0?'green':''}">${r.videos_new ?? 0}</td>
          <td style="color:#94a3b8">${r.duration_seconds ?? '-'}s</td>
          <td style="color:#94a3b8;font-size:.75rem">${fmt(r.started_at)}</td>
          <td class="error-text" title="${r.error_message||''}">${r.error_message || '-'}</td>
        </tr>`;
      }).join('');
    }

    // ── Sedang Berjalan Sekarang — gabungan Instagram + Facebook ─────────────
    const itsRunningTbody = document.getElementById('its-running-table');
    const triggerLabel = { manual_api: 'Manual (Frontend/API)', manual_cli: 'Manual (CLI)', celery_beat: 'Otomatis (Jadwal)' };
    const allRunning = [
      ...(its.running_now || []).map(r => ({ ...r, platform: 'Instagram' })),
      ...(fts.running_now || []).map(r => ({ ...r, platform: 'Facebook' })),
    ];
    if (allRunning.length === 0) {
      itsRunningTbody.innerHTML = '<tr><td colspan="5" style="color:#475569;font-style:italic;padding:12px">Tidak ada scraping yang sedang berjalan</td></tr>';
    } else {
      itsRunningTbody.innerHTML = allRunning.map(r => {
        const secs = r.elapsed_seconds || 0;
        const elapsed = secs < 60 ? `${secs.toFixed(0)}d` : `${Math.floor(secs/60)}m ${(secs%60).toFixed(0)}d`;
        return `<tr>
          <td><span class="pill ${r.platform === 'Facebook' ? 'pill-waiting' : 'pill-success'}">${r.platform}</span></td>
          <td><span class="live-dot"></span>${r.topic}</td>
          <td>${triggerLabel[r.triggered_by] || r.triggered_by}</td>
          <td style="color:#64748b;font-size:.72rem">${r.api_source || '-'}</td>
          <td style="color:#60a5fa;font-weight:600">${elapsed}</td>
        </tr>`;
      }).join('');
    }

    const itsRunsTbody = document.getElementById('its-runs-table');
    const itsRuns = its.recent_runs || [];
    if (itsRuns.length === 0) {
      itsRunsTbody.innerHTML = '<tr><td colspan="7" style="color:#475569;font-style:italic;padding:12px">Belum ada riwayat scrape</td></tr>';
    } else {
      itsRunsTbody.innerHTML = itsRuns.map(r => {
        const pillClass = r.status === 'success' ? 'pill-success' : (r.status === 'failed' ? 'pill-failed' : 'pill-running');
        const sourcePill = r.api_source === 'anthropic_web_search'
          ? '<span class="pill pill-waiting">AI Discovery</span>'
          : `<span style="color:#64748b;font-size:.72rem">${r.api_source || '-'}</span>`;
        return `<tr>
          <td>${r.topic}</td>
          <td><span class="pill ${pillClass}">${r.status}</span></td>
          <td>${sourcePill}</td>
          <td class="${(r.videos_new||0)>0?'green':''}">${r.videos_new ?? 0}</td>
          <td style="color:#94a3b8">${r.duration_seconds ?? '-'}s</td>
          <td style="color:#94a3b8;font-size:.75rem">${fmt(r.started_at)}</td>
          <td class="error-text" title="${r.error_message||''}">${r.error_message || '-'}</td>
        </tr>`;
      }).join('');
    }

    document.getElementById('last-update').textContent = new Date().toLocaleTimeString('id-ID');
    countdown = 15;
  } catch(e) {
    console.error(e);
  }
}

load();
setInterval(load, 15000);
setInterval(() => {
  countdown--;
  if (countdown < 0) countdown = 15;
  document.getElementById('countdown').textContent = countdown;
}, 1000);
</script>
</body>
</html>"""
    return HTMLResponse(content=html)


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
app.include_router(topic_search.router, prefix=API_PREFIX)
app.include_router(metrics.router, prefix=API_PREFIX)
app.include_router(agents.router, prefix=API_PREFIX)
app.include_router(reports.router, prefix=API_PREFIX)
app.include_router(youtube_router, prefix=API_PREFIX)
app.include_router(instagram_router, prefix=API_PREFIX)
app.include_router(facebook_router, prefix=API_PREFIX)
app.include_router(trend_recommendations.router, prefix=API_PREFIX)
