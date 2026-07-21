from contextlib import asynccontextmanager
import json

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse


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
    agent_registry,
    agents,
    apify_pool,
    auth,
    collectors,
    credentials,
    ensembledata_pool,
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
    users,
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
import app.domain.agent_registry.models  # noqa: F401
import app.domain.youtube_discovery.models  # noqa: F401
import app.domain.youtube_video_metadata.models  # noqa: F401

from app.api.v1.youtube.router import router as youtube_router
from app.api.v1.instagram.router import router as instagram_router
from app.api.v1.facebook.router import router as facebook_router
from app.api.v1.tiktok.router import router as tiktok_router
from app.api.v1.twitter.router import router as twitter_router
from app.api.v1.news.router import router as news_router
from app.api.v1.threads.router import router as threads_router
from app.api.v1.trend_discovery.router import router as trend_discovery_router
from app.infrastructure.database.connection import engine
from app.infrastructure.logging.logger import get_logger, setup_logging
from app.infrastructure.middleware.request_id import RequestIDMiddleware
from app.infrastructure.redis.connection import close_redis, get_redis
from app.shared.config import settings
from app.shared.exceptions import AppException
from app.shared.utils import build_error_response, build_success_response

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


@app.get("/api/v1/system/ollama-status", tags=["monitor"])
async def get_ollama_status():
    """Status Ollama LEBIH RINCI drpd /health (yg cuma OK/error) -- model apa
    yg SEDANG ter-load di memori + kapan expire (via /api/ps, pola sama dgn
    `ollama ps`). Dipakai banner /scraping-status (permintaan user 2026-07-18:
    "apakah ollama sudah ada di dashboard riwayatnya" -- belum ada sebelumnya,
    cuma numpang di /health TANPA detail apa2)."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            tags_resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            ps_resp = await client.get(f"{settings.ollama_base_url}/api/ps")
        alive = tags_resp.status_code == 200
        loaded = ps_resp.json().get("models", []) if ps_resp.status_code == 200 else []
        return build_success_response({
            "alive": alive,
            "loaded_models": [
                {
                    "name": m.get("name"),
                    "size_mb": round((m.get("size") or 0) / 1024 / 1024, 1),
                    "expires_at": m.get("expires_at"),
                }
                for m in loaded
            ],
        })
    except Exception as exc:
        return build_success_response({"alive": False, "error": str(exc)[:300], "loaded_models": []})


@app.get("/manage-api-keys", response_class=HTMLResponse, include_in_schema=False)
async def manage_api_keys_page():
    """Halaman TERPUSAT kelola SEMUA API key/credential third-party
    (permintaan user 2026-07-18) -- sebelumnya tersebar di banyak tab agent
    berbeda. Admin-only (butuh token Bearer, lihat GET/PATCH /api/v1/credentials).

    2026-07-20: Pool Token Apify DIGABUNG ke halaman ini sbg kategori
    tambahan (bukan halaman terpisah /apify-pool lagi) -- permintaan user
    setelah /apify-pool terasa ribet (bearer + token Apify dua kolom
    terpisah, dua halaman beda): "gabungkan saja tpi perkategori, jgn bikin
    susah lagi". SATU kolom Bearer dipakai kedua bagian (kategori credential
    + pool Apify). Tombol Hapus token Apify SEKARANG by index (klik
    langsung, TIDAK perlu tempel ulang token lengkap -- lihat
    app/services/apify_pool/config.py::remove_token_at_index())."""
    html = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kelola API Key</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; padding: 24px; }
  h1 { font-size: 1.5rem; font-weight: 700; margin-bottom: 4px; }
  .subtitle { color: #64748b; font-size: 0.85rem; margin-bottom: 20px; }
  .token-box { font-size: 0.78rem; color: #94a3b8; margin-bottom: 20px; padding: 12px 14px; background: #1e293b; border-radius: 8px; max-width: 640px; line-height: 1.5; }
  .token-box input { width: 100%; margin-top: 8px; padding: 8px 10px; background: #0f172a; border: 1px solid #334155; border-radius: 6px; color: #e2e8f0; font-size: 0.82rem; }
  .category-group { margin-bottom: 28px; }
  .category-title { font-size: 0.78rem; font-weight: 700; color: #60a5fa; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 10px; }
  .cred-card { background: #1e293b; border-radius: 10px; padding: 16px 18px; margin-bottom: 10px; display: flex; flex-wrap: wrap; align-items: center; gap: 14px; }
  .cred-info { flex: 1 1 260px; min-width: 220px; }
  .cred-label { font-size: 0.9rem; font-weight: 600; margin-bottom: 3px; }
  .cred-used-by { font-size: 0.75rem; color: #94a3b8; margin-bottom: 6px; }
  .cred-value { font-size: 0.78rem; font-family: monospace; }
  .cred-value.set { color: #4ade80; }
  .cred-value.unset { color: #64748b; font-style: italic; }
  .cred-live { font-size: 0.68rem; color: #475569; margin-top: 4px; }
  .cred-edit { flex: 1 1 260px; display: flex; gap: 8px; min-width: 240px; }
  .cred-edit input { flex: 1; padding: 8px 10px; background: #0f172a; border: 1px solid #334155; border-radius: 6px; color: #e2e8f0; font-size: 0.82rem; }
  .cred-btn { background: #1d4ed8; color: #fff; border: none; padding: 8px 14px; border-radius: 6px; font-size: 0.8rem; font-weight: 600; cursor: pointer; white-space: nowrap; }
  .cred-btn:hover { background: #2563eb; }
  .cred-msg { font-size: 0.75rem; margin-left: 4px; }
  .green { color: #4ade80; }
  .red { color: #f87171; }
  #auth-gate { padding: 40px 0; text-align: center; color: #64748b; }
  /* Pool Apify (kategori tambahan, digabung 2026-07-20) */
  .note-box { font-size: 0.78rem; color: #fbbf24; background: rgba(251,191,36,0.08); border: 1px solid #451a03; border-radius: 8px; padding: 10px 14px; margin-bottom: 14px; }
  .toolbar { display: flex; gap: 10px; align-items: center; margin-bottom: 14px; flex-wrap: wrap; }
  .add-form { display: flex; gap: 8px; flex: 1 1 380px; min-width: 280px; }
  .add-form input { flex: 1; padding: 8px 10px; background: #0f172a; border: 1px solid #334155; border-radius: 6px; color: #e2e8f0; font-size: 0.82rem; }
  .btn-secondary { background: #334155; color: #e2e8f0; border: none; padding: 8px 14px; border-radius: 6px; font-size: 0.8rem; font-weight: 600; cursor: pointer; white-space: nowrap; }
  .btn-secondary:hover { background: #475569; }
  .btn-danger { background: #7f1d1d; color: #fca5a5; border: none; padding: 6px 12px; border-radius: 6px; font-size: 0.78rem; font-weight: 600; cursor: pointer; }
  .btn-danger:hover { background: #991b1b; }
  .pool-size { font-size: 0.8rem; color: #64748b; }
  .token-card { background: #1e293b; border-radius: 10px; padding: 14px 16px; margin-bottom: 8px; display: flex; flex-wrap: wrap; align-items: center; gap: 14px; border-left: 3px solid #334155; }
  .token-card.exhausted { border-left-color: #f87171; }
  .token-card.ok { border-left-color: #4ade80; }
  .token-info { flex: 1 1 180px; min-width: 160px; }
  .token-masked { font-family: monospace; font-size: 0.82rem; margin-bottom: 4px; }
  .token-status { font-size: 0.7rem; padding: 2px 8px; border-radius: 99px; font-weight: 600; display: inline-block; }
  .token-status.ok { background: #14532d; color: #4ade80; }
  .token-status.exhausted { background: #450a0a; color: #f87171; }
  .usage-block { flex: 2 1 240px; min-width: 220px; }
  .usage-top { display: flex; justify-content: space-between; font-size: 0.76rem; color: #94a3b8; margin-bottom: 5px; }
  .usage-top .amount { font-weight: 700; color: #e2e8f0; }
  .usage-bar { background: #0f172a; border-radius: 99px; height: 7px; width: 100%; overflow: hidden; }
  .usage-fill { height: 7px; border-radius: 99px; transition: width 0.3s; }
  .usage-fill.green { background: #4ade80; }
  .usage-fill.yellow { background: #fbbf24; }
  .usage-fill.red { background: #f87171; }
  .usage-cycle { font-size: 0.66rem; color: #475569; margin-top: 4px; }
  .usage-error { font-size: 0.74rem; color: #f87171; font-style: italic; }
  .empty-state { color: #475569; font-size: 0.85rem; font-style: italic; padding: 14px 0; }
</style>
</head>
<body>

<h1>Kelola API Key &amp; Credential</h1>
<div class="subtitle">Satu halaman utk lihat/ganti SEMUA API key third-party yg dipakai project ini -- perubahan langsung aktif, tanpa restart server.</div>

<div class="token-box">
  Butuh token login ADMIN (Bearer) -- tempel sekali, tersimpan di browser ini saja.
  <input type="password" id="ck-token" placeholder="Bearer token admin..." onchange="ckSaveToken()">
</div>

<div id="ck-content"><div id="auth-gate">Masukkan token admin di atas utk memuat daftar credential.</div></div>

<div class="category-group">
  <div class="category-title">Apify (Pool Rotasi)</div>
  <div id="ap-content"><div id="auth-gate-ap">Masukkan token admin di atas utk memuat pool.</div></div>
</div>

<div class="category-group">
  <div class="category-title">EnsembleData (Pool Rotasi)</div>
  <div id="ed-content"><div id="auth-gate-ed">Masukkan token admin di atas utk memuat pool.</div></div>
</div>

<script>
function ckToken() {
  return document.getElementById('ck-token').value || localStorage.getItem('ck_token') || '';
}
function ckSaveToken() {
  const t = document.getElementById('ck-token').value.trim();
  if (t) { localStorage.setItem('ck_token', t); ckLoad(); apLoad(); edLoad(); }
}
function ckAuthHeaders() {
  return { 'Authorization': 'Bearer ' + ckToken(), 'Content-Type': 'application/json' };
}
function ckEsc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

async function ckLoad() {
  const content = document.getElementById('ck-content');
  if (!ckToken()) { content.innerHTML = '<div id="auth-gate">Masukkan token admin di atas utk memuat daftar credential.</div>'; return; }
  content.innerHTML = '<div id="auth-gate">Memuat...</div>';
  try {
    const r = await fetch(window.location.origin + '/api/v1/credentials', { headers: ckAuthHeaders() });
    const json = await r.json();
    if (!r.ok) {
      content.innerHTML = `<div id="auth-gate" class="red">Gagal memuat (${r.status}): ${ckEsc(json.error?.message || json.detail || 'token invalid/bukan admin')}</div>`;
      return;
    }
    const items = json.data?.items || [];
    const byCategory = {};
    for (const it of items) {
      (byCategory[it.category] = byCategory[it.category] || []).push(it);
    }
    content.innerHTML = Object.entries(byCategory).map(([cat, entries]) => `
      <div class="category-group">
        <div class="category-title">${ckEsc(cat)}</div>
        ${entries.map(it => `
          <div class="cred-card">
            <div class="cred-info">
              <div class="cred-label">${ckEsc(it.label)}</div>
              <div class="cred-used-by">${ckEsc(it.used_by)}</div>
              <div class="cred-value ${it.is_set ? 'set' : 'unset'}">${it.is_set ? ckEsc(it.masked_value) : 'belum diisi'}</div>
              <div class="cred-live">${ckEsc(it.live_effect)}</div>
            </div>
            <div class="cred-edit">
              <input type="password" id="ck-input-${ckEsc(it.id)}" placeholder="Tempel key/token baru...">
              <button class="cred-btn" onclick="ckSave('${it.id}')">Simpan</button>
              <span class="cred-msg" id="ck-msg-${ckEsc(it.id)}"></span>
            </div>
          </div>
        `).join('')}
      </div>
    `).join('');
  } catch(e) {
    content.innerHTML = '<div id="auth-gate" class="red">Gagal terhubung ke server.</div>';
  }
}

async function ckSave(id) {
  const input = document.getElementById('ck-input-' + id);
  const msg = document.getElementById('ck-msg-' + id);
  const value = input.value.trim();
  if (!value) { msg.className = 'cred-msg red'; msg.textContent = 'Isi dulu.'; return; }
  try {
    const r = await fetch(window.location.origin + '/api/v1/credentials/' + encodeURIComponent(id), {
      method: 'PATCH', headers: ckAuthHeaders(), body: JSON.stringify({ value }),
    });
    const json = await r.json();
    if (r.ok) {
      msg.className = 'cred-msg green';
      msg.textContent = 'Tersimpan.';
      input.value = '';
      ckLoad();
    } else {
      msg.className = 'cred-msg red';
      msg.textContent = json.error?.message || json.detail || 'Gagal.';
    }
  } catch(e) { msg.className = 'cred-msg red'; msg.textContent = 'Gagal terhubung.'; }
}

// ── Pool Apify (kategori tambahan, digabung ke halaman ini 2026-07-20) ──
function apEsc(s) { return ckEsc(s); }
function apBarClass(pct) {
  if (pct === null || pct === undefined) return 'green';
  if (pct >= 85) return 'red';
  if (pct >= 60) return 'yellow';
  return 'green';
}

async function apLoad() {
  const content = document.getElementById('ap-content');
  if (!ckToken()) { content.innerHTML = '<div id="auth-gate-ap">Masukkan token admin di atas utk memuat pool.</div>'; return; }
  content.innerHTML = '<div id="auth-gate-ap">Memuat...</div>';
  try {
    const r = await fetch(window.location.origin + '/api/v1/apify-pool', { headers: ckAuthHeaders() });
    const json = await r.json();
    if (!r.ok) {
      content.innerHTML = `<div id="auth-gate-ap" class="red">Gagal memuat (${r.status}): ${apEsc(json.error?.message || json.detail || 'token invalid/bukan admin')}</div>`;
      return;
    }
    const data = json.data || {};
    const tokens = data.tokens || [];

    let html = `
      <div class="toolbar">
        <div class="add-form">
          <input type="password" id="ap-new-token" placeholder="Tempel token Apify baru...">
          <button class="cred-btn" onclick="apAdd()">Tambah</button>
        </div>
        <button class="btn-secondary" onclick="apReset()">Reset Status Habis</button>
        <span class="pool-size">${tokens.length} token di pool</span>
      </div>
      <div class="cred-msg" id="ap-msg"></div>
    `;

    if (data.note) {
      html += `<div class="note-box">${apEsc(data.note)}</div>`;
    }

    if (tokens.length === 0) {
      html += '<div class="empty-state">Pool masih kosong -- semua platform Apify pakai APIFY_API_TOKEN .env (satu token, tanpa rotasi). Tambahkan token pertama di atas kalau mau aktifkan rotasi.</div>';
    } else {
      html += tokens.map((t, idx) => {
        const usage = t.usage || {};
        const pct = usage.percent_used;
        const cardCls = t.exhausted_flag ? 'exhausted' : 'ok';
        const statusText = t.exhausted_flag ? 'HABIS (menunggu pulih)' : 'Aktif';
        let usageHtml;
        if (usage.checked) {
          usageHtml = `
            <div class="usage-top">
              <span>Pemakaian bulan ini</span>
              <span class="amount">$${usage.used_usd} / $${usage.limit_usd} (${usage.percent_used}%)</span>
            </div>
            <div class="usage-bar"><div class="usage-fill ${apBarClass(pct)}" style="width:${Math.min(pct ?? 0, 100)}%"></div></div>
            <div class="usage-cycle">Siklus: ${apEsc((usage.cycle_start||'').slice(0,10))} s/d ${apEsc((usage.cycle_end||'').slice(0,10))}</div>
          `;
        } else {
          usageHtml = `<div class="usage-error">Gagal cek pemakaian: ${apEsc(usage.message || 'tidak diketahui')}</div>`;
        }
        return `
          <div class="token-card ${cardCls}">
            <div class="token-info">
              <div class="token-masked">${apEsc(t.masked)}</div>
              <span class="token-status ${cardCls}">${statusText}</span>
            </div>
            <div class="usage-block">${usageHtml}</div>
            <button class="btn-danger" onclick="apRemove(${idx})">Hapus</button>
          </div>
        `;
      }).join('');
    }

    content.innerHTML = html;
  } catch(e) {
    content.innerHTML = '<div id="auth-gate-ap" class="red">Gagal terhubung ke server.</div>';
  }
}

async function apAdd() {
  const input = document.getElementById('ap-new-token');
  const msg = document.getElementById('ap-msg');
  const value = input.value.trim();
  if (!value) { msg.className = 'cred-msg red'; msg.textContent = 'Isi token dulu.'; return; }
  try {
    const r = await fetch(window.location.origin + '/api/v1/apify-pool', {
      method: 'POST', headers: ckAuthHeaders(), body: JSON.stringify({ token: value }),
    });
    const json = await r.json();
    if (r.ok) {
      msg.className = 'cred-msg green';
      msg.textContent = 'Token ditambahkan (pool: ' + json.data.pool_size + ').';
      input.value = '';
      apLoad();
    } else {
      msg.className = 'cred-msg red';
      msg.textContent = json.error?.message || json.detail || 'Gagal.';
    }
  } catch(e) { msg.className = 'cred-msg red'; msg.textContent = 'Gagal terhubung.'; }
}

async function apRemove(index) {
  // Hapus by POSISI (index) -- TIDAK perlu tempel ulang token lengkap,
  // API tidak pernah balikin nilai token asli (cuma masked, keamanan).
  const msg = document.getElementById('ap-msg');
  try {
    const r = await fetch(window.location.origin + '/api/v1/apify-pool/remove', {
      method: 'POST', headers: ckAuthHeaders(), body: JSON.stringify({ index }),
    });
    const json = await r.json();
    if (r.ok) {
      msg.className = 'cred-msg green';
      msg.textContent = 'Token dihapus (pool: ' + json.data.pool_size + ').';
      apLoad();
    } else {
      msg.className = 'cred-msg red';
      msg.textContent = json.error?.message || json.detail || 'Gagal.';
    }
  } catch(e) { msg.className = 'cred-msg red'; msg.textContent = 'Gagal terhubung.'; }
}

async function apReset() {
  const msg = document.getElementById('ap-msg');
  try {
    const r = await fetch(window.location.origin + '/api/v1/apify-pool/reset', {
      method: 'POST', headers: ckAuthHeaders(),
    });
    const json = await r.json();
    if (r.ok) {
      msg.className = 'cred-msg green';
      msg.textContent = 'Reset ' + json.data.reset_count + ' token yang sebelumnya ditandai habis.';
      apLoad();
    } else {
      msg.className = 'cred-msg red';
      msg.textContent = json.error?.message || json.detail || 'Gagal.';
    }
  } catch(e) { msg.className = 'cred-msg red'; msg.textContent = 'Gagal terhubung.'; }
}

// ── Pool EnsembleData (kategori tambahan, pola SAMA dgn Pool Apify di atas) ──
function edEsc(s) { return ckEsc(s); }

async function edLoad() {
  const content = document.getElementById('ed-content');
  if (!ckToken()) { content.innerHTML = '<div id="auth-gate-ed">Masukkan token admin di atas utk memuat pool.</div>'; return; }
  content.innerHTML = '<div id="auth-gate-ed">Memuat...</div>';
  try {
    const r = await fetch(window.location.origin + '/api/v1/ensembledata-pool', { headers: ckAuthHeaders() });
    const json = await r.json();
    if (!r.ok) {
      content.innerHTML = `<div id="auth-gate-ed" class="red">Gagal memuat (${r.status}): ${edEsc(json.error?.message || json.detail || 'token invalid/bukan admin')}</div>`;
      return;
    }
    const data = json.data || {};
    const tokens = data.tokens || [];

    let html = `
      <div class="toolbar">
        <div class="add-form">
          <input type="password" id="ed-new-token" placeholder="Tempel token EnsembleData baru...">
          <button class="cred-btn" onclick="edAdd()">Tambah</button>
        </div>
        <button class="btn-secondary" onclick="edReset()">Reset Status Habis</button>
        <span class="pool-size">${tokens.length} token di pool</span>
      </div>
      <div class="cred-msg" id="ed-msg"></div>
    `;

    if (data.note) {
      html += `<div class="note-box">${edEsc(data.note)}</div>`;
    }

    if (tokens.length === 0) {
      html += '<div class="empty-state">Pool masih kosong -- pakai ENSEMBLE_DATA_API_TOKEN .env (satu token, tanpa rotasi). Tambahkan token pertama di atas kalau mau aktifkan rotasi.</div>';
    } else {
      html += tokens.map((t, idx) => {
        const cardCls = t.exhausted ? 'exhausted' : 'ok';
        const statusText = t.exhausted ? 'HABIS (menunggu pulih)' : 'Aktif';
        return `
          <div class="token-card ${cardCls}">
            <div class="token-info">
              <div class="token-masked">${edEsc(t.masked)}</div>
              <span class="token-status ${cardCls}">${statusText}</span>
            </div>
            <button class="btn-danger" onclick="edRemove(${idx})">Hapus</button>
          </div>
        `;
      }).join('');
    }

    content.innerHTML = html;
  } catch(e) {
    content.innerHTML = '<div id="auth-gate-ed" class="red">Gagal terhubung ke server.</div>';
  }
}

async function edAdd() {
  const input = document.getElementById('ed-new-token');
  const msg = document.getElementById('ed-msg');
  const value = input.value.trim();
  if (!value) { msg.className = 'cred-msg red'; msg.textContent = 'Isi token dulu.'; return; }
  try {
    const r = await fetch(window.location.origin + '/api/v1/ensembledata-pool', {
      method: 'POST', headers: ckAuthHeaders(), body: JSON.stringify({ token: value }),
    });
    const json = await r.json();
    if (r.ok) {
      msg.className = 'cred-msg green';
      msg.textContent = 'Token ditambahkan (pool: ' + json.data.pool_size + ').';
      input.value = '';
      edLoad();
    } else {
      msg.className = 'cred-msg red';
      msg.textContent = json.error?.message || json.detail || 'Gagal.';
    }
  } catch(e) { msg.className = 'cred-msg red'; msg.textContent = 'Gagal terhubung.'; }
}

async function edRemove(index) {
  const msg = document.getElementById('ed-msg');
  try {
    const r = await fetch(window.location.origin + '/api/v1/ensembledata-pool/remove', {
      method: 'POST', headers: ckAuthHeaders(), body: JSON.stringify({ index }),
    });
    const json = await r.json();
    if (r.ok) {
      msg.className = 'cred-msg green';
      msg.textContent = 'Token dihapus (pool: ' + json.data.pool_size + ').';
      edLoad();
    } else {
      msg.className = 'cred-msg red';
      msg.textContent = json.error?.message || json.detail || 'Gagal.';
    }
  } catch(e) { msg.className = 'cred-msg red'; msg.textContent = 'Gagal terhubung.'; }
}

async function edReset() {
  const msg = document.getElementById('ed-msg');
  try {
    const r = await fetch(window.location.origin + '/api/v1/ensembledata-pool/reset', {
      method: 'POST', headers: ckAuthHeaders(),
    });
    const json = await r.json();
    if (r.ok) {
      msg.className = 'cred-msg green';
      msg.textContent = 'Reset ' + json.data.reset_count + ' token yang sebelumnya ditandai habis.';
      edLoad();
    } else {
      msg.className = 'cred-msg red';
      msg.textContent = json.error?.message || json.detail || 'Gagal.';
    }
  } catch(e) { msg.className = 'cred-msg red'; msg.textContent = 'Gagal terhubung.'; }
}

const savedToken = localStorage.getItem('ck_token');
if (savedToken) { document.getElementById('ck-token').value = savedToken; ckLoad(); apLoad(); edLoad(); }
</script>

</body>
</html>"""
    return HTMLResponse(content=html)


@app.get("/apify-pool", include_in_schema=False)
async def apify_pool_page_redirect():
    """Halaman lama, DIGABUNG ke /manage-api-keys 2026-07-20 (permintaan
    user: "gabungkan saja tpi perkategori, jgn bikin susah lagi") --
    redirect supaya link/bookmark lama tetap jalan."""
    return RedirectResponse(url="/manage-api-keys")


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
  .da-tabbar { display: flex; gap: 4px; margin-bottom: 4px; border-bottom: 1px solid #1e293b; }
  .da-tab-btn { background: none; border: none; color: #64748b; padding: 8px 16px; font-size: 0.82rem; font-weight: 600; cursor: pointer; border-bottom: 2px solid transparent; }
  .da-tab-btn.active { color: #60a5fa; border-bottom-color: #60a5fa; }
  .da-panel { padding-top: 4px; }
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

<div id="ollama-banner" class="ed-banner unknown">
  <span class="alive-dot off" id="ollama-dot"></span>
  <div>
    <span class="ed-banner-title">Ollama (model lokal, fallback AI Discovery) &mdash;</span>
    <span id="ollama-status-text" style="margin-left:4px">Memuat...</span>
  </div>
  <div style="margin-left:auto;text-align:right;font-size:0.75rem;color:#475569;max-width:320px">
    <div id="ollama-models">-</div>
  </div>
</div>

<div id="apify-banner" class="ed-banner unknown">
  <span class="alive-dot off" id="apify-dot"></span>
  <div>
    <span class="ed-banner-title">Kuota Apify (Facebook/Instagram/TikTok/Twitter/Smart Search) &mdash;</span>
    <span id="apify-status-text" style="margin-left:4px">Memuat...</span>
  </div>
  <div style="margin-left:auto;text-align:right;font-size:0.75rem;color:#475569;max-width:420px">
    <div id="apify-detail">-</div>
  </div>
</div>

<div id="ytq-banner" class="ed-banner unknown">
  <span class="alive-dot off" id="ytq-dot"></span>
  <div>
    <span class="ed-banner-title">Kuota YouTube Data API v3 (fallback EnsembleData) &mdash;</span>
    <span id="ytq-status-text" style="margin-left:4px">Memuat...</span>
    <span id="ytq-status-detail" style="color:#64748b;font-size:0.8rem;margin-left:8px"></span>
  </div>
  <div style="margin-left:auto;text-align:right;font-size:0.75rem;color:#475569">
    <div id="ytq-last-err">-</div>
    <div id="ytq-last-ok" style="color:#4ade80"></div>
  </div>
</div>

<div id="bf-banner" class="ed-banner unknown">
  <span class="alive-dot off" id="bf-dot"></span>
  <div>
    <span class="ed-banner-title">Backfill views/likes/comments YouTube lama &mdash;</span>
    <span id="bf-status-text" style="margin-left:4px">Memuat...</span>
    <span id="bf-status-detail" style="color:#64748b;font-size:0.8rem;margin-left:8px"></span>
  </div>
  <div style="margin-left:auto;text-align:right;font-size:0.75rem;color:#475569">
    <div id="bf-progress">-</div>
  </div>
</div>

<div class="section-title" style="margin-top:24px">Kelola Agent &mdash; katalog semua agent AI (key+model)</div>
<div style="font-size:0.72rem;color:#64748b;margin-bottom:12px;max-width:760px">
  Key yang sudah LINKED ke kredensial existing diedit lewat <a href="/manage-api-keys" style="color:#60a5fa">/manage-api-keys</a>
  atau tab Pengaturan agent masing-masing di bawah. Agent CUSTOM (baru dari form, belum tentu py kode scraping asli)
  bisa diedit langsung di sini.
</div>
<div style="margin-bottom:10px">
  <button class="retry-btn" onclick="agRegLoad()">Muat / Refresh Daftar Agent</button>
  <span id="agreg-msg" style="margin-left:10px;font-size:0.82rem;color:#64748b"></span>
</div>
<div id="agreg-list" style="margin-bottom:20px">
  <div style="color:#475569;font-style:italic;font-size:0.82rem">Klik "Muat / Refresh Daftar Agent" utk mulai (butuh token login Bearer, sama dgn tab Discovery Agent di bawah)</div>
</div>

<div style="max-width:560px;background:#1e293b;border-radius:8px;padding:16px;margin-bottom:24px">
  <div style="font-size:0.85rem;font-weight:600;margin-bottom:10px">+ Tambah Agent Baru</div>
  <div style="font-size:0.72rem;color:#94a3b8;margin-bottom:10px">
    Ini CUMA mencatat nama/key/model agent baru (jadi tercatat rapi) -- TIDAK otomatis membuat kode
    scraping baru. Agent baru genuinely aktif tetap butuh kode (pola 6-lapis: config-agent-worker-jadwal-endpoint-dashboard).
  </div>
  <input type="text" id="agreg-new-name" placeholder="Nama agent (mis. TikTok Discovery Agent)" style="width:100%;margin-bottom:8px;padding:7px 10px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:0.82rem">
  <input type="text" id="agreg-new-category" placeholder="Kategori (mis. TikTok)" style="width:100%;margin-bottom:8px;padding:7px 10px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:0.82rem">
  <input type="text" id="agreg-new-desc" placeholder="Deskripsi singkat (opsional)" style="width:100%;margin-bottom:8px;padding:7px 10px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:0.82rem">
  <input type="text" id="agreg-new-keylabel" placeholder="Label key (mis. OpenRouter)" style="width:100%;margin-bottom:8px;padding:7px 10px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:0.82rem">
  <input type="password" id="agreg-new-apikey" placeholder="API key (opsional)" style="width:100%;margin-bottom:8px;padding:7px 10px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:0.82rem">
  <input type="text" id="agreg-new-model" placeholder="Model (opsional, mis. meta-llama/llama-3.3-70b-instruct:free)" style="width:100%;margin-bottom:10px;padding:7px 10px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:0.82rem">
  <button class="retry-btn" onclick="agRegAddNew()">Tambah Agent</button>
  <span id="agreg-add-msg" style="margin-left:10px;font-size:0.82rem"></span>
</div>

<div class="section-title" style="margin-top:24px">YouTube Discovery Agent &mdash; pencarian viral/trending otomatis</div>
<div class="da-tabbar">
  <button class="da-tab-btn active" id="da-tab-btn-status" onclick="daSwitchTab('status')">Status</button>
  <button class="da-tab-btn" id="da-tab-btn-runs" onclick="daSwitchTab('runs')">Riwayat Run</button>
  <button class="da-tab-btn" id="da-tab-btn-config" onclick="daSwitchTab('config')">Pengaturan</button>
</div>

<div id="da-panel-status" class="da-panel">
  <div id="da-status-banner" class="ed-banner unknown">
    <span class="alive-dot off" id="da-dot"></span>
    <div>
      <span class="ed-banner-title">Status Agent &mdash;</span>
      <span id="da-status-text" style="margin-left:4px">Memuat...</span>
    </div>
    <div style="margin-left:auto;text-align:right;font-size:0.75rem;color:#475569">
      <div id="da-last-run-at">-</div>
    </div>
  </div>
  <div class="grid" style="margin-top:12px">
    <div class="card"><div class="label">Topik Dicek</div><div class="value" id="da-topics-checked">-</div></div>
    <div class="card"><div class="label">Kandidat Ditemukan</div><div class="value blue" id="da-candidates-found">-</div></div>
    <div class="card"><div class="label">Lolos Validasi</div><div class="value green" id="da-candidates-validated">-</div></div>
    <div class="card"><div class="label">Ditolak LLM</div><div class="value yellow" id="da-candidates-rejected">-</div></div>
    <div class="card"><div class="label">Post Tersimpan</div><div class="value green" id="da-posts-saved">-</div></div>
  </div>
  <div id="da-error-box" style="display:none;margin-top:12px;padding:10px 14px;background:#450a0a;border-radius:8px;color:#fca5a5;font-size:0.82rem"></div>

  <div style="margin-top:16px">
    <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:8px">Topik yang Dicakup (Mode Topic-guided) &mdash; daftar tetap, bukan progress run tertentu</div>
    <div id="da-topics-covered" style="font-size:0.82rem;color:#e2e8f0">Memuat...</div>
  </div>
</div>

<div id="da-panel-runs" class="da-panel" style="display:none">
  <table style="margin-top:12px">
    <thead><tr>
      <th>Mulai</th><th>Status</th><th>Topik</th><th>Ditemukan</th><th>Lolos</th><th>Ditolak</th><th>Tersimpan</th><th>Model</th><th></th>
    </tr></thead>
    <tbody id="da-runs-tbody"><tr><td colspan="9">Memuat...</td></tr></tbody>
  </table>
  <div id="da-runs-pagination" style="margin-top:10px"></div>
</div>

<div id="da-detail-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:1000;align-items:center;justify-content:center" onclick="if(event.target===this) daCloseDetails()">
  <div style="background:#1e293b;border-radius:10px;padding:20px;max-width:820px;max-height:82vh;overflow-y:auto;width:92%">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <h3 style="margin:0;font-size:1rem">Rincian Kandidat yang Discraping</h3>
      <button class="page-btn" onclick="daCloseDetails()">Tutup</button>
    </div>
    <div id="da-detail-modal-body"></div>
  </div>
</div>

<div id="da-panel-config" class="da-panel" style="display:none">
  <div style="max-width:520px;margin-top:12px">
    <div style="font-size:0.78rem;color:#94a3b8;margin-bottom:14px;padding:10px 12px;background:#1e293b;border-radius:8px;line-height:1.5">
      Lihat status/riwayat TIDAK perlu token. Mengubah pengaturan di bawah butuh token
      login (Bearer) &mdash; tempel sekali, tersimpan di browser ini saja.
      <input type="password" id="da-token" placeholder="Bearer token..." style="width:100%;margin-top:8px;padding:7px 10px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:0.8rem">
    </div>

    <div style="margin-bottom:18px">
      <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:8px">Interval Scheduler</div>
      <div id="da-interval-btns" style="display:flex;gap:8px"></div>
    </div>

    <div style="margin-bottom:14px">
      <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:6px">Model OpenRouter</div>
      <input type="text" id="da-model-input" placeholder="mis. deepseek/deepseek-chat-v3-0324:free" style="width:100%;padding:7px 10px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:0.82rem">
    </div>

    <div style="margin-bottom:14px">
      <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:6px">API Key OpenRouter <span id="da-key-current" style="color:#64748b"></span></div>
      <input type="password" id="da-apikey-input" placeholder="Kosongkan kalau tidak diubah" style="width:100%;padding:7px 10px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:0.82rem">
    </div>

    <button class="retry-btn" onclick="daSaveConfig()">Simpan Pengaturan</button>
    <span id="da-config-msg" style="margin-left:10px;font-size:0.82rem"></span>
  </div>
</div>

<div class="section-title" style="margin-top:24px">YouTube Discovery Agent 2 &mdash; agent TERPISAH, key YouTube+OpenRouter sendiri, HANYA topic-guided, tiap 1 jam</div>

<div id="da2q-banner" class="ed-banner unknown">
  <span class="alive-dot off" id="da2q-dot"></span>
  <div>
    <span class="ed-banner-title">Kuota YouTube Data API v3 (Agent 2, key terpisah) &mdash;</span>
    <span id="da2q-status-text" style="margin-left:4px">Memuat...</span>
    <span id="da2q-status-detail" style="color:#64748b;font-size:0.8rem;margin-left:8px"></span>
  </div>
  <div style="margin-left:auto;text-align:right;font-size:0.75rem;color:#475569">
    <div id="da2q-last-err">-</div>
    <div id="da2q-last-ok" style="color:#4ade80"></div>
  </div>
</div>

<div class="da-tabbar">
  <button class="da-tab-btn active" id="da2-tab-btn-status" onclick="da2SwitchTab('status')">Status</button>
  <button class="da-tab-btn" id="da2-tab-btn-runs" onclick="da2SwitchTab('runs')">Riwayat Run</button>
  <button class="da-tab-btn" id="da2-tab-btn-config" onclick="da2SwitchTab('config')">Pengaturan</button>
</div>

<div id="da2-panel-status" class="da-panel">
  <div id="da2-status-banner" class="ed-banner unknown">
    <span class="alive-dot off" id="da2-dot"></span>
    <div>
      <span class="ed-banner-title">Status Agent 2 &mdash;</span>
      <span id="da2-status-text" style="margin-left:4px">Memuat...</span>
    </div>
    <div style="margin-left:auto;text-align:right;font-size:0.75rem;color:#475569">
      <div id="da2-last-run-at">-</div>
    </div>
  </div>
  <div class="grid" style="margin-top:12px">
    <div class="card"><div class="label">Topik Dicek</div><div class="value" id="da2-topics-checked">-</div></div>
    <div class="card"><div class="label">Kandidat Ditemukan</div><div class="value blue" id="da2-candidates-found">-</div></div>
    <div class="card"><div class="label">Lolos Validasi</div><div class="value green" id="da2-candidates-validated">-</div></div>
    <div class="card"><div class="label">Ditolak LLM</div><div class="value yellow" id="da2-candidates-rejected">-</div></div>
    <div class="card"><div class="label">Post Tersimpan</div><div class="value green" id="da2-posts-saved">-</div></div>
  </div>
  <div id="da2-error-box" style="display:none;margin-top:12px;padding:10px 14px;background:#450a0a;border-radius:8px;color:#fca5a5;font-size:0.82rem"></div>

  <div style="margin-top:16px">
    <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:8px">Topik yang Dicakup (HANYA topic-guided, TIDAK ada mode free-discovery) &mdash; daftar SAMA dgn Agent 1, dua agent cari topik yg sama pakai key terpisah</div>
    <div id="da2-topics-covered" style="font-size:0.82rem;color:#e2e8f0">Memuat...</div>
  </div>
</div>

<div id="da2-panel-runs" class="da-panel" style="display:none">
  <table style="margin-top:12px">
    <thead><tr>
      <th>Mulai</th><th>Status</th><th>Topik</th><th>Ditemukan</th><th>Lolos</th><th>Ditolak</th><th>Tersimpan</th><th>Model</th><th></th>
    </tr></thead>
    <tbody id="da2-runs-tbody"><tr><td colspan="9">Memuat...</td></tr></tbody>
  </table>
  <div id="da2-runs-pagination" style="margin-top:10px"></div>
</div>

<div id="da2-panel-config" class="da-panel" style="display:none">
  <div style="max-width:520px;margin-top:12px">
    <div style="font-size:0.78rem;color:#94a3b8;margin-bottom:14px;padding:10px 12px;background:#1e293b;border-radius:8px;line-height:1.5">
      Lihat status/riwayat TIDAK perlu token. Mengubah pengaturan di bawah butuh token
      login (Bearer) &mdash; pakai token yang sama dgn tab Discovery Agent di atas (tersimpan bersama).
    </div>

    <div style="margin-bottom:18px;padding:12px 14px;background:#1e293b;border-radius:8px;display:flex;align-items:center;justify-content:space-between">
      <div>
        <div style="font-size:0.85rem;font-weight:600">Agent 2 Aktif?</div>
        <div style="font-size:0.75rem;color:#94a3b8;margin-top:2px">Matikan kapan saja tanpa menghapus key/model yang tersimpan.</div>
      </div>
      <button class="retry-btn" id="da2-enabled-btn" onclick="da2ToggleEnabled()">Memuat...</button>
    </div>

    <div style="margin-bottom:18px">
      <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:8px">Interval Scheduler</div>
      <div id="da2-interval-btns" style="display:flex;gap:8px"></div>
    </div>

    <div style="margin-bottom:14px">
      <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:6px">Model OpenRouter (milik Agent 2, terpisah dari Agent 1)</div>
      <input type="text" id="da2-model-input" placeholder="mis. nvidia/nemotron-nano-9b-v2:free" style="width:100%;padding:7px 10px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:0.82rem">
    </div>

    <div style="margin-bottom:14px">
      <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:6px">API Key OpenRouter (Agent 2) <span id="da2-key-current" style="color:#64748b"></span></div>
      <input type="password" id="da2-apikey-input" placeholder="Kosongkan kalau tidak diubah" style="width:100%;padding:7px 10px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:0.82rem">
    </div>

    <div style="margin-bottom:14px">
      <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:6px">YouTube Data API Key (Agent 2, TERPISAH dari Agent 1) <span id="da2-ytkey-current" style="color:#64748b"></span></div>
      <input type="password" id="da2-ytkey-input" placeholder="Kosongkan kalau tidak diubah" style="width:100%;padding:7px 10px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:0.82rem">
    </div>

    <button class="retry-btn" onclick="da2SaveConfig()">Simpan Pengaturan</button>
    <span id="da2-config-msg" style="margin-left:10px;font-size:0.82rem"></span>
  </div>
</div>

<div class="section-title" style="margin-top:24px">YouTube Metadata Agent &mdash; lengkapi info video+channel (bukan analisis)</div>
<div class="da-tabbar">
  <button class="da-tab-btn active" id="ma-tab-btn-status" onclick="maSwitchTab('status')">Status</button>
  <button class="da-tab-btn" id="ma-tab-btn-history" onclick="maSwitchTab('history')">Riwayat</button>
  <button class="da-tab-btn" id="ma-tab-btn-config" onclick="maSwitchTab('config')">Pengaturan</button>
</div>

<div id="ma-panel-status" class="da-panel">
  <div id="ma-status-banner" class="ed-banner unknown">
    <span class="alive-dot off" id="ma-dot"></span>
    <div>
      <span class="ed-banner-title">Status Agent &mdash;</span>
      <span id="ma-status-text" style="margin-left:4px">Memuat...</span>
    </div>
    <div style="margin-left:auto;text-align:right;font-size:0.75rem;color:#475569">
      <div id="ma-last-run-at">-</div>
    </div>
  </div>
  <div class="grid" style="margin-top:12px">
    <div class="card"><div class="label">Antrian (Belum Ter-enrich)</div><div class="value yellow" id="ma-pending">-</div></div>
    <div class="card"><div class="label">Total Sudah Ter-enrich</div><div class="value green" id="ma-total-enriched">-</div></div>
    <div class="card"><div class="label">Antrian Refresh (Data Basi)</div><div class="value yellow" id="ma-pending-refresh">-</div></div>
    <div class="card"><div class="label">Judul Berubah (Perlu Ditinjau)</div><div class="value red" id="ma-title-mismatch-count">-</div></div>
  </div>
  <div style="font-size:0.72rem;color:#64748b;margin-top:6px">
    "Antrian Refresh" = video yg SUDAH ter-enrich tapi datanya (views/likes/komentar) lebih tua dari
    <span id="ma-refresh-age-label">-</span> &mdash; akan otomatis diambil ulang tiap agent jalan (Stage 2, terus-menerus).
    "Judul Berubah" = judul tersimpan TIDAK cocok lagi dengan judul asli di YouTube saat terakhir dicek
    (video mungkin ganti judul, atau data awal salah) &mdash; judul lama SENGAJA tidak ditimpa otomatis,
    lihat tab Riwayat (centang "Cuma judul berubah") untuk tinjau manual.
  </div>
</div>

<div id="ma-panel-history" class="da-panel" style="display:none">
  <label style="font-size:0.8rem;color:#94a3b8;display:flex;align-items:center;gap:6px;margin-top:12px">
    <input type="checkbox" id="ma-history-mismatch-only" onchange="maLoadHistory(1)"> Cuma judul berubah (perlu ditinjau)
  </label>
  <table style="margin-top:8px">
    <thead><tr>
      <th>Waktu</th><th>Judul</th><th>Channel</th><th>Views</th><th>Keyword</th><th>Konteks Viral</th>
    </tr></thead>
    <tbody id="ma-history-tbody"><tr><td colspan="6">Memuat...</td></tr></tbody>
  </table>
  <div id="ma-history-pagination" style="margin-top:10px"></div>
</div>

<div id="ma-panel-config" class="da-panel" style="display:none">
  <div style="max-width:520px;margin-top:12px">
    <div style="font-size:0.78rem;color:#94a3b8;margin-bottom:14px;padding:10px 12px;background:#1e293b;border-radius:8px;line-height:1.5">
      Lihat status/riwayat TIDAK perlu token. Mengubah pengaturan di bawah butuh token
      login (Bearer) &mdash; pakai token yang sama dgn tab Discovery Agent di atas (tersimpan bersama).
    </div>

    <div style="margin-bottom:18px">
      <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:8px">Interval Scheduler (menit)</div>
      <div id="ma-interval-btns" style="display:flex;gap:8px"></div>
    </div>

    <div style="margin-bottom:18px">
      <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:8px">Refresh data basi setelah (jam) &mdash; Stage 2, butuh login</div>
      <div id="ma-refresh-age-btns" style="display:flex;gap:8px;flex-wrap:wrap"></div>
    </div>

    <div style="margin-bottom:18px">
      <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:8px">Jumlah video di-refresh per run &mdash; butuh login</div>
      <div id="ma-refresh-batch-btns" style="display:flex;gap:8px;flex-wrap:wrap"></div>
    </div>

    <div style="margin-bottom:18px">
      <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:8px">Jumlah post BARU di-enrich per run (kecepatan kejar backlog) &mdash; butuh login</div>
      <div id="ma-enrich-batch-btns" style="display:flex;gap:8px;flex-wrap:wrap"></div>
      <div style="font-size:0.72rem;color:#64748b;margin-top:6px">Makin besar = makin cepat backlog habis, tapi makin banyak kuota YouTube API terpakai per run. Naikkan bertahap sambil pantau error kuota.</div>
    </div>

    <div style="margin-bottom:14px">
      <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:6px">Model OpenRouter (khusus viral_context)</div>
      <input type="text" id="ma-model-input" placeholder="mis. openai/gpt-oss-20b:free" style="width:100%;padding:7px 10px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:0.82rem">
    </div>

    <div style="margin-bottom:14px">
      <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:6px">API Key OpenRouter <span id="ma-key-current" style="color:#64748b"></span></div>
      <input type="password" id="ma-apikey-input" placeholder="Kosongkan kalau tidak diubah" style="width:100%;padding:7px 10px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:0.82rem">
    </div>

    <button class="retry-btn" onclick="maSaveConfig()">Simpan Pengaturan</button>
    <span id="ma-config-msg" style="margin-left:10px;font-size:0.82rem"></span>
  </div>
</div>

<div class="section-title" style="margin-top:24px">Sentiment Agent &mdash; opini kedua LLM utk komentar yg lexicon kemungkinan salah</div>
<div class="da-tabbar">
  <button class="da-tab-btn active" id="sa-tab-btn-status" onclick="saSwitchTab('status')">Status</button>
  <button class="da-tab-btn" id="sa-tab-btn-history" onclick="saSwitchTab('history')">Riwayat</button>
  <button class="da-tab-btn" id="sa-tab-btn-config" onclick="saSwitchTab('config')">Pengaturan</button>
</div>

<div id="sa-panel-status" class="da-panel">
  <div id="sa-status-banner" class="ed-banner unknown">
    <span class="alive-dot off" id="sa-dot"></span>
    <div>
      <span class="ed-banner-title">Status Agent &mdash;</span>
      <span id="sa-status-text" style="margin-left:4px">Memuat...</span>
    </div>
    <div style="margin-left:auto;text-align:right;font-size:0.75rem;color:#475569">
      <div id="sa-last-run-at">-</div>
    </div>
  </div>
  <div class="grid" style="margin-top:12px">
    <div class="card"><div class="label">Antrian (Belum Direview)</div><div class="value yellow" id="sa-pending">-</div></div>
    <div class="card"><div class="label">Direview LLM</div><div class="value blue" id="sa-reviewed-by-llm">-</div></div>
    <div class="card"><div class="label">Sepakat dgn Lexicon</div><div class="value green" id="sa-agreements">-</div></div>
    <div class="card"><div class="label">Beda Pendapat</div><div class="value red" id="sa-disagreements">-</div></div>
  </div>
  <div style="font-size:0.72rem;color:#64748b;margin-top:6px">
    Cuma komentar BUKAN Bahasa Indonesia atau berlabel "netral" dari lexicon yg direview LLM (yg lain
    dipercaya lexicon apa adanya, tidak buang panggilan LLM percuma). Lexicon TIDAK ditimpa -- lihat
    tab Riwayat utk bandingkan kedua opininya.
  </div>
</div>

<div id="sa-panel-history" class="da-panel" style="display:none">
  <label style="font-size:0.8rem;color:#94a3b8;display:flex;align-items:center;gap:6px;margin-top:12px">
    <input type="checkbox" id="sa-history-disagreement-only" onchange="saLoadHistory(1)"> Cuma yang beda pendapat (lexicon vs LLM)
  </label>
  <table style="margin-top:8px">
    <thead><tr>
      <th>Waktu</th><th>Komentar</th><th>Bahasa</th><th>Lexicon</th><th>LLM</th><th>Sepakat?</th>
    </tr></thead>
    <tbody id="sa-history-tbody"><tr><td colspan="6">Memuat...</td></tr></tbody>
  </table>
  <div id="sa-history-pagination" style="margin-top:10px"></div>
</div>

<div id="sa-panel-config" class="da-panel" style="display:none">
  <div style="max-width:520px;margin-top:12px">
    <div style="font-size:0.78rem;color:#94a3b8;margin-bottom:14px;padding:10px 12px;background:#1e293b;border-radius:8px;line-height:1.5">
      Lihat status/riwayat TIDAK perlu token. Mengubah pengaturan di bawah butuh token
      login (Bearer) &mdash; pakai token yang sama dgn tab Discovery Agent di atas (tersimpan bersama).
    </div>

    <div style="margin-bottom:18px">
      <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:8px">Interval Scheduler (menit)</div>
      <div id="sa-interval-btns" style="display:flex;gap:8px"></div>
    </div>

    <div style="margin-bottom:18px">
      <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:8px">Jumlah komentar direview per run</div>
      <div id="sa-batch-btns" style="display:flex;gap:8px;flex-wrap:wrap"></div>
    </div>

    <div style="margin-bottom:14px">
      <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:6px">Model OpenRouter</div>
      <input type="text" id="sa-model-input" placeholder="mis. openai/gpt-oss-20b:free" style="width:100%;padding:7px 10px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:0.82rem">
    </div>

    <div style="margin-bottom:14px">
      <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:6px">API Key OpenRouter <span id="sa-key-current" style="color:#64748b"></span></div>
      <input type="password" id="sa-apikey-input" placeholder="Kosongkan kalau tidak diubah" style="width:100%;padding:7px 10px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:0.82rem">
    </div>

    <div style="font-size:0.78rem;color:#94a3b8;margin:20px 0 14px;padding:10px 12px;background:#1e293b;border-radius:8px;line-height:1.5">
      <b>Tie-breaker</b> &mdash; LLM KEDUA (provider beda) yg jadi penengah HANYA saat lexicon vs
      LLM pertama tidak sepakat. Kalau kosong, tie-breaker dilewati (langsung ditandai "tidak sepakat" tanpa suara ketiga).
    </div>

    <div style="margin-bottom:14px">
      <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:6px">Model Tie-breaker</div>
      <input type="text" id="sa-tb-model-input" placeholder="mis. google/gemma-4-31b-it:free" style="width:100%;padding:7px 10px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:0.82rem">
    </div>

    <div style="margin-bottom:14px">
      <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:6px">API Key Tie-breaker <span id="sa-tb-key-current" style="color:#64748b"></span></div>
      <input type="password" id="sa-tb-apikey-input" placeholder="Kosongkan kalau tidak diubah" style="width:100%;padding:7px 10px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:0.82rem">
    </div>

    <button class="retry-btn" onclick="saSaveConfig()">Simpan Pengaturan</button>
    <span id="sa-config-msg" style="margin-left:10px;font-size:0.82rem"></span>

    <div style="margin-top:26px">
      <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:8px">Usulan Kata Baru utk Kamus Lexicon (dari kasus lexicon kalah suara)</div>
      <table>
        <thead><tr><th>Kata</th><th>Polaritas</th><th>Muncul</th><th>Contoh Komentar</th></tr></thead>
        <tbody id="sa-suggestions-tbody"><tr><td colspan="4">Memuat...</td></tr></tbody>
      </table>
      <div style="font-size:0.72rem;color:#64748b;margin-top:6px">
        Ini USULAN, TIDAK otomatis ditambahkan ke kamus -- tinjau manual dulu sebelum dimasukkan ke
        app/ai/lexicon/data/positive.txt atau negative.txt (kamus ini dipakai lintas platform, bukan cuma YouTube).
      </div>
    </div>
  </div>
</div>

<div class="section-title" style="margin-top:24px">Views Refresh Agent &mdash; agent kedua, kuota YouTube API TERPISAH, khusus konsistensi views (prioritas video topic-search dulu)</div>
<div id="vr-status-banner" class="ed-banner unknown" style="margin-top:12px">
  <span class="alive-dot off" id="vr-dot"></span>
  <div>
    <span class="ed-banner-title">Status Agent &mdash;</span>
    <span id="vr-status-text" style="margin-left:4px">Memuat...</span>
  </div>
  <div style="margin-left:auto;text-align:right;font-size:0.75rem;color:#475569">
    <div id="vr-last-run-at">-</div>
  </div>
</div>
<div class="grid" style="margin-top:12px">
  <div class="card"><div class="label">Antrian Refresh (Kuota Terpisah)</div><div class="value yellow" id="vr-pending">-</div></div>
</div>
<div style="font-size:0.72rem;color:#64748b;margin:6px 0 16px">
  Agent ini CUMA update <b>views/likes/comments</b> (angka saja, cepat) -- TIDAK ambil ulang info
  channel/subscriber/konten komentar (itu tetap tugas Metadata Agent, lebih lengkap tapi lebih pelan).
  Prioritas: video yg terkait topic-search diproses LEBIH DULU drpd video tanpa keterkaitan topik.
  Pakai API key YouTube TERPISAH (kuota 10.000/hari sendiri) -- jalan berdampingan dgn Metadata Agent
  tanpa tabrakan (baris yg sedang diproses satu agent otomatis dilewati agent lainnya).
</div>

<div style="max-width:520px">
  <div style="font-size:0.78rem;color:#94a3b8;margin-bottom:14px;padding:10px 12px;background:#1e293b;border-radius:8px;line-height:1.5">
    Lihat status TIDAK perlu token. Mengubah pengaturan di bawah butuh token login (Bearer) &mdash;
    pakai token yang sama dgn tab Discovery Agent di atas.
  </div>

  <div style="margin-bottom:18px">
    <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:8px">Interval Scheduler (menit)</div>
    <div id="vr-interval-btns" style="display:flex;gap:8px"></div>
  </div>

  <div style="margin-bottom:18px">
    <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:8px">Jumlah video di-refresh per run</div>
    <div id="vr-batch-btns" style="display:flex;gap:8px;flex-wrap:wrap"></div>
  </div>

  <div style="margin-bottom:18px">
    <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:8px">Refresh data basi setelah (jam)</div>
    <div id="vr-age-btns" style="display:flex;gap:8px;flex-wrap:wrap"></div>
  </div>

  <div style="margin-bottom:14px">
    <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:6px">API Key YouTube Data API v3 (project Google Cloud TERPISAH) <span id="vr-key-current" style="color:#64748b"></span></div>
    <input type="password" id="vr-apikey-input" placeholder="Kosongkan kalau tidak diubah" style="width:100%;padding:7px 10px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:0.82rem">
  </div>

  <button class="retry-btn" onclick="vrSaveConfig()">Simpan Pengaturan</button>
  <span id="vr-config-msg" style="margin-left:10px;font-size:0.82rem"></span>
</div>

<div class="section-title" style="margin-top:24px">YouTube API Key &mdash; Monitoring Kuota Semua Agent</div>
<div style="font-size:0.72rem;color:#64748b;margin-bottom:12px;max-width:760px">
  Cek status LANGSUNG dari Google (1 unit kuota per key UNIK, key yg dipakai &gt;1 slot cuma dites sekali).
  Kalau kolom "Berbagi Dengan" terisi, artinya 2 slot itu memakai key YANG SAMA (kuota digabung, BUKAN
  rotasi terpisah) -- isi slot yg kosong/berbagi dengan key baru supaya benar-benar terpisah.
</div>
<div style="margin-bottom:10px">
  <button class="retry-btn" onclick="ykCheckNow()">Cek Status Sekarang</button>
  <span id="yk-msg" style="margin-left:10px;font-size:0.82rem;color:#64748b"></span>
</div>
<div style="overflow-x:auto">
<table>
  <thead><tr><th>Slot</th><th>Key</th><th>Berbagi Dengan</th><th>Status</th><th>Keterangan</th><th>Ganti Key</th></tr></thead>
  <tbody id="yk-tbody"><tr><td colspan="6" style="color:#475569;font-style:italic">Klik "Cek Status Sekarang" utk mulai (butuh token login Bearer, sama dgn tab Discovery Agent di atas)</td></tr></tbody>
</table>
</div>

<div class="grid" style="margin-top:24px">
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

<div class="section-title" style="margin-top:24px">Instagram — Statistik</div>
<div class="ig-grid">
  <div class="ig-card"><div class="label">Total Posts</div><div class="value" id="ig-posts">-</div><div class="sub">platform instagram</div></div>
  <div class="ig-card"><div class="label">Total Komentar</div><div class="value" id="ig-comments">-</div><div class="sub">platform instagram</div></div>
  <div class="ig-card"><div class="label">Scrape Hari Ini</div><div class="value" id="ig-today">-</div><div class="sub">akun di-scrape</div></div>
</div>

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
  <div class="ig-card"><div class="label">Gagal Permanen</div><div class="value" id="fts-failed-permanent">-</div><div class="sub">menyerah setelah 3x gagal</div></div>
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

<div class="section-title" style="margin-top:24px">Alur Pipeline Live — Subsistem A (AI Discovery) &rarr; Subsistem B (Scrape Worker Facebook)</div>
<div class="pf-legend">
  <div class="pf-legend-item"><span class="pf-legend-dot" style="border-color:#4ade80;background:rgba(74,222,128,0.15)"></span> Sukses</div>
  <div class="pf-legend-item"><span class="pf-legend-dot" style="border-color:#f87171;background:rgba(248,113,113,0.15)"></span> Gagal / Berhenti Di Sini</div>
  <div class="pf-legend-item"><span class="pf-legend-dot" style="border-color:#fbbf24;background:rgba(251,191,36,0.15)"></span> Menunggu</div>
  <div class="pf-legend-item"><span class="pf-legend-dot" style="border-color:#334155"></span> Belum Ada Data</div>
</div>
<div class="pipeline-flow" id="fb-pipeline-flow"></div>
<table class="pf-batch-table" id="fb-pf-batch-wrap" style="display:none">
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
  <tbody id="fb-pf-batch-table"></tbody>
</table>
<div class="pf-empty-hint" id="fb-pf-empty-hint" style="display:none">Belum ada run AI Viral Discovery tercatat — jalan otomatis jam 07:00 WIB, atau trigger manual untuk test.</div>

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

<div class="section-title" style="margin-top:24px">TikTok Trend-Scrape (trend_recommendations)</div>
<div class="ig-grid">
  <div class="ig-card"><div class="label">Pending</div><div class="value" id="tts-pending">-</div><div class="sub">nunggu giliran scrape</div></div>
  <div class="ig-card"><div class="label">Sudah Discrape</div><div class="value" id="tts-used">-</div><div class="sub">status=used</div></div>
  <div class="ig-card"><div class="label">Gagal Permanen</div><div class="value" id="tts-failed-permanent">-</div><div class="sub">menyerah setelah 3x gagal</div></div>
  <div class="ig-card"><div class="label">Budget Harian</div><div class="value" id="tts-budget">-</div><div class="sub">topik/hari (Apify)</div></div>
  <div class="ig-card"><div class="label">Jadwal</div><div class="value" id="tts-schedule" style="font-size:1rem">-</div><div class="sub">Celery Beat</div></div>
</div>
<table>
  <thead>
    <tr>
      <th>Topik</th>
      <th>Score</th>
      <th>Akun TikTok</th>
      <th>Sumber</th>
      <th>Dibuat</th>
    </tr>
  </thead>
  <tbody id="tts-pending-table"></tbody>
</table>

<div class="section-title" style="margin-top:24px">Alur Pipeline Live — Subsistem A (AI Discovery) &rarr; Subsistem B (Scrape Worker TikTok)</div>
<div class="pf-legend">
  <div class="pf-legend-item"><span class="pf-legend-dot" style="border-color:#4ade80;background:rgba(74,222,128,0.15)"></span> Sukses</div>
  <div class="pf-legend-item"><span class="pf-legend-dot" style="border-color:#f87171;background:rgba(248,113,113,0.15)"></span> Gagal / Berhenti Di Sini</div>
  <div class="pf-legend-item"><span class="pf-legend-dot" style="border-color:#fbbf24;background:rgba(251,191,36,0.15)"></span> Menunggu</div>
  <div class="pf-legend-item"><span class="pf-legend-dot" style="border-color:#334155"></span> Belum Ada Data</div>
</div>
<div class="pipeline-flow" id="tt-pipeline-flow"></div>
<table class="pf-batch-table" id="tt-pf-batch-wrap" style="display:none">
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
  <tbody id="tt-pf-batch-table"></tbody>
</table>
<div class="pf-empty-hint" id="tt-pf-empty-hint" style="display:none">Belum ada run AI Viral Discovery tercatat — jalan otomatis jam 07:00 WIB, atau trigger manual untuk test.</div>

<div class="section-title" style="margin-top:24px">Riwayat Scrape TikTok (trend_recommendations)</div>
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
  <tbody id="tts-runs-table"></tbody>
</table>

<div class="section-title" style="margin-top:24px">Twitter/X Trend-Scrape (trend_recommendations)</div>
<div class="ig-grid">
  <div class="ig-card"><div class="label">Pending</div><div class="value" id="twts-pending">-</div><div class="sub">nunggu giliran scrape</div></div>
  <div class="ig-card"><div class="label">Sudah Discrape</div><div class="value" id="twts-used">-</div><div class="sub">status=used</div></div>
  <div class="ig-card"><div class="label">Gagal Permanen</div><div class="value" id="twts-failed-permanent">-</div><div class="sub">menyerah setelah 3x gagal</div></div>
  <div class="ig-card"><div class="label">Budget Harian</div><div class="value" id="twts-budget">-</div><div class="sub">topik/hari (Apify)</div></div>
  <div class="ig-card"><div class="label">Jadwal</div><div class="value" id="twts-schedule" style="font-size:1rem">-</div><div class="sub">Celery Beat</div></div>
</div>
<table>
  <thead>
    <tr>
      <th>Topik</th>
      <th>Score</th>
      <th>Akun Twitter</th>
      <th>Sumber</th>
      <th>Dibuat</th>
    </tr>
  </thead>
  <tbody id="twts-pending-table"></tbody>
</table>

<div class="section-title" style="margin-top:24px">Alur Pipeline Live — Subsistem A (AI Discovery) &rarr; Subsistem B (Scrape Worker Twitter/X)</div>
<div class="pf-legend">
  <div class="pf-legend-item"><span class="pf-legend-dot" style="border-color:#4ade80;background:rgba(74,222,128,0.15)"></span> Sukses</div>
  <div class="pf-legend-item"><span class="pf-legend-dot" style="border-color:#f87171;background:rgba(248,113,113,0.15)"></span> Gagal / Berhenti Di Sini</div>
  <div class="pf-legend-item"><span class="pf-legend-dot" style="border-color:#fbbf24;background:rgba(251,191,36,0.15)"></span> Menunggu</div>
  <div class="pf-legend-item"><span class="pf-legend-dot" style="border-color:#334155"></span> Belum Ada Data</div>
</div>
<div class="pipeline-flow" id="tw-pipeline-flow"></div>
<table class="pf-batch-table" id="tw-pf-batch-wrap" style="display:none">
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
  <tbody id="tw-pf-batch-table"></tbody>
</table>
<div class="pf-empty-hint" id="tw-pf-empty-hint" style="display:none">Belum ada run AI Viral Discovery tercatat — jalan otomatis jam 07:00 WIB, atau trigger manual untuk test.</div>

<div class="section-title" style="margin-top:24px">Riwayat Scrape Twitter/X (trend_recommendations)</div>
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
  <tbody id="twts-runs-table"></tbody>
</table>

<div class="section-title" style="margin-top:24px">News — Statistik & Jadwal (pipeline mandiri, Firecrawl)</div>
<div class="ig-grid">
  <div class="ig-card"><div class="label">Total Artikel</div><div class="value" id="news-total">-</div><div class="sub">platform news</div></div>
  <div class="ig-card"><div class="label">Artikel Hari Ini</div><div class="value" id="news-today">-</div><div class="sub">ditemukan hari ini</div></div>
  <div class="ig-card"><div class="label">Budget Harian</div><div class="value" id="news-budget">-</div><div class="sub">artikel baru/hari (Firecrawl)</div></div>
  <div class="ig-card"><div class="label">Jadwal</div><div class="value" id="news-schedule" style="font-size:0.95rem">-</div><div class="sub">Celery Beat, mandiri</div></div>
</div>
<table>
  <thead>
    <tr>
      <th>Judul</th>
      <th>URL</th>
      <th>Ditemukan</th>
    </tr>
  </thead>
  <tbody id="news-latest-table"></tbody>
</table>

<div class="section-title" style="margin-top:24px">Riwayat Discovery News</div>
<table>
  <thead>
    <tr>
      <th>Run</th>
      <th>Status</th>
      <th>Sumber</th>
      <th>Artikel Baru</th>
      <th>Durasi</th>
      <th>Waktu Mulai</th>
      <th>Error</th>
    </tr>
  </thead>
  <tbody id="news-runs-table"></tbody>
</table>

<div class="section-title" style="margin-top:24px">Smart Search — AI-Context Discovery (Subsistem A2)</div>
<div class="ig-grid">
  <div class="ig-card"><div class="label">Provider Aktif</div><div class="value" id="std-provider" style="font-size:1rem">-</div><div class="sub">anthropic/ollama/auto</div></div>
  <div class="ig-card"><div class="label">Jadwal</div><div class="value" id="std-schedule" style="font-size:0.9rem">-</div><div class="sub">Celery Beat</div></div>
  <div class="ig-card"><div class="label">Topik Dipertimbangkan</div><div class="value" id="std-considered">-</div><div class="sub">run terakhir</div></div>
  <div class="ig-card"><div class="label">Topik Dipanggil AI</div><div class="value" id="std-called">-</div><div class="sub">run terakhir</div></div>
</div>
<table>
  <thead>
    <tr>
      <th>Topik (Smart Search)</th>
      <th>Status Panggilan AI</th>
      <th>Durasi</th>
      <th>Sub-topik Baru Ditemukan</th>
      <th>Waktu Panggilan</th>
      <th>Error</th>
    </tr>
  </thead>
  <tbody id="std-topics-table"></tbody>
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
function renderPipelineFlow(trace, idPrefix) {
  idPrefix = idPrefix || '';
  const flowEl = document.getElementById(idPrefix + 'pipeline-flow');
  const batchWrap = document.getElementById(idPrefix + 'pf-batch-wrap');
  const batchTbody = document.getElementById(idPrefix + 'pf-batch-table');
  const emptyHint = document.getElementById(idPrefix + 'pf-empty-hint');
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

  const providerLabel = { anthropic: 'Claude web_search', openai: 'OpenAI (tanpa browsing)', ollama: 'Ollama + Firecrawl/Tavily' };
  const nodes = [
    { label: 'AI Discovery',    sub: providerLabel[aiRun.api_source] || aiRun.api_source || '-', status: s1 },
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

    // ── Ollama status banner (model lokal, fallback AI Discovery) -- BARU,
    // permintaan user 2026-07-18 "apakah ollama sudah ada di dashboard" ──
    try {
      const ro = await fetch(base + '/api/v1/system/ollama-status');
      const jo = await ro.json();
      const od = jo.data || {};
      const ollamaBanner = document.getElementById('ollama-banner');
      const ollamaDot = document.getElementById('ollama-dot');
      const ollamaText = document.getElementById('ollama-status-text');
      const ollamaModels = document.getElementById('ollama-models');
      if (od.alive) {
        ollamaBanner.className = 'ed-banner active';
        ollamaDot.className = 'alive-dot on';
        ollamaText.innerHTML = '<span class="green">AKTIF</span>';
      } else {
        ollamaBanner.className = 'ed-banner expired';
        ollamaDot.className = 'alive-dot off';
        ollamaText.innerHTML = '<span class="red">MATI</span>';
      }
      const models = od.loaded_models || [];
      if (models.length === 0) {
        ollamaModels.textContent = 'Tidak ada model ter-load di memori saat ini';
      } else {
        ollamaModels.innerHTML = models.map(m =>
          `${daEsc(m.name)} (${m.size_mb} MB)`
        ).join(', ');
      }
    } catch(e) { console.error(e); }

    // ── Apify quota banner (Facebook/Instagram/TikTok/Twitter/Smart Search) ──
    const apifyQ = d.apify_quota || {};
    const apifyBanner = document.getElementById('apify-banner');
    const apifyDot  = document.getElementById('apify-dot');
    const apifyText = document.getElementById('apify-status-text');
    const apifyDetail = document.getElementById('apify-detail');
    if (!apifyQ.checked) {
      apifyBanner.className = 'ed-banner unknown';
      apifyDot.className = 'alive-dot off';
      apifyText.innerHTML = '<span style="color:#64748b">TIDAK BISA DICEK</span>';
      apifyDetail.textContent = apifyQ.message || '';
    } else if (apifyQ.exhausted) {
      apifyBanner.className = 'ed-banner expired';
      apifyDot.className = 'alive-dot off';
      apifyText.innerHTML = '<span class="red">KUOTA HABIS</span>';
      apifyDetail.textContent = apifyQ.message || '';
    } else {
      apifyBanner.className = 'ed-banner active';
      apifyDot.className = 'alive-dot on';
      apifyText.innerHTML = `<span class="green">TERSEDIA</span> (${apifyQ.plan || '-'})`;
      apifyDetail.textContent = apifyQ.message || '';
    }

    // ── YouTube Data API v3 quota banner (fallback EnsembleData) ────────────
    const ytq = d.youtube_data_api_quota || {};
    const ytqBanner = document.getElementById('ytq-banner');
    const ytqDot  = document.getElementById('ytq-dot');
    const ytqText = document.getElementById('ytq-status-text');
    const ytqDetail = document.getElementById('ytq-status-detail');
    const ytqLastErr = document.getElementById('ytq-last-err');
    const ytqLastOk  = document.getElementById('ytq-last-ok');
    ytqBanner.className = `ed-banner ${ytq.status || 'unknown'}`;
    if (ytq.status === 'active') {
      ytqDot.className = 'alive-dot on';
      ytqText.innerHTML = '<span class="green">AKTIF</span>';
      ytqDetail.textContent = ytq.message || '';
    } else if (ytq.status === 'expired') {
      ytqDot.className = 'alive-dot off';
      ytqText.innerHTML = '<span class="red">QUOTA HABIS</span>';
      ytqDetail.textContent = ytq.message || '';
    } else {
      ytqDot.className = 'alive-dot off';
      ytqText.innerHTML = '<span style="color:#64748b">UNKNOWN</span>';
      ytqDetail.textContent = ytq.message || 'Belum ada data';
    }
    ytqLastErr.textContent = ytq.last_error_at  ? `Error terakhir: ${fmt(ytq.last_error_at)}` : '';
    ytqLastOk.textContent  = ytq.last_success_at ? `Sukses terakhir: ${fmt(ytq.last_success_at)}` : '';

    // ── Backfill views/likes/comments YouTube lama ──────────────────────────
    const bf = d.youtube_backfill || {};
    const bfBanner = document.getElementById('bf-banner');
    const bfDot  = document.getElementById('bf-dot');
    const bfText = document.getElementById('bf-status-text');
    const bfDetail = document.getElementById('bf-status-detail');
    const bfProgress = document.getElementById('bf-progress');
    if (bf.status === 'completed') {
      bfBanner.className = 'ed-banner active';
      bfDot.className = 'alive-dot on';
      bfText.innerHTML = '<span class="green">SELESAI</span> ✓';
    } else if (bf.status === 'running') {
      bfBanner.className = 'ed-banner unknown';
      bfDot.className = 'alive-dot on';
      bfText.innerHTML = '<span style="color:#60a5fa">SEDANG JALAN...</span>';
    } else if (bf.status === 'error') {
      bfBanner.className = 'ed-banner expired';
      bfDot.className = 'alive-dot off';
      bfText.innerHTML = '<span class="red">ERROR</span>';
    } else if (bf.status === 'stopped') {
      bfBanner.className = 'ed-banner unknown';
      bfDot.className = 'alive-dot off';
      bfText.innerHTML = '<span style="color:#64748b">DIHENTIKAN MANUAL</span>';
    } else {
      bfBanner.className = 'ed-banner unknown';
      bfDot.className = 'alive-dot off';
      bfText.innerHTML = '<span style="color:#64748b">BELUM PERNAH JALAN</span>';
    }
    bfDetail.textContent = bf.message || '';
    bfProgress.textContent = bf.total_target
      ? `${bf.processed || 0}/${bf.total_target} diperiksa · ${bf.updated || 0} diperbaiki · ${bf.remaining || 0} sisa`
      : '';

    // ── Instagram statistik ───────────────────────────────────────────────
    const ig = d.instagram || {};
    document.getElementById('ig-posts').textContent    = (ig.total_posts    || 0).toLocaleString();
    document.getElementById('ig-comments').textContent = (ig.total_comments || 0).toLocaleString();
    document.getElementById('ig-today').textContent    = (ig.accounts_scraped_today || 0).toLocaleString();

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
    renderPipelineFlow(fts.viral_discovery_trace, 'fb-');
    document.getElementById('fts-pending').textContent  = ftsSummary.pending_with_facebook_account || 0;
    document.getElementById('fts-used').textContent     = ftsSummary.used_with_facebook_account || 0;
    document.getElementById('fts-failed-permanent').textContent = ftsSummary.failed_permanent_with_facebook_account || 0;
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

    // ── TikTok trend-scrape (trend_recommendations) ──────────────────────────
    const tts = d.tiktok_trend_scrape || {};
    const ttsSummary = tts.summary || {};
    renderPipelineFlow(tts.viral_discovery_trace, 'tt-');
    document.getElementById('tts-pending').textContent  = ttsSummary.pending_with_tiktok_account || 0;
    document.getElementById('tts-used').textContent     = ttsSummary.used_with_tiktok_account || 0;
    document.getElementById('tts-failed-permanent').textContent = ttsSummary.failed_permanent_with_tiktok_account || 0;
    document.getElementById('tts-budget').textContent   = tts.daily_budget ?? '-';
    document.getElementById('tts-schedule').textContent = tts.schedule ?? '-';

    const ttsPendingTbody = document.getElementById('tts-pending-table');
    const ttsPending = tts.pending_topics || [];
    if (ttsPending.length === 0) {
      ttsPendingTbody.innerHTML = '<tr><td colspan="5" style="color:#475569;font-style:italic;padding:12px">Tidak ada topik pending</td></tr>';
    } else {
      ttsPendingTbody.innerHTML = ttsPending.map(t => `<tr>
        <td>${t.topic}</td>
        <td>${(t.score||0).toFixed(2)}</td>
        <td>@${t.tiktok_identifier || '-'}</td>
        <td style="color:#64748b;font-size:.72rem">${t.source || '-'}</td>
        <td style="color:#94a3b8;font-size:.75rem">${fmt(t.created_at)}</td>
      </tr>`).join('');
    }

    const ttsRunsTbody = document.getElementById('tts-runs-table');
    const ttsRuns = tts.recent_runs || [];
    if (ttsRuns.length === 0) {
      ttsRunsTbody.innerHTML = '<tr><td colspan="7" style="color:#475569;font-style:italic;padding:12px">Belum ada riwayat scrape</td></tr>';
    } else {
      ttsRunsTbody.innerHTML = ttsRuns.map(r => {
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

    // ── Twitter/X trend-scrape (trend_recommendations) ───────────────────────
    const twts = d.twitter_trend_scrape || {};
    const twtsSummary = twts.summary || {};
    renderPipelineFlow(twts.viral_discovery_trace, 'tw-');
    document.getElementById('twts-pending').textContent  = twtsSummary.pending_with_twitter_account || 0;
    document.getElementById('twts-used').textContent     = twtsSummary.used_with_twitter_account || 0;
    document.getElementById('twts-failed-permanent').textContent = twtsSummary.failed_permanent_with_twitter_account || 0;
    document.getElementById('twts-budget').textContent   = twts.daily_budget ?? '-';
    document.getElementById('twts-schedule').textContent = twts.schedule ?? '-';

    const twtsPendingTbody = document.getElementById('twts-pending-table');
    const twtsPending = twts.pending_topics || [];
    if (twtsPending.length === 0) {
      twtsPendingTbody.innerHTML = '<tr><td colspan="5" style="color:#475569;font-style:italic;padding:12px">Tidak ada topik pending</td></tr>';
    } else {
      twtsPendingTbody.innerHTML = twtsPending.map(t => `<tr>
        <td>${t.topic}</td>
        <td>${(t.score||0).toFixed(2)}</td>
        <td>@${t.twitter_identifier || '-'}</td>
        <td style="color:#64748b;font-size:.72rem">${t.source || '-'}</td>
        <td style="color:#94a3b8;font-size:.75rem">${fmt(t.created_at)}</td>
      </tr>`).join('');
    }

    const twtsRunsTbody = document.getElementById('twts-runs-table');
    const twtsRuns = twts.recent_runs || [];
    if (twtsRuns.length === 0) {
      twtsRunsTbody.innerHTML = '<tr><td colspan="7" style="color:#475569;font-style:italic;padding:12px">Belum ada riwayat scrape</td></tr>';
    } else {
      twtsRunsTbody.innerHTML = twtsRuns.map(r => {
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

    // ── News (pipeline mandiri, Firecrawl — TIDAK terkait trend_recommendations) ──
    const news = d.news_trend_scrape || {};
    const newsSummary = news.summary || {};
    document.getElementById('news-total').textContent    = newsSummary.total_articles || 0;
    document.getElementById('news-today').textContent    = newsSummary.articles_today || 0;
    document.getElementById('news-budget').textContent   = news.daily_budget ?? '-';
    document.getElementById('news-schedule').textContent = news.schedule ?? '-';

    const newsLatestTbody = document.getElementById('news-latest-table');
    const newsLatest = news.latest_articles || [];
    if (newsLatest.length === 0) {
      newsLatestTbody.innerHTML = '<tr><td colspan="3" style="color:#475569;font-style:italic;padding:12px">Belum ada artikel</td></tr>';
    } else {
      newsLatestTbody.innerHTML = newsLatest.map(a => `<tr>
        <td>${a.title || '-'}</td>
        <td style="font-size:.72rem"><a href="${a.url}" target="_blank" rel="noopener" style="color:#38bdf8">${(a.url||'').substring(0,60)}</a></td>
        <td style="color:#94a3b8;font-size:.75rem">${fmt(a.collected_at)}</td>
      </tr>`).join('');
    }

    const newsRunsTbody = document.getElementById('news-runs-table');
    const newsRuns = news.recent_runs || [];
    if (newsRuns.length === 0) {
      newsRunsTbody.innerHTML = '<tr><td colspan="7" style="color:#475569;font-style:italic;padding:12px">Belum ada riwayat discovery</td></tr>';
    } else {
      newsRunsTbody.innerHTML = newsRuns.map(r => {
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

    // ── Smart Search AI-Context Discovery (Subsistem A2) ─────────────────────
    const std = d.search_topics_ai_discovery || {};
    const stdLastRun = std.last_run || {};
    document.getElementById('std-provider').textContent   = stdLastRun.api_source || '-';
    document.getElementById('std-schedule').textContent   = std.schedule || '-';
    document.getElementById('std-considered').textContent = stdLastRun.topics_considered ?? '-';
    document.getElementById('std-called').textContent     = stdLastRun.topics_called ?? '-';

    const stdTopicsTbody = document.getElementById('std-topics-table');
    const stdTopics = std.topics || [];
    if (stdTopics.length === 0) {
      stdTopicsTbody.innerHTML = '<tr><td colspan="6" style="color:#475569;font-style:italic;padding:12px">Belum ada topik yang dipanggil AI discovery</td></tr>';
    } else {
      stdTopicsTbody.innerHTML = stdTopics.map(t => {
        const pillClass = t.ai_call_status === 'success' ? 'pill-success' : (t.ai_call_status === 'failed' ? 'pill-failed' : 'pill-running');
        const subtopics = (t.found_subtopics || []).map(s => s.subtopic).join('<br>') || '-';
        return `<tr>
          <td>${t.context_topic_name}</td>
          <td><span class="pill ${pillClass}">${t.ai_call_status}</span></td>
          <td style="color:#94a3b8">${t.duration_seconds ?? '-'}s</td>
          <td style="font-size:.78rem">${subtopics}</td>
          <td style="color:#94a3b8;font-size:.75rem">${fmt(t.ai_call_started_at)}</td>
          <td class="error-text" title="${t.error_message||''}">${t.error_message || '-'}</td>
        </tr>`;
      }).join('');
    }

    // ── Sedang Berjalan Sekarang — gabungan Instagram + Facebook + TikTok + Twitter ──
    const itsRunningTbody = document.getElementById('its-running-table');
    const triggerLabel = { manual_api: 'Manual (Frontend/API)', manual_cli: 'Manual (CLI)', celery_beat: 'Otomatis (Jadwal)' };
    const allRunning = [
      ...(its.running_now || []).map(r => ({ ...r, platform: 'Instagram' })),
      ...(fts.running_now || []).map(r => ({ ...r, platform: 'Facebook' })),
      ...(tts.running_now || []).map(r => ({ ...r, platform: 'TikTok' })),
      ...(twts.running_now || []).map(r => ({ ...r, platform: 'Twitter' })),
    ];
    const platformPill = { Facebook: 'pill-waiting', TikTok: 'pill-fallback', Instagram: 'pill-success', Twitter: 'pill-running' };
    if (allRunning.length === 0) {
      itsRunningTbody.innerHTML = '<tr><td colspan="5" style="color:#475569;font-style:italic;padding:12px">Tidak ada scraping yang sedang berjalan</td></tr>';
    } else {
      itsRunningTbody.innerHTML = allRunning.map(r => {
        const secs = r.elapsed_seconds || 0;
        const elapsed = secs < 60 ? `${secs.toFixed(0)}d` : `${Math.floor(secs/60)}m ${(secs%60).toFixed(0)}d`;
        return `<tr>
          <td><span class="pill ${platformPill[r.platform] || 'pill-success'}">${r.platform}</span></td>
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

// ── YouTube Discovery Agent (tab baru, endpoint TERPISAH dari monitor-public) ──
let daActiveTab = 'status';
let daRunsPage = 1;
let daStatusTimer = null;

function daToken() {
  return document.getElementById('da-token').value || localStorage.getItem('da_token') || '';
}

function daAuthHeaders() {
  const t = daToken();
  return t ? { 'Authorization': 'Bearer ' + t, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' };
}

// ── Kelola Agent -- katalog semua agent AI (2026-07-22) ──
async function agRegLoad() {
  const msgEl = document.getElementById('agreg-msg');
  msgEl.style.color = '#60a5fa';
  msgEl.textContent = 'Memuat...';
  try {
    const r = await fetch(window.location.origin + '/api/v1/agent-registry', { headers: daAuthHeaders() });
    const j = await r.json();
    if (!r.ok) {
      msgEl.style.color = '#f87171';
      msgEl.textContent = 'Gagal: ' + (j.detail || j.message || 'isi token login dulu');
      return;
    }
    const agents = j.data.agents;
    document.getElementById('agreg-list').innerHTML = agents.map(a => `
      <div style="background:#1e293b;border-radius:8px;padding:12px 16px;margin-bottom:10px">
        <div style="margin-bottom:6px">
          <b>${a.agent_name}</b>
          <span class="pill" style="background:#1e3a5f;color:#60a5fa;margin-left:6px">${a.category}</span>
        </div>
        <div style="font-size:0.72rem;color:#94a3b8;margin-bottom:8px">${a.description || ''}</div>
        <table style="width:100%">
          <thead><tr><th>Key</th><th>Nilai</th><th>Model</th><th>Aksi</th></tr></thead>
          <tbody>
          ${a.keys.map(k => `
            <tr>
              <td style="font-size:0.78rem">${k.key_label}</td>
              <td style="font-family:monospace;font-size:0.75rem">${k.masked_value || '-'}</td>
              <td style="font-size:0.75rem">${k.model || '-'}</td>
              <td style="font-size:0.72rem;color:#64748b">
                ${k.editable_here
                  ? `<input type="password" id="agreg-edit-${k.id}" placeholder="Key baru..." style="width:110px;padding:4px 6px;background:#0f172a;border:1px solid #334155;border-radius:4px;color:#e2e8f0;font-size:0.7rem">
                     <button class="retry-btn" style="padding:4px 8px;font-size:0.7rem" onclick="agRegEditCustom('${k.id}')">Ganti</button>`
                  : (k.note || 'Lihat /manage-api-keys')}
              </td>
            </tr>
          `).join('')}
          </tbody>
        </table>
      </div>
    `).join('');
    msgEl.style.color = '#64748b';
    msgEl.textContent = 'Terakhir dimuat: ' + new Date().toLocaleTimeString('id-ID');
  } catch (e) {
    msgEl.style.color = '#f87171';
    msgEl.textContent = 'Gagal: ' + e.message;
  }
}

async function agRegEditCustom(id) {
  const input = document.getElementById('agreg-edit-' + id);
  const value = input.value.trim();
  if (!value) return;
  if (!daToken()) { alert('Isi token login (Bearer) dulu di tab Discovery Agent di bawah'); return; }
  try {
    const r = await fetch(window.location.origin + '/api/v1/agent-registry/' + id, {
      method: 'PATCH', headers: daAuthHeaders(), body: JSON.stringify({ api_key: value }),
    });
    const j = await r.json();
    if (!r.ok) { alert('Gagal: ' + (j.detail || j.message || 'unknown')); return; }
    input.value = '';
    agRegLoad();
  } catch (e) {
    alert('Gagal: ' + e.message);
  }
}

async function agRegAddNew() {
  const name = document.getElementById('agreg-new-name').value.trim();
  if (!name) { alert('Nama agent wajib diisi'); return; }
  if (!daToken()) { alert('Isi token login (Bearer) dulu di tab Discovery Agent di bawah'); return; }
  const body = {
    agent_name: name,
    category: document.getElementById('agreg-new-category').value.trim() || 'Umum',
    description: document.getElementById('agreg-new-desc').value.trim() || null,
    key_label: document.getElementById('agreg-new-keylabel').value.trim() || 'API Key',
    api_key: document.getElementById('agreg-new-apikey').value.trim() || null,
    model: document.getElementById('agreg-new-model').value.trim() || null,
  };
  const msgEl = document.getElementById('agreg-add-msg');
  try {
    const r = await fetch(window.location.origin + '/api/v1/agent-registry', {
      method: 'POST', headers: daAuthHeaders(), body: JSON.stringify(body),
    });
    const j = await r.json();
    if (!r.ok) {
      msgEl.style.color = '#f87171';
      msgEl.textContent = 'Gagal: ' + (j.detail || j.message || 'unknown');
      return;
    }
    msgEl.style.color = '#4ade80';
    msgEl.textContent = 'Agent ditambahkan!';
    ['name', 'category', 'desc', 'keylabel', 'apikey', 'model'].forEach(f => {
      document.getElementById('agreg-new-' + f).value = '';
    });
    agRegLoad();
  } catch (e) {
    msgEl.style.color = '#f87171';
    msgEl.textContent = 'Gagal: ' + e.message;
  }
}

// ── YouTube API Key -- Monitoring Kuota Semua Agent (2026-07-20) ──
const YK_STATUS_MAP = {
  ok:             ['OK',            '#4ade80'],
  quota_exceeded: ['Kuota Habis',   '#f87171'],
  rate_limited:   ['Rate Limited',  '#fbbf24'],
  invalid_key:    ['Key Invalid',   '#f87171'],
  forbidden:      ['Forbidden',     '#f87171'],
  not_set:        ['Belum Diisi',   '#64748b'],
  error:          ['Error',         '#fb923c'],
};

async function ykCheckNow() {
  const msgEl = document.getElementById('yk-msg');
  msgEl.style.color = '#60a5fa';
  msgEl.textContent = 'Mengecek ke Google...';
  try {
    const r = await fetch(window.location.origin + '/api/v1/youtube/api-keys/health', { headers: daAuthHeaders() });
    const j = await r.json();
    if (!r.ok) {
      msgEl.style.color = '#f87171';
      msgEl.textContent = 'Gagal: ' + (j.detail || j.message || 'isi token login dulu');
      return;
    }
    const items = j.data.items;
    document.getElementById('yk-tbody').innerHTML = items.map(it => {
      const [label, color] = YK_STATUS_MAP[it.status] || [it.status, '#94a3b8'];
      return `<tr>
        <td>${it.label}</td>
        <td style="font-family:monospace;font-size:0.75rem">${it.masked_key || '-'}</td>
        <td style="font-size:0.75rem;color:#94a3b8">${it.shared_with.length ? it.shared_with.join(', ') : '-'}</td>
        <td><span class="pill" style="background:${color}22;color:${color}">${label}</span></td>
        <td style="font-size:0.75rem;color:#94a3b8;max-width:260px">${it.detail}</td>
        <td>
          <input type="password" id="yk-new-${it.id}" placeholder="Key baru..." style="width:150px;padding:4px 6px;background:#0f172a;border:1px solid #334155;border-radius:4px;color:#e2e8f0;font-size:0.72rem">
          <button class="retry-btn" style="padding:4px 10px;font-size:0.72rem" onclick="ykSwap('${it.id}')">Ganti</button>
        </td>
      </tr>`;
    }).join('');
    msgEl.style.color = '#64748b';
    msgEl.textContent = 'Terakhir dicek: ' + new Date().toLocaleTimeString('id-ID');
  } catch (e) {
    msgEl.style.color = '#f87171';
    msgEl.textContent = 'Gagal: ' + e.message;
  }
}

async function ykSwap(id) {
  const input = document.getElementById('yk-new-' + id);
  const value = input.value.trim();
  if (!value) return;
  if (!daToken()) { alert('Isi token login (Bearer) dulu di tab Discovery Agent di atas'); return; }
  try {
    const r = await fetch(window.location.origin + '/api/v1/credentials/' + id, {
      method: 'PATCH',
      headers: daAuthHeaders(),
      body: JSON.stringify({ value }),
    });
    const j = await r.json();
    if (!r.ok) { alert('Gagal: ' + (j.detail || j.message || 'unknown')); return; }
    input.value = '';
    ykCheckNow();
  } catch (e) {
    alert('Gagal: ' + e.message);
  }
}

function daSwitchTab(name) {
  daActiveTab = name;
  ['status', 'runs', 'config'].forEach(n => {
    document.getElementById('da-panel-' + n).style.display = (n === name) ? '' : 'none';
    document.getElementById('da-tab-btn-' + n).classList.toggle('active', n === name);
  });
  if (name === 'status') daLoadStatus();
  if (name === 'runs') daLoadRuns(1);
  if (name === 'config') daLoadConfig();
}

async function daLoadStatus() {
  try {
    const r = await fetch(window.location.origin + '/api/v1/youtube/discovery-agent/status');
    const json = await r.json();
    const d = json.data || {};
    const banner = document.getElementById('da-status-banner');
    const dot = document.getElementById('da-dot');
    const text = document.getElementById('da-status-text');
    const lastRunAt = document.getElementById('da-last-run-at');
    const errBox = document.getElementById('da-error-box');

    if (d.is_running) {
      banner.className = 'ed-banner unknown';
      dot.className = 'alive-dot on';
      text.innerHTML = '<span style="color:#60a5fa">SEDANG MENCARI...</span>';
    } else if (d.last_run?.status === 'success') {
      banner.className = 'ed-banner active';
      dot.className = 'alive-dot on';
      text.innerHTML = '<span class="green">IDLE (run terakhir sukses)</span>';
    } else if (d.last_run?.status === 'failed') {
      banner.className = 'ed-banner expired';
      dot.className = 'alive-dot off';
      text.innerHTML = '<span class="red">IDLE (run terakhir GAGAL)</span>';
    } else {
      banner.className = 'ed-banner unknown';
      dot.className = 'alive-dot off';
      text.innerHTML = '<span style="color:#64748b">BELUM PERNAH JALAN</span>';
    }

    const lr = d.last_run;
    if (lr) {
      lastRunAt.textContent = 'Mulai: ' + new Date(lr.started_at).toLocaleString('id-ID');
      document.getElementById('da-topics-checked').textContent = lr.topics_checked;
      document.getElementById('da-candidates-found').textContent = lr.candidates_found;
      document.getElementById('da-candidates-validated').textContent = lr.candidates_validated;
      document.getElementById('da-candidates-rejected').textContent = lr.candidates_rejected;
      document.getElementById('da-posts-saved').textContent = lr.posts_saved;
      if (lr.error_message) {
        errBox.style.display = '';
        errBox.textContent = 'Error: ' + lr.error_message;
      } else {
        errBox.style.display = 'none';
      }
    }

    const topicsCovered = d.topics_covered || [];
    const tcBox = document.getElementById('da-topics-covered');
    if (topicsCovered.length === 0) {
      tcBox.innerHTML = '<span style="color:#64748b">Belum ada topik aktif yang mencakup YouTube.</span>';
    } else {
      tcBox.innerHTML = topicsCovered.map(t =>
        `<div style="margin-bottom:6px"><b>${daEsc(t.topic)}</b>: ${t.keywords.map(daEsc).join(', ') || '<span style="color:#64748b">(tanpa keyword)</span>'}</div>`
      ).join('');
    }
  } catch(e) { console.error(e); }
}

async function daLoadRuns(page) {
  daRunsPage = page;
  const tbody = document.getElementById('da-runs-tbody');
  tbody.innerHTML = '<tr><td colspan="9">Memuat...</td></tr>';
  try {
    const r = await fetch(window.location.origin + `/api/v1/youtube/discovery-agent/runs?page=${page}&limit=${PAGE_LIMIT}`);
    const json = await r.json();
    const items = json.data?.items || [];
    const pag = json.data?.pagination || {};

    if (items.length === 0) {
      tbody.innerHTML = '<tr><td colspan="9">Belum ada riwayat run.</td></tr>';
    } else {
      tbody.innerHTML = items.map(r => {
        const topicsInDetail = (r.details || []).filter(d => d.mode === 'topic').map(d => d.topic);
        const topicSummary = [...new Set(topicsInDetail)].slice(0, 3).join(', ') || (r.topics_checked > 0 ? `${r.topics_checked} topik` : '-');
        return `<tr>
          <td>${new Date(r.started_at).toLocaleString('id-ID')}</td>
          <td><span class="pill pill-${r.status}">${r.status}</span></td>
          <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${topicSummary}">${topicSummary}</td>
          <td>${r.candidates_found}</td>
          <td class="green">${r.candidates_validated}</td>
          <td class="yellow">${r.candidates_rejected}</td>
          <td class="green">${r.posts_saved}</td>
          <td style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${r.model_used || '-'}</td>
          <td><button class="page-btn" onclick='daShowDetails(${JSON.stringify(JSON.stringify(r.details || []))})'>Detail</button></td>
        </tr>`;
      }).join('');
    }

    renderPagination('da-runs-pagination', pag.page, pag.total_pages, pag.total, 'daLoadRuns');
  } catch(e) { console.error(e); tbody.innerHTML = '<tr><td colspan="9">Gagal memuat.</td></tr>'; }
}

function daEsc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function daShowDetails(detailsJsonStr) {
  const details = JSON.parse(detailsJsonStr);
  const body = document.getElementById('da-detail-modal-body');

  if (!details.length) {
    body.innerHTML = '<p style="color:#94a3b8">Tidak ada rincian kandidat utk run ini.</p>';
    document.getElementById('da-detail-modal').style.display = 'flex';
    return;
  }

  const validCount = details.filter(d => d.valid).length;
  const rejectedCount = details.length - validCount;

  // Ringkasan alasan ditolak (dedup) -- biar langsung ketahuan kalau SEMUA
  // gagal krn 1 sebab yg sama (mis. model LLM deprecated), bukan harus
  // scroll baca ratusan baris identik satu-satu.
  const reasonCounts = {};
  details.filter(d => !d.valid).forEach(d => {
    const r = (d.reason || '(tidak ada alasan)').slice(0, 200);
    reasonCounts[r] = (reasonCounts[r] || 0) + 1;
  });
  const reasonSummary = Object.entries(reasonCounts).sort((a, b) => b[1] - a[1]);

  let html = `<div style="font-size:0.8rem;color:#94a3b8;margin-bottom:10px">${details.length} kandidat diperiksa &mdash; <span class="green">${validCount} lolos</span>, <span class="yellow">${rejectedCount} ditolak</span></div>`;

  if (reasonSummary.length) {
    html += `<div style="margin-bottom:14px;padding:10px 12px;background:#0f172a;border-radius:8px">
      <div style="font-size:0.72rem;color:#94a3b8;margin-bottom:6px;text-transform:uppercase;letter-spacing:.05em">Ringkasan Alasan Ditolak</div>
      ${reasonSummary.map(([reason, count]) => `<div style="font-size:0.78rem;margin-bottom:4px"><span class="yellow">${count}x</span> &mdash; ${daEsc(reason)}</div>`).join('')}
    </div>`;
  }

  html += `<table><thead><tr><th>Status</th><th>Judul (klik utk buka di YouTube)</th><th>Topik/Mode</th></tr></thead><tbody>`;
  html += details.map(d => `<tr>
    <td><span class="pill pill-${d.valid ? 'success' : 'failed'}">${d.valid ? 'LOLOS' : 'DITOLAK'}</span></td>
    <td style="max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${daEsc(d.reason)}">
      ${d.video_id ? `<a href="https://youtube.com/watch?v=${daEsc(d.video_id)}" target="_blank" style="color:#e2e8f0">${daEsc(d.title) || daEsc(d.video_id)}</a>` : (daEsc(d.title) || '-')}
    </td>
    <td>${daEsc(d.mode) || '-'}${d.topic ? ' / ' + daEsc(d.topic) : ''}</td>
  </tr>`).join('');
  html += `</tbody></table>`;

  body.innerHTML = html;
  document.getElementById('da-detail-modal').style.display = 'flex';
}

function daCloseDetails() {
  document.getElementById('da-detail-modal').style.display = 'none';
}

async function daLoadConfig() {
  try {
    const rs = await fetch(window.location.origin + '/api/v1/youtube/discovery-agent/schedule');
    const js = await rs.json();
    const interval = js.data?.interval_hours;
    const allowed = js.data?.allowed_values || [1, 4, 8, 24];
    document.getElementById('da-interval-btns').innerHTML = allowed.map(h =>
      `<button class="page-btn ${h === interval ? 'active' : ''}" style="${h === interval ? 'background:#1d4ed8;color:#fff' : ''}" onclick="daSetInterval(${h})">${h} jam</button>`
    ).join('');
  } catch(e) { console.error(e); }

  const savedToken = localStorage.getItem('da_token');
  if (savedToken) document.getElementById('da-token').value = savedToken;

  if (daToken()) {
    try {
      const rc = await fetch(window.location.origin + '/api/v1/youtube/discovery-agent/config', { headers: daAuthHeaders() });
      const jc = await rc.json();
      if (rc.ok) {
        document.getElementById('da-model-input').placeholder = jc.data?.model || '';
        document.getElementById('da-key-current').textContent = jc.data?.api_key_set ? `(saat ini: ${jc.data.api_key_masked})` : '(belum diatur)';
      }
    } catch(e) { console.error(e); }
  }
}

async function daSetInterval(hours) {
  const msg = document.getElementById('da-config-msg');
  try {
    const r = await fetch(window.location.origin + '/api/v1/youtube/discovery-agent/schedule', {
      method: 'PATCH', headers: daAuthHeaders(), body: JSON.stringify({ interval_hours: hours }),
    });
    const json = await r.json();
    if (r.ok) {
      msg.style.color = '#4ade80';
      msg.textContent = `Interval diubah ke ${hours} jam.`;
      daLoadConfig();
    } else {
      msg.style.color = '#f87171';
      msg.textContent = json.error?.message || json.detail || 'Gagal (butuh token valid?).';
    }
  } catch(e) { msg.style.color = '#f87171'; msg.textContent = 'Gagal terhubung.'; }
}

async function daSaveConfig() {
  const msg = document.getElementById('da-config-msg');
  const model = document.getElementById('da-model-input').value.trim();
  const apiKey = document.getElementById('da-apikey-input').value.trim();
  const token = document.getElementById('da-token').value.trim();
  if (token) localStorage.setItem('da_token', token);

  if (!model && !apiKey) { msg.style.color = '#fbbf24'; msg.textContent = 'Isi model atau API key dulu.'; return; }

  try {
    const body = {};
    if (model) body.model = model;
    if (apiKey) body.api_key = apiKey;
    const r = await fetch(window.location.origin + '/api/v1/youtube/discovery-agent/config', {
      method: 'PATCH', headers: daAuthHeaders(), body: JSON.stringify(body),
    });
    const json = await r.json();
    if (r.ok) {
      msg.style.color = '#4ade80';
      msg.textContent = 'Tersimpan.';
      document.getElementById('da-apikey-input').value = '';
      daLoadConfig();
    } else {
      msg.style.color = '#f87171';
      msg.textContent = json.error?.message || json.detail || 'Gagal (butuh token valid?).';
    }
  } catch(e) { msg.style.color = '#f87171'; msg.textContent = 'Gagal terhubung.'; }
}

daLoadStatus();
daStatusTimer = setInterval(() => { if (daActiveTab === 'status') daLoadStatus(); }, 15000);

// ── YouTube Discovery Agent 2 (AGENT TERPISAH dari Agent 1 di atas -- key
//    YouTube+OpenRouter SENDIRI, HANYA topic-guided, reuse token da-token) ──
let da2ActiveTab = 'status';
let da2RunsPage = 1;
let da2StatusTimer = null;
let da2EnabledState = null;

function da2SwitchTab(name) {
  da2ActiveTab = name;
  ['status', 'runs', 'config'].forEach(n => {
    document.getElementById('da2-panel-' + n).style.display = (n === name) ? '' : 'none';
    document.getElementById('da2-tab-btn-' + n).classList.toggle('active', n === name);
  });
  if (name === 'status') da2LoadStatus();
  if (name === 'runs') da2LoadRuns(1);
  if (name === 'config') da2LoadConfig();
}

async function da2LoadStatus() {
  try {
    const r = await fetch(window.location.origin + '/api/v1/youtube/discovery-agent-2/status');
    const json = await r.json();
    const d = json.data || {};
    const banner = document.getElementById('da2-status-banner');
    const dot = document.getElementById('da2-dot');
    const text = document.getElementById('da2-status-text');
    const lastRunAt = document.getElementById('da2-last-run-at');
    const errBox = document.getElementById('da2-error-box');

    if (d.enabled === false) {
      banner.className = 'ed-banner expired';
      dot.className = 'alive-dot off';
      text.innerHTML = '<span style="color:#64748b">DIMATIKAN (tombol OFF)</span>';
    } else if (d.is_running) {
      banner.className = 'ed-banner unknown';
      dot.className = 'alive-dot on';
      text.innerHTML = '<span style="color:#60a5fa">SEDANG MENCARI...</span>';
    } else if (d.last_run?.status === 'success') {
      banner.className = 'ed-banner active';
      dot.className = 'alive-dot on';
      text.innerHTML = '<span class="green">IDLE (run terakhir sukses)</span>';
    } else if (d.last_run?.status === 'failed') {
      banner.className = 'ed-banner expired';
      dot.className = 'alive-dot off';
      text.innerHTML = '<span class="red">IDLE (run terakhir GAGAL)</span>';
    } else {
      banner.className = 'ed-banner unknown';
      dot.className = 'alive-dot off';
      text.innerHTML = '<span style="color:#64748b">BELUM PERNAH JALAN</span>';
    }

    const lr = d.last_run;
    if (lr) {
      lastRunAt.textContent = 'Mulai: ' + new Date(lr.started_at).toLocaleString('id-ID');
      document.getElementById('da2-topics-checked').textContent = lr.topics_checked;
      document.getElementById('da2-candidates-found').textContent = lr.candidates_found;
      document.getElementById('da2-candidates-validated').textContent = lr.candidates_validated;
      document.getElementById('da2-candidates-rejected').textContent = lr.candidates_rejected;
      document.getElementById('da2-posts-saved').textContent = lr.posts_saved;
      if (lr.error_message) {
        errBox.style.display = '';
        errBox.textContent = 'Error: ' + lr.error_message;
      } else {
        errBox.style.display = 'none';
      }
    }

    // ── Banner kuota YouTube Data API v3 milik Agent 2 (key terpisah) ──────
    const da2q = d.youtube_api_quota || {};
    const da2qBanner = document.getElementById('da2q-banner');
    const da2qDot = document.getElementById('da2q-dot');
    const da2qText = document.getElementById('da2q-status-text');
    const da2qDetail = document.getElementById('da2q-status-detail');
    const da2qLastErr = document.getElementById('da2q-last-err');
    const da2qLastOk = document.getElementById('da2q-last-ok');
    da2qBanner.className = `ed-banner ${da2q.status || 'unknown'}`;
    if (da2q.status === 'active') {
      da2qDot.className = 'alive-dot on';
      da2qText.innerHTML = '<span class="green">AKTIF</span>';
      da2qDetail.textContent = da2q.message || '';
    } else if (da2q.status === 'expired') {
      da2qDot.className = 'alive-dot off';
      da2qText.innerHTML = '<span class="red">QUOTA HABIS</span>';
      da2qDetail.textContent = da2q.message || '';
    } else {
      da2qDot.className = 'alive-dot off';
      da2qText.innerHTML = '<span style="color:#64748b">UNKNOWN</span>';
      da2qDetail.textContent = da2q.message || 'Belum ada data';
    }
    da2qLastErr.textContent = da2q.last_error_at ? `Error terakhir: ${fmt(da2q.last_error_at)}` : '';
    da2qLastOk.textContent = da2q.last_success_at ? `Sukses terakhir: ${fmt(da2q.last_success_at)}` : '';

    const topicsCovered = d.topics_covered || [];
    const tcBox = document.getElementById('da2-topics-covered');
    if (topicsCovered.length === 0) {
      tcBox.innerHTML = '<span style="color:#64748b">Belum ada topik aktif yang mencakup YouTube.</span>';
    } else {
      tcBox.innerHTML = topicsCovered.map(t =>
        `<div style="margin-bottom:6px"><b>${daEsc(t.topic)}</b>: ${t.keywords.map(daEsc).join(', ') || '<span style="color:#64748b">(tanpa keyword)</span>'}</div>`
      ).join('');
    }
  } catch(e) { console.error(e); }
}

async function da2LoadRuns(page) {
  da2RunsPage = page;
  const tbody = document.getElementById('da2-runs-tbody');
  tbody.innerHTML = '<tr><td colspan="9">Memuat...</td></tr>';
  try {
    const r = await fetch(window.location.origin + `/api/v1/youtube/discovery-agent-2/runs?page=${page}&limit=${PAGE_LIMIT}`);
    const json = await r.json();
    const items = json.data?.items || [];
    const pag = json.data?.pagination || {};

    if (items.length === 0) {
      tbody.innerHTML = '<tr><td colspan="9">Belum ada riwayat run.</td></tr>';
    } else {
      tbody.innerHTML = items.map(r => {
        const topicsInDetail = (r.details || []).filter(d => d.mode === 'topic').map(d => d.topic);
        const topicSummary = [...new Set(topicsInDetail)].slice(0, 3).join(', ') || (r.topics_checked > 0 ? `${r.topics_checked} topik` : '-');
        return `<tr>
          <td>${new Date(r.started_at).toLocaleString('id-ID')}</td>
          <td><span class="pill pill-${r.status}">${r.status}</span></td>
          <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${topicSummary}">${topicSummary}</td>
          <td>${r.candidates_found}</td>
          <td class="green">${r.candidates_validated}</td>
          <td class="yellow">${r.candidates_rejected}</td>
          <td class="green">${r.posts_saved}</td>
          <td style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${r.model_used || '-'}</td>
          <td><button class="page-btn" onclick='daShowDetails(${JSON.stringify(JSON.stringify(r.details || []))})'>Detail</button></td>
        </tr>`;
      }).join('');
    }

    renderPagination('da2-runs-pagination', pag.page, pag.total_pages, pag.total, 'da2LoadRuns');
  } catch(e) { console.error(e); tbody.innerHTML = '<tr><td colspan="9">Gagal memuat.</td></tr>'; }
}

async function da2LoadConfig() {
  try {
    const rs = await fetch(window.location.origin + '/api/v1/youtube/discovery-agent-2/schedule');
    const js = await rs.json();
    const interval = js.data?.interval_hours;
    const allowed = js.data?.allowed_values || [1, 4, 8, 12];
    document.getElementById('da2-interval-btns').innerHTML = allowed.map(h =>
      `<button class="page-btn ${h === interval ? 'active' : ''}" style="${h === interval ? 'background:#1d4ed8;color:#fff' : ''}" onclick="da2SetInterval(${h})">${h} jam</button>`
    ).join('');
  } catch(e) { console.error(e); }

  const savedToken = localStorage.getItem('da_token');
  if (savedToken) document.getElementById('da-token').value = savedToken;

  if (daToken()) {
    try {
      const rc = await fetch(window.location.origin + '/api/v1/youtube/discovery-agent-2/config', { headers: daAuthHeaders() });
      const jc = await rc.json();
      if (rc.ok) {
        document.getElementById('da2-model-input').placeholder = jc.data?.model || '';
        document.getElementById('da2-key-current').textContent = jc.data?.api_key_set ? `(saat ini: ${jc.data.api_key_masked})` : '(belum diatur)';
        document.getElementById('da2-ytkey-current').textContent = jc.data?.youtube_api_key_set ? `(saat ini: ${jc.data.youtube_api_key_masked})` : '(belum diatur)';
        da2EnabledState = jc.data?.enabled;
        da2RenderEnabledBtn();
      }
    } catch(e) { console.error(e); }
  }
}

function da2RenderEnabledBtn() {
  const btn = document.getElementById('da2-enabled-btn');
  if (da2EnabledState === null) { btn.textContent = 'Login dulu'; return; }
  if (da2EnabledState) {
    btn.textContent = 'AKTIF -- klik utk matikan';
    btn.style.background = '#166534'; btn.style.color = '#fff';
  } else {
    btn.textContent = 'MATI -- klik utk nyalakan';
    btn.style.background = '#7f1d1d'; btn.style.color = '#fff';
  }
}

async function da2ToggleEnabled() {
  if (!daToken()) { alert('Isi token dulu di tab Pengaturan Discovery Agent (atas).'); return; }
  const msg = document.getElementById('da2-config-msg');
  const next = !da2EnabledState;
  try {
    const r = await fetch(window.location.origin + '/api/v1/youtube/discovery-agent-2/config', {
      method: 'PATCH', headers: daAuthHeaders(), body: JSON.stringify({ enabled: next }),
    });
    const json = await r.json();
    if (r.ok) {
      da2EnabledState = json.data?.enabled ?? next;
      da2RenderEnabledBtn();
      msg.style.color = '#4ade80';
      msg.textContent = da2EnabledState ? 'Agent 2 diaktifkan.' : 'Agent 2 dimatikan.';
    } else {
      msg.style.color = '#f87171';
      msg.textContent = json.error?.message || json.detail || 'Gagal (butuh token valid?).';
    }
  } catch(e) { msg.style.color = '#f87171'; msg.textContent = 'Gagal terhubung.'; }
}

async function da2SetInterval(hours) {
  const msg = document.getElementById('da2-config-msg');
  try {
    const r = await fetch(window.location.origin + '/api/v1/youtube/discovery-agent-2/schedule', {
      method: 'PATCH', headers: daAuthHeaders(), body: JSON.stringify({ interval_hours: hours }),
    });
    const json = await r.json();
    if (r.ok) {
      msg.style.color = '#4ade80';
      msg.textContent = `Interval diubah ke ${hours} jam.`;
      da2LoadConfig();
    } else {
      msg.style.color = '#f87171';
      msg.textContent = json.error?.message || json.detail || 'Gagal (butuh token valid?).';
    }
  } catch(e) { msg.style.color = '#f87171'; msg.textContent = 'Gagal terhubung.'; }
}

async function da2SaveConfig() {
  const msg = document.getElementById('da2-config-msg');
  const model = document.getElementById('da2-model-input').value.trim();
  const apiKey = document.getElementById('da2-apikey-input').value.trim();
  const ytKey = document.getElementById('da2-ytkey-input').value.trim();
  const token = document.getElementById('da-token').value.trim();
  if (token) localStorage.setItem('da_token', token);

  if (!model && !apiKey && !ytKey) { msg.style.color = '#fbbf24'; msg.textContent = 'Isi model, API key, atau YouTube key dulu.'; return; }

  try {
    const body = {};
    if (model) body.model = model;
    if (apiKey) body.api_key = apiKey;
    if (ytKey) body.youtube_api_key = ytKey;
    const r = await fetch(window.location.origin + '/api/v1/youtube/discovery-agent-2/config', {
      method: 'PATCH', headers: daAuthHeaders(), body: JSON.stringify(body),
    });
    const json = await r.json();
    if (r.ok) {
      msg.style.color = '#4ade80';
      msg.textContent = 'Tersimpan.';
      document.getElementById('da2-apikey-input').value = '';
      document.getElementById('da2-ytkey-input').value = '';
      da2LoadConfig();
    } else {
      msg.style.color = '#f87171';
      msg.textContent = json.error?.message || json.detail || 'Gagal (butuh token valid?).';
    }
  } catch(e) { msg.style.color = '#f87171'; msg.textContent = 'Gagal terhubung.'; }
}

da2LoadStatus();
da2StatusTimer = setInterval(() => { if (da2ActiveTab === 'status') da2LoadStatus(); }, 15000);

// ── YouTube Metadata Agent (tab terpisah, endpoint TERPISAH -- reuse token dari Discovery Agent) ──
let maActiveTab = 'status';
let maStatusTimer = null;

function maSwitchTab(name) {
  maActiveTab = name;
  ['status', 'history', 'config'].forEach(n => {
    document.getElementById('ma-panel-' + n).style.display = (n === name) ? '' : 'none';
    document.getElementById('ma-tab-btn-' + n).classList.toggle('active', n === name);
  });
  if (name === 'status') maLoadStatus();
  if (name === 'history') maLoadHistory(1);
  if (name === 'config') maLoadConfig();
}

async function maLoadStatus() {
  try {
    const r = await fetch(window.location.origin + '/api/v1/youtube/metadata-agent/status');
    const json = await r.json();
    const d = json.data || {};
    const banner = document.getElementById('ma-status-banner');
    const dot = document.getElementById('ma-dot');
    const text = document.getElementById('ma-status-text');

    if (d.is_running) {
      banner.className = 'ed-banner unknown';
      dot.className = 'alive-dot on';
      text.innerHTML = '<span style="color:#60a5fa">SEDANG MELENGKAPI DATA...</span>';
    } else if (d.total_enriched > 0) {
      banner.className = 'ed-banner active';
      dot.className = 'alive-dot on';
      text.innerHTML = '<span class="green">IDLE</span>';
    } else {
      banner.className = 'ed-banner unknown';
      dot.className = 'alive-dot off';
      text.innerHTML = '<span style="color:#64748b">BELUM PERNAH JALAN</span>';
    }
    document.getElementById('ma-last-run-at').textContent = d.last_run_at ? ('Terakhir: ' + new Date(d.last_run_at).toLocaleString('id-ID')) : '-';
    document.getElementById('ma-pending').textContent = (d.pending_enrichment ?? 0).toLocaleString('id-ID');
    document.getElementById('ma-total-enriched').textContent = (d.total_enriched ?? 0).toLocaleString('id-ID');
    document.getElementById('ma-pending-refresh').textContent = (d.pending_refresh ?? 0).toLocaleString('id-ID');
    document.getElementById('ma-refresh-age-label').textContent = (d.refresh_age_hours ?? 6) + ' jam';
    document.getElementById('ma-title-mismatch-count').textContent = (d.title_mismatch_count ?? 0).toLocaleString('id-ID');
  } catch(e) { console.error(e); }
}

async function maLoadHistory(page) {
  const tbody = document.getElementById('ma-history-tbody');
  tbody.innerHTML = '<tr><td colspan="6">Memuat...</td></tr>';
  const mismatchOnly = document.getElementById('ma-history-mismatch-only').checked;
  try {
    const r = await fetch(window.location.origin + `/api/v1/youtube/metadata-agent/history?page=${page}&limit=${PAGE_LIMIT}&only_title_mismatch=${mismatchOnly}`);
    const json = await r.json();
    const items = json.data?.items || [];
    const pag = json.data?.pagination || {};

    if (items.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6">${mismatchOnly ? 'Tidak ada judul yang berubah.' : 'Belum ada video yang ter-enrich.'}</td></tr>`;
    } else {
      tbody.innerHTML = items.map(it => `<tr${it.title_mismatch ? ' style="background:#450a0a"' : ''}>
        <td>${new Date(it.fetched_at).toLocaleString('id-ID')}</td>
        <td style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
          <a href="${it.url}" target="_blank" style="color:#e2e8f0">${it.title || it.video_id}</a>
          ${it.title_mismatch ? `<div class="red" style="font-size:0.7rem" title="Judul asli YouTube saat terakhir dicek: ${it.title_live || ''}">&#9888; judul berubah -&gt; ${(it.title_live || '').slice(0, 50)}</div>` : ''}
        </td>
        <td>${it.channel_name || '-'}</td>
        <td>${(it.views ?? 0).toLocaleString('id-ID')}</td>
        <td>${it.keyword_matched || '-'}</td>
        <td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${it.viral_context || ''}">${it.viral_context ? it.viral_context.slice(0, 60) + '...' : '-'}</td>
      </tr>`).join('');
    }
    renderPagination('ma-history-pagination', pag.page, pag.total_pages, pag.total, 'maLoadHistory');
  } catch(e) { console.error(e); tbody.innerHTML = '<tr><td colspan="6">Gagal memuat.</td></tr>'; }
}

async function maLoadConfig() {
  try {
    const rs = await fetch(window.location.origin + '/api/v1/youtube/metadata-agent/schedule');
    const js = await rs.json();
    const interval = js.data?.interval_minutes;
    const allowed = js.data?.allowed_values || [15, 30, 60, 240];
    document.getElementById('ma-interval-btns').innerHTML = allowed.map(m =>
      `<button class="page-btn ${m === interval ? 'active' : ''}" style="${m === interval ? 'background:#1d4ed8;color:#fff' : ''}" onclick="maSetInterval(${m})">${m} mnt</button>`
    ).join('');
  } catch(e) { console.error(e); }

  if (daToken()) {
    try {
      const rc = await fetch(window.location.origin + '/api/v1/youtube/metadata-agent/config', { headers: daAuthHeaders() });
      const jc = await rc.json();
      if (rc.ok) {
        document.getElementById('ma-model-input').placeholder = jc.data?.model || '';
        document.getElementById('ma-key-current').textContent = jc.data?.api_key_set ? `(saat ini: ${jc.data.api_key_masked})` : '(belum diatur)';

        const rah = jc.data?.refresh_age_hours;
        const allowedAge = jc.data?.allowed_refresh_age_hours || [1, 3, 6, 12, 24];
        document.getElementById('ma-refresh-age-btns').innerHTML = allowedAge.map(h =>
          `<button class="page-btn ${h === rah ? 'active' : ''}" style="${h === rah ? 'background:#1d4ed8;color:#fff' : ''}" onclick="maSetRefreshAgeHours(${h})">${h} jam</button>`
        ).join('');

        const rbs = jc.data?.refresh_batch_size;
        const allowedBatch = jc.data?.allowed_refresh_batch_size || [10, 20, 50, 100];
        document.getElementById('ma-refresh-batch-btns').innerHTML = allowedBatch.map(s =>
          `<button class="page-btn ${s === rbs ? 'active' : ''}" style="${s === rbs ? 'background:#1d4ed8;color:#fff' : ''}" onclick="maSetRefreshBatchSize(${s})">${s}</button>`
        ).join('');

        const ebs = jc.data?.enrich_batch_size;
        const allowedEnrichBatch = jc.data?.allowed_enrich_batch_size || [10, 20, 50, 100];
        document.getElementById('ma-enrich-batch-btns').innerHTML = allowedEnrichBatch.map(s =>
          `<button class="page-btn ${s === ebs ? 'active' : ''}" style="${s === ebs ? 'background:#1d4ed8;color:#fff' : ''}" onclick="maSetEnrichBatchSize(${s})">${s}</button>`
        ).join('');
      }
    } catch(e) { console.error(e); }
  } else {
    document.getElementById('ma-refresh-age-btns').innerHTML = '<span style="color:#64748b;font-size:0.78rem">Login dulu (paste token di atas) utk mengatur.</span>';
    document.getElementById('ma-refresh-batch-btns').innerHTML = '<span style="color:#64748b;font-size:0.78rem">Login dulu (paste token di atas) utk mengatur.</span>';
    document.getElementById('ma-enrich-batch-btns').innerHTML = '<span style="color:#64748b;font-size:0.78rem">Login dulu (paste token di atas) utk mengatur.</span>';
  }
}

async function maSetInterval(minutes) {
  const msg = document.getElementById('ma-config-msg');
  try {
    const r = await fetch(window.location.origin + '/api/v1/youtube/metadata-agent/schedule', {
      method: 'PATCH', headers: daAuthHeaders(), body: JSON.stringify({ interval_minutes: minutes }),
    });
    const json = await r.json();
    if (r.ok) {
      msg.style.color = '#4ade80';
      msg.textContent = `Interval diubah ke ${minutes} menit.`;
      maLoadConfig();
    } else {
      msg.style.color = '#f87171';
      msg.textContent = json.error?.message || json.detail || 'Gagal (butuh token valid?).';
    }
  } catch(e) { msg.style.color = '#f87171'; msg.textContent = 'Gagal terhubung.'; }
}

async function maSetRefreshAgeHours(hours) {
  const msg = document.getElementById('ma-config-msg');
  try {
    const r = await fetch(window.location.origin + '/api/v1/youtube/metadata-agent/config', {
      method: 'PATCH', headers: daAuthHeaders(), body: JSON.stringify({ refresh_age_hours: hours }),
    });
    const json = await r.json();
    if (r.ok) {
      msg.style.color = '#4ade80';
      msg.textContent = `Ambang refresh diubah ke ${hours} jam.`;
      maLoadConfig();
    } else {
      msg.style.color = '#f87171';
      msg.textContent = json.error?.message || json.detail || 'Gagal (butuh token valid?).';
    }
  } catch(e) { msg.style.color = '#f87171'; msg.textContent = 'Gagal terhubung.'; }
}

async function maSetRefreshBatchSize(size) {
  const msg = document.getElementById('ma-config-msg');
  try {
    const r = await fetch(window.location.origin + '/api/v1/youtube/metadata-agent/config', {
      method: 'PATCH', headers: daAuthHeaders(), body: JSON.stringify({ refresh_batch_size: size }),
    });
    const json = await r.json();
    if (r.ok) {
      msg.style.color = '#4ade80';
      msg.textContent = `Batch refresh diubah ke ${size} video/run.`;
      maLoadConfig();
    } else {
      msg.style.color = '#f87171';
      msg.textContent = json.error?.message || json.detail || 'Gagal (butuh token valid?).';
    }
  } catch(e) { msg.style.color = '#f87171'; msg.textContent = 'Gagal terhubung.'; }
}

async function maSetEnrichBatchSize(size) {
  const msg = document.getElementById('ma-config-msg');
  try {
    const r = await fetch(window.location.origin + '/api/v1/youtube/metadata-agent/config', {
      method: 'PATCH', headers: daAuthHeaders(), body: JSON.stringify({ enrich_batch_size: size }),
    });
    const json = await r.json();
    if (r.ok) {
      msg.style.color = '#4ade80';
      msg.textContent = `Batch enrich diubah ke ${size} post baru/run.`;
      maLoadConfig();
    } else {
      msg.style.color = '#f87171';
      msg.textContent = json.error?.message || json.detail || 'Gagal (butuh token valid?).';
    }
  } catch(e) { msg.style.color = '#f87171'; msg.textContent = 'Gagal terhubung.'; }
}

async function maSaveConfig() {
  const msg = document.getElementById('ma-config-msg');
  const model = document.getElementById('ma-model-input').value.trim();
  const apiKey = document.getElementById('ma-apikey-input').value.trim();

  if (!model && !apiKey) { msg.style.color = '#fbbf24'; msg.textContent = 'Isi model atau API key dulu.'; return; }

  try {
    const body = {};
    if (model) body.model = model;
    if (apiKey) body.api_key = apiKey;
    const r = await fetch(window.location.origin + '/api/v1/youtube/metadata-agent/config', {
      method: 'PATCH', headers: daAuthHeaders(), body: JSON.stringify(body),
    });
    const json = await r.json();
    if (r.ok) {
      msg.style.color = '#4ade80';
      msg.textContent = 'Tersimpan.';
      document.getElementById('ma-apikey-input').value = '';
      maLoadConfig();
    } else {
      msg.style.color = '#f87171';
      msg.textContent = json.error?.message || json.detail || 'Gagal (butuh token valid?).';
    }
  } catch(e) { msg.style.color = '#f87171'; msg.textContent = 'Gagal terhubung.'; }
}

maLoadStatus();
maStatusTimer = setInterval(() => { if (maActiveTab === 'status') maLoadStatus(); }, 15000);

// ── Sentiment Agent (tab terpisah, endpoint TERPISAH -- reuse token dari Discovery Agent) ──
let saActiveTab = 'status';
let saStatusTimer = null;

function saSwitchTab(name) {
  saActiveTab = name;
  ['status', 'history', 'config'].forEach(n => {
    document.getElementById('sa-panel-' + n).style.display = (n === name) ? '' : 'none';
    document.getElementById('sa-tab-btn-' + n).classList.toggle('active', n === name);
  });
  if (name === 'status') saLoadStatus();
  if (name === 'history') saLoadHistory(1);
  if (name === 'config') { saLoadConfig(); saLoadSuggestions(); }
}

async function saLoadStatus() {
  try {
    const r = await fetch(window.location.origin + '/api/v1/youtube/sentiment-agent/status');
    const json = await r.json();
    const d = json.data || {};
    const banner = document.getElementById('sa-status-banner');
    const dot = document.getElementById('sa-dot');
    const text = document.getElementById('sa-status-text');

    if (d.is_running) {
      banner.className = 'ed-banner unknown';
      dot.className = 'alive-dot on';
      text.innerHTML = '<span style="color:#60a5fa">SEDANG MEREVIEW...</span>';
    } else if (d.total_reviewed > 0) {
      banner.className = 'ed-banner active';
      dot.className = 'alive-dot on';
      text.innerHTML = '<span class="green">IDLE</span>';
    } else {
      banner.className = 'ed-banner unknown';
      dot.className = 'alive-dot off';
      text.innerHTML = '<span style="color:#64748b">BELUM PERNAH JALAN</span>';
    }
    document.getElementById('sa-last-run-at').textContent = d.last_run_at ? ('Terakhir: ' + new Date(d.last_run_at).toLocaleString('id-ID')) : '-';
    document.getElementById('sa-pending').textContent = (d.pending_review ?? 0).toLocaleString('id-ID');
    document.getElementById('sa-reviewed-by-llm').textContent = (d.reviewed_by_llm ?? 0).toLocaleString('id-ID');
    document.getElementById('sa-agreements').textContent = (d.agreements ?? 0).toLocaleString('id-ID');
    document.getElementById('sa-disagreements').textContent = (d.disagreements ?? 0).toLocaleString('id-ID');
  } catch(e) { console.error(e); }
}

async function saLoadHistory(page) {
  const tbody = document.getElementById('sa-history-tbody');
  tbody.innerHTML = '<tr><td colspan="6">Memuat...</td></tr>';
  const disagreementOnly = document.getElementById('sa-history-disagreement-only').checked;
  try {
    const r = await fetch(window.location.origin + `/api/v1/youtube/sentiment-agent/history?page=${page}&limit=${PAGE_LIMIT}&only_disagreement=${disagreementOnly}`);
    const json = await r.json();
    const items = json.data?.items || [];
    const pag = json.data?.pagination || {};

    if (items.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6">${disagreementOnly ? 'Tidak ada yang beda pendapat.' : 'Belum ada komentar yang direview.'}</td></tr>`;
    } else {
      tbody.innerHTML = items.map(it => `<tr${it.agreement === false ? ' style="background:#450a0a"' : ''}>
        <td>${new Date(it.checked_at).toLocaleString('id-ID')}</td>
        <td style="max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${daEsc(it.content)}">${daEsc(it.content)}</td>
        <td>${daEsc(it.detected_language) || '-'}</td>
        <td>${daEsc(it.lexicon_label)}</td>
        <td>${daEsc(it.llm_label) || '-'}</td>
        <td>${it.agreement === true ? '<span class="green">Ya</span>' : (it.agreement === false ? '<span class="red">Tidak</span>' : '-')}</td>
      </tr>`).join('');
    }
    renderPagination('sa-history-pagination', pag.page, pag.total_pages, pag.total, 'saLoadHistory');
  } catch(e) { console.error(e); tbody.innerHTML = '<tr><td colspan="6">Gagal memuat.</td></tr>'; }
}

async function saLoadConfig() {
  try {
    const rs = await fetch(window.location.origin + '/api/v1/youtube/sentiment-agent/schedule');
    const js = await rs.json();
    const interval = js.data?.interval_minutes;
    const allowed = js.data?.allowed_values || [15, 30, 60, 240];
    document.getElementById('sa-interval-btns').innerHTML = allowed.map(m =>
      `<button class="page-btn ${m === interval ? 'active' : ''}" style="${m === interval ? 'background:#1d4ed8;color:#fff' : ''}" onclick="saSetInterval(${m})">${m} mnt</button>`
    ).join('');
  } catch(e) { console.error(e); }

  if (daToken()) {
    try {
      const rc = await fetch(window.location.origin + '/api/v1/youtube/sentiment-agent/config', { headers: daAuthHeaders() });
      const jc = await rc.json();
      if (rc.ok) {
        document.getElementById('sa-model-input').placeholder = jc.data?.model || '';
        document.getElementById('sa-key-current').textContent = jc.data?.api_key_set ? `(saat ini: ${jc.data.api_key_masked})` : '(belum diatur)';
        document.getElementById('sa-tb-model-input').placeholder = jc.data?.tiebreaker_model || '';
        document.getElementById('sa-tb-key-current').textContent = jc.data?.tiebreaker_api_key_set ? `(saat ini: ${jc.data.tiebreaker_api_key_masked})` : '(belum diatur)';

        const bs = jc.data?.batch_size;
        const allowedBatch = jc.data?.allowed_batch_size || [10, 20, 50, 100];
        document.getElementById('sa-batch-btns').innerHTML = allowedBatch.map(s =>
          `<button class="page-btn ${s === bs ? 'active' : ''}" style="${s === bs ? 'background:#1d4ed8;color:#fff' : ''}" onclick="saSetBatchSize(${s})">${s}</button>`
        ).join('');
      }
    } catch(e) { console.error(e); }
  } else {
    document.getElementById('sa-batch-btns').innerHTML = '<span style="color:#64748b;font-size:0.78rem">Login dulu (paste token di atas) utk mengatur.</span>';
  }
}

async function saSetInterval(minutes) {
  const msg = document.getElementById('sa-config-msg');
  try {
    const r = await fetch(window.location.origin + '/api/v1/youtube/sentiment-agent/schedule', {
      method: 'PATCH', headers: daAuthHeaders(), body: JSON.stringify({ interval_minutes: minutes }),
    });
    const json = await r.json();
    if (r.ok) {
      msg.style.color = '#4ade80';
      msg.textContent = `Interval diubah ke ${minutes} menit.`;
      saLoadConfig();
    } else {
      msg.style.color = '#f87171';
      msg.textContent = json.error?.message || json.detail || 'Gagal (butuh token valid?).';
    }
  } catch(e) { msg.style.color = '#f87171'; msg.textContent = 'Gagal terhubung.'; }
}

async function saSetBatchSize(size) {
  const msg = document.getElementById('sa-config-msg');
  try {
    const r = await fetch(window.location.origin + '/api/v1/youtube/sentiment-agent/config', {
      method: 'PATCH', headers: daAuthHeaders(), body: JSON.stringify({ batch_size: size }),
    });
    const json = await r.json();
    if (r.ok) {
      msg.style.color = '#4ade80';
      msg.textContent = `Batch diubah ke ${size} komentar/run.`;
      saLoadConfig();
    } else {
      msg.style.color = '#f87171';
      msg.textContent = json.error?.message || json.detail || 'Gagal (butuh token valid?).';
    }
  } catch(e) { msg.style.color = '#f87171'; msg.textContent = 'Gagal terhubung.'; }
}

async function saSaveConfig() {
  const msg = document.getElementById('sa-config-msg');
  const model = document.getElementById('sa-model-input').value.trim();
  const apiKey = document.getElementById('sa-apikey-input').value.trim();
  const tbModel = document.getElementById('sa-tb-model-input').value.trim();
  const tbApiKey = document.getElementById('sa-tb-apikey-input').value.trim();

  if (!model && !apiKey && !tbModel && !tbApiKey) { msg.style.color = '#fbbf24'; msg.textContent = 'Isi minimal salah satu kolom dulu.'; return; }

  try {
    const body = {};
    if (model) body.model = model;
    if (apiKey) body.api_key = apiKey;
    if (tbModel) body.tiebreaker_model = tbModel;
    if (tbApiKey) body.tiebreaker_api_key = tbApiKey;
    const r = await fetch(window.location.origin + '/api/v1/youtube/sentiment-agent/config', {
      method: 'PATCH', headers: daAuthHeaders(), body: JSON.stringify(body),
    });
    const json = await r.json();
    if (r.ok) {
      msg.style.color = '#4ade80';
      msg.textContent = 'Tersimpan.';
      document.getElementById('sa-apikey-input').value = '';
      document.getElementById('sa-tb-apikey-input').value = '';
      saLoadConfig();
    } else {
      msg.style.color = '#f87171';
      msg.textContent = json.error?.message || json.detail || 'Gagal (butuh token valid?).';
    }
  } catch(e) { msg.style.color = '#f87171'; msg.textContent = 'Gagal terhubung.'; }
}

async function saLoadSuggestions() {
  const tbody = document.getElementById('sa-suggestions-tbody');
  tbody.innerHTML = '<tr><td colspan="4">Memuat...</td></tr>';
  try {
    const r = await fetch(window.location.origin + '/api/v1/youtube/sentiment-agent/lexicon-suggestions?min_evidence=2&limit=20');
    const json = await r.json();
    const items = json.data?.items || [];
    if (items.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4">Belum ada usulan (min. 2x kemunculan).</td></tr>';
    } else {
      tbody.innerHTML = items.map(it => `<tr>
        <td><b>${daEsc(it.word)}</b></td>
        <td>${it.suggested_polarity === 'positif' ? '<span class="green">positif</span>' : '<span class="red">negatif</span>'}</td>
        <td>${it.evidence_count}x</td>
        <td style="max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${daEsc(it.example_comment)}">${daEsc(it.example_comment)}</td>
      </tr>`).join('');
    }
  } catch(e) { console.error(e); tbody.innerHTML = '<tr><td colspan="4">Gagal memuat.</td></tr>'; }
}

saLoadStatus();
saStatusTimer = setInterval(() => { if (saActiveTab === 'status') saLoadStatus(); }, 15000);

// ── Views Refresh Agent (agent kedua, kuota YouTube API terpisah) ──
async function vrLoadStatus() {
  try {
    const r = await fetch(window.location.origin + '/api/v1/youtube/views-refresh-agent/status');
    const json = await r.json();
    const d = json.data || {};
    const banner = document.getElementById('vr-status-banner');
    const dot = document.getElementById('vr-dot');
    const text = document.getElementById('vr-status-text');

    if (d.is_running) {
      banner.className = 'ed-banner unknown';
      dot.className = 'alive-dot on';
      text.innerHTML = '<span style="color:#60a5fa">SEDANG MEREFRESH...</span>';
    } else if (d.last_run_at) {
      banner.className = 'ed-banner active';
      dot.className = 'alive-dot on';
      text.innerHTML = '<span class="green">IDLE</span>';
    } else {
      banner.className = 'ed-banner unknown';
      dot.className = 'alive-dot off';
      text.innerHTML = '<span style="color:#64748b">BELUM PERNAH JALAN</span>';
    }
    document.getElementById('vr-last-run-at').textContent = d.last_run_at ? ('Terakhir: ' + new Date(d.last_run_at).toLocaleString('id-ID')) : '-';
    document.getElementById('vr-pending').textContent = (d.pending_refresh ?? 0).toLocaleString('id-ID');
  } catch(e) { console.error(e); }
}

async function vrLoadConfig() {
  try {
    const rs = await fetch(window.location.origin + '/api/v1/youtube/views-refresh-agent/schedule');
    const js = await rs.json();
    const interval = js.data?.interval_minutes;
    const allowed = js.data?.allowed_values || [15, 30, 60, 240];
    document.getElementById('vr-interval-btns').innerHTML = allowed.map(m =>
      `<button class="page-btn ${m === interval ? 'active' : ''}" style="${m === interval ? 'background:#1d4ed8;color:#fff' : ''}" onclick="vrSetInterval(${m})">${m} mnt</button>`
    ).join('');
  } catch(e) { console.error(e); }

  if (daToken()) {
    try {
      const rc = await fetch(window.location.origin + '/api/v1/youtube/views-refresh-agent/config', { headers: daAuthHeaders() });
      const jc = await rc.json();
      if (rc.ok) {
        document.getElementById('vr-key-current').textContent = jc.data?.api_key_set ? `(saat ini: ${jc.data.api_key_masked})` : '(belum diatur)';

        const bs = jc.data?.batch_size;
        const allowedBatch = jc.data?.allowed_batch_size || [10, 20, 50, 100];
        document.getElementById('vr-batch-btns').innerHTML = allowedBatch.map(s =>
          `<button class="page-btn ${s === bs ? 'active' : ''}" style="${s === bs ? 'background:#1d4ed8;color:#fff' : ''}" onclick="vrSetBatchSize(${s})">${s}</button>`
        ).join('');

        const age = jc.data?.refresh_age_hours;
        const allowedAge = jc.data?.allowed_refresh_age_hours || [1, 3, 6, 12, 24];
        document.getElementById('vr-age-btns').innerHTML = allowedAge.map(h =>
          `<button class="page-btn ${h === age ? 'active' : ''}" style="${h === age ? 'background:#1d4ed8;color:#fff' : ''}" onclick="vrSetAgeHours(${h})">${h} jam</button>`
        ).join('');
      }
    } catch(e) { console.error(e); }
  } else {
    document.getElementById('vr-batch-btns').innerHTML = '<span style="color:#64748b;font-size:0.78rem">Login dulu (paste token di atas) utk mengatur.</span>';
    document.getElementById('vr-age-btns').innerHTML = '<span style="color:#64748b;font-size:0.78rem">Login dulu (paste token di atas) utk mengatur.</span>';
  }
}

async function vrSetInterval(minutes) {
  const msg = document.getElementById('vr-config-msg');
  try {
    const r = await fetch(window.location.origin + '/api/v1/youtube/views-refresh-agent/schedule', {
      method: 'PATCH', headers: daAuthHeaders(), body: JSON.stringify({ interval_minutes: minutes }),
    });
    const json = await r.json();
    if (r.ok) { msg.style.color = '#4ade80'; msg.textContent = `Interval diubah ke ${minutes} menit.`; vrLoadConfig(); }
    else { msg.style.color = '#f87171'; msg.textContent = json.error?.message || json.detail || 'Gagal (butuh token valid?).'; }
  } catch(e) { msg.style.color = '#f87171'; msg.textContent = 'Gagal terhubung.'; }
}

async function vrSetBatchSize(size) {
  const msg = document.getElementById('vr-config-msg');
  try {
    const r = await fetch(window.location.origin + '/api/v1/youtube/views-refresh-agent/config', {
      method: 'PATCH', headers: daAuthHeaders(), body: JSON.stringify({ batch_size: size }),
    });
    const json = await r.json();
    if (r.ok) { msg.style.color = '#4ade80'; msg.textContent = `Batch diubah ke ${size} video/run.`; vrLoadConfig(); }
    else { msg.style.color = '#f87171'; msg.textContent = json.error?.message || json.detail || 'Gagal (butuh token valid?).'; }
  } catch(e) { msg.style.color = '#f87171'; msg.textContent = 'Gagal terhubung.'; }
}

async function vrSetAgeHours(hours) {
  const msg = document.getElementById('vr-config-msg');
  try {
    const r = await fetch(window.location.origin + '/api/v1/youtube/views-refresh-agent/config', {
      method: 'PATCH', headers: daAuthHeaders(), body: JSON.stringify({ refresh_age_hours: hours }),
    });
    const json = await r.json();
    if (r.ok) { msg.style.color = '#4ade80'; msg.textContent = `Ambang basi diubah ke ${hours} jam.`; vrLoadConfig(); }
    else { msg.style.color = '#f87171'; msg.textContent = json.error?.message || json.detail || 'Gagal (butuh token valid?).'; }
  } catch(e) { msg.style.color = '#f87171'; msg.textContent = 'Gagal terhubung.'; }
}

async function vrSaveConfig() {
  const msg = document.getElementById('vr-config-msg');
  const apiKey = document.getElementById('vr-apikey-input').value.trim();
  if (!apiKey) { msg.style.color = '#fbbf24'; msg.textContent = 'Isi API key dulu.'; return; }
  try {
    const r = await fetch(window.location.origin + '/api/v1/youtube/views-refresh-agent/config', {
      method: 'PATCH', headers: daAuthHeaders(), body: JSON.stringify({ api_key: apiKey }),
    });
    const json = await r.json();
    if (r.ok) {
      msg.style.color = '#4ade80'; msg.textContent = 'Tersimpan.';
      document.getElementById('vr-apikey-input').value = '';
      vrLoadConfig();
    } else { msg.style.color = '#f87171'; msg.textContent = json.error?.message || json.detail || 'Gagal (butuh token valid?).'; }
  } catch(e) { msg.style.color = '#f87171'; msg.textContent = 'Gagal terhubung.'; }
}

vrLoadStatus();
vrLoadConfig();
setInterval(vrLoadStatus, 15000);

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
app.include_router(users.router, prefix=API_PREFIX)
app.include_router(credentials.router, prefix=API_PREFIX)
app.include_router(agent_registry.router, prefix=API_PREFIX)
app.include_router(apify_pool.router, prefix=API_PREFIX)
app.include_router(ensembledata_pool.router, prefix=API_PREFIX)
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
app.include_router(tiktok_router, prefix=API_PREFIX)
app.include_router(twitter_router, prefix=API_PREFIX)
app.include_router(news_router, prefix=API_PREFIX)
app.include_router(threads_router, prefix=API_PREFIX)
app.include_router(trend_discovery_router, prefix=API_PREFIX)
app.include_router(trend_recommendations.router, prefix=API_PREFIX)
