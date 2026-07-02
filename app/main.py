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
</style>
</head>
<body>
<h1>Scraping Monitor</h1>
<div class="subtitle">Social Intelligence Platform &mdash; Auto-refresh tiap 15 detik</div>
<div class="refresh-bar">Terakhir update: <span id="last-update">-</span> &nbsp;|&nbsp; Refresh dalam <span id="countdown">15</span>s</div>

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
<table>
  <thead>
    <tr>
      <th>Channel</th>
      <th>Tipe</th>
      <th>Status Tracker</th>
      <th>Post Terkumpul</th>
      <th>Terakhir Scrape</th>
      <th>Log Terakhir</th>
    </tr>
  </thead>
  <tbody id="vt-table"></tbody>
</table>

<div class="section-title" style="margin-top:24px">Riwayat Scraping Keyword (50 terbaru)</div>
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

<script>
let countdown = 15;

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

function fmtTaskName(name) {
  if (!name) return '-';
  const parts = name.split('.');
  return parts[parts.length - 1];
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
    const r = await fetch(base + '/api/v1/youtube/monitor-public');
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
      vtTbody.innerHTML = '<tr><td colspan="6" style="color:#475569;font-style:italic;padding:12px">Belum ada aktivitas scraping tracker</td></tr>';
    } else {
      vtTbody.innerHTML = vtRows.map(r => {
        const ll = r.last_log || {};
        const logText = ll.error
          ? `<span class="red">Error: ${ll.error.substring(0,40)}...</span>`
          : ll.posts_new !== undefined
            ? `<span class="green">+${ll.posts_new} post</span> (skip: ${ll.posts_skipped ?? 0})`
            : '-';
        const typePill = r.tracker_type === 'viral'
          ? '<span class="pill pill-viral">viral</span>'
          : '<span class="pill pill-flagged">flagged</span>';
        const statusPillVt = r.status === 'active'
          ? '<span class="pill pill-active">active</span>'
          : '<span class="pill pill-completed">completed</span>';
        return `<tr>
          <td><b>${r.channel_name || '-'}</b><br><span style="color:#475569;font-size:.7rem">${r.tracker_id.substring(0,8)}...</span></td>
          <td>${typePill}</td>
          <td>${statusPillVt}</td>
          <td class="${r.posts_collected > 0 ? 'green' : ''}">${r.posts_collected ?? 0}</td>
          <td style="color:#94a3b8;font-size:.75rem">${r.last_scraped_date || '-'}</td>
          <td style="font-size:.75rem">${logText}</td>
        </tr>`;
      }).join('');
    }

    // Keyword scraping runs
    const tbody = document.getElementById('runs-table');
    tbody.innerHTML = d.runs.map(r => `<tr>
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
