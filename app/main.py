"""
Social Intelligence Platform -- API v2 (2026-07-22).

Direstrukturisasi TOTAL atas permintaan user: seluruh router/service/worker
platform lama (YouTube/Instagram/Facebook/TikTok/Twitter/Threads/News/dll)
DIHAPUS -- riwayat lengkapnya tetap ada di branch `main` (GitHub) kalau
suatu saat perlu dirujuk lagi. Database TIDAK disentuh (semua tabel+data
lama tetap ada, cuma kode API-nya yang dibangun ulang dari sini).

Yang tersisa: auth+users (login), credentials (kelola API key generik),
agent_registry (katalog agent AI + key/model, dashboard "Kelola Agent").
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from app.api.v1 import agent_curl_targets, agent_registry, auth, credentials, rotation_key_bank, third_party_apis, tiktok_pipeline, trend_recommendations, users, youtube_metadata, youtube_pipeline
# Import SEMUA domain model agar SQLAlchemy mapper bisa resolve
# relationship (tabel lama TETAP ada, walau endpoint API-nya sudah
# tidak ada) -- daftar lengkapnya di register_all_models.py, dipakai
# BARENG app.workers.celery_app supaya proses worker Celery juga py
# mapper registry yg sama (lihat docstring modul itu).
import app.infrastructure.database.register_all_models  # noqa: F401

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
    logger.info("app_starting", env=settings.app_env, version="2.0.0")
    yield
    logger.info("app_stopping")
    await close_redis()
    await engine.dispose()


app = FastAPI(
    title="Social Intelligence Platform",
    description="API v2 -- direstrukturisasi, lihat docstring app/main.py",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.app_debug else None,
    redoc_url="/redoc" if settings.app_debug else None,
)

app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
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


@app.get("/health", tags=["health"])
async def health_check():
    """Cek konektivitas DB+Redis (dua infra yang masih dipakai API v2)."""
    from sqlalchemy import text

    checks: dict[str, dict] = {}
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = {"status": "ok"}
    except Exception as exc:
        checks["database"] = {"status": "error", "detail": str(exc)}

    try:
        redis = await get_redis()
        await redis.ping()
        checks["redis"] = {"status": "ok"}
    except Exception as exc:
        checks["redis"] = {"status": "error", "detail": str(exc)}

    overall = "ok" if all(v["status"] == "ok" for v in checks.values()) else "degraded"
    return JSONResponse(
        status_code=200 if overall == "ok" else 207,
        content={"success": True, "data": {"status": overall, "version": "2.0.0", "checks": checks}},
    )


# ── Dashboard: Kelola Agent (SATU-SATUNYA halaman dashboard yang tersisa) ──────
@app.get("/scraping-status", response_class=HTMLResponse, include_in_schema=False)
async def kelola_agent_page():
    """Dashboard tunggal API v2 -- list agent AI + form tambah agent baru.
    Butuh token login (Bearer) admin, tempel sekali tersimpan di browser."""
    html = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kelola Agent</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; padding: 24px; }
  h1 { font-size: 1.5rem; font-weight: 700; margin-bottom: 4px; }
  .subtitle { color: #64748b; font-size: 0.85rem; margin-bottom: 20px; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 99px; font-size: 0.7rem; font-weight: 600; }
  table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
  th { text-align: left; padding: 8px 10px; color: #64748b; border-bottom: 1px solid #1e293b; font-weight: 600; font-size: 0.72rem; text-transform: uppercase; }
  td { padding: 9px 10px; border-bottom: 1px solid #1e293b; vertical-align: middle; }
  tr:hover td { background: #1e293b44; }
  .retry-btn { background: #1d4ed8; border: none; color: #fff; padding: 6px 16px; border-radius: 6px; cursor: pointer; font-size: 0.8rem; font-weight: 600; transition: background 0.15s; }
  .retry-btn:hover { background: #1e40af; }
  input[type=text], input[type=password], select { width: 100%; margin-bottom: 8px; padding: 7px 10px; background: #0f172a; border: 1px solid #334155; border-radius: 6px; color: #e2e8f0; font-size: 0.82rem; }
  details.ag-parent { background: #1e293b; border-radius: 8px; padding: 12px 16px; margin-bottom: 10px; }
  details.ag-parent > summary { cursor: pointer; list-style: none; display: flex; align-items: center; gap: 8px; }
  details.ag-parent > summary::-webkit-details-marker { display: none; }
  details.ag-parent > summary::before { content: '▶'; font-size: 0.7rem; color: #64748b; transition: transform 0.15s; }
  details.ag-parent[open] > summary::before { transform: rotate(90deg); }
  .ag-child-count { font-size: 0.7rem; color: #64748b; margin-left: auto; }
  .ag-child-box { background: #0f172a; border-radius: 6px; padding: 10px 12px; margin-top: 8px; margin-left: 14px; }
  .ag-child-box + .ag-child-box { margin-top: 6px; }
</style>
</head>
<body>

<h1>Kelola Agent</h1>
<div class="subtitle">Katalog semua agent AI + key/model + API pihak ketiga. API v2 -- direstrukturisasi 2026-07-22.</div>

<div style="max-width:420px;margin-bottom:20px;padding:12px 16px;background:#1e293b;border-radius:8px">
  <label style="font-size:0.8rem;color:#94a3b8">Token login (Bearer) admin -- tempel sekali, tersimpan di browser ini saja.</label>
  <input type="password" id="ag-token" placeholder="Bearer token admin..." onchange="agSaveToken()">
</div>

<div class="da-tabbar" style="display:flex;gap:4px;margin-bottom:16px;border-bottom:1px solid #1e293b">
  <button class="da-tab-btn active" id="tabbtn-agents" onclick="switchMainTab('agents')" style="background:none;border:none;color:#60a5fa;border-bottom:2px solid #60a5fa;padding:8px 16px;font-size:0.85rem;font-weight:600;cursor:pointer">Kelola Agent</button>
  <button class="da-tab-btn" id="tabbtn-apis" onclick="switchMainTab('apis')" style="background:none;border:none;color:#64748b;border-bottom:2px solid transparent;padding:8px 16px;font-size:0.85rem;font-weight:600;cursor:pointer">API Pihak Ketiga</button>
  <button class="da-tab-btn" id="tabbtn-curl" onclick="switchMainTab('curl')" style="background:none;border:none;color:#64748b;border-bottom:2px solid transparent;padding:8px 16px;font-size:0.85rem;font-weight:600;cursor:pointer">Target Curl</button>
  <button class="da-tab-btn" id="tabbtn-rotasi" onclick="switchMainTab('rotasi')" style="background:none;border:none;color:#64748b;border-bottom:2px solid transparent;padding:8px 16px;font-size:0.85rem;font-weight:600;cursor:pointer">Rotasi API Key</button>
</div>

<div id="tabpanel-agents">
<div style="margin-bottom:10px">
  <button class="retry-btn" onclick="agRegLoad()">Muat / Refresh Daftar Agent</button>
  <span id="agreg-msg" style="margin-left:10px;font-size:0.82rem;color:#64748b"></span>
</div>
<div id="agreg-list" style="margin-bottom:20px">
  <div style="color:#475569;font-style:italic;font-size:0.82rem">Klik "Muat / Refresh Daftar Agent" utk mulai.</div>
</div>

<div style="max-width:560px;background:#1e293b;border-radius:8px;padding:16px">
  <div style="font-size:0.85rem;font-weight:600;margin-bottom:10px">+ Tambah Agent Baru</div>
  <div style="font-size:0.72rem;color:#94a3b8;margin-bottom:10px">
    Ini CUMA mencatat nama/key/model agent baru -- TIDAK otomatis membuat kode scraping baru.
  </div>
  <input type="text" id="agreg-new-name" placeholder="Nama agent (mis. TikTok Discovery Agent)">
  <select id="agreg-new-parent">
    <option value="">(Tidak ada -- agent mandiri/parent baru)</option>
  </select>
  <input type="text" id="agreg-new-category" placeholder="Kategori (mis. TikTok)">
  <input type="text" id="agreg-new-desc" placeholder="Deskripsi singkat (opsional)">
  <input type="text" id="agreg-new-keylabel" placeholder="Label key (mis. OpenRouter)">
  <input type="password" id="agreg-new-apikey" placeholder="API key (opsional)">
  <input type="text" id="agreg-new-model" placeholder="Model (opsional)">
  <input type="text" id="agreg-new-account" placeholder="Akun/email (opsional)">
  <button class="retry-btn" onclick="agRegAddNew()">Tambah Agent</button>
  <span id="agreg-add-msg" style="margin-left:10px;font-size:0.82rem"></span>
</div>
</div>

<div id="tabpanel-apis" style="display:none">
<div style="max-width:560px;background:#1e293b;border-radius:8px;padding:16px">
  <div style="font-size:0.85rem;font-weight:600;margin-bottom:10px">+ Tambah API Pihak Ketiga</div>
  <div style="font-size:0.72rem;color:#94a3b8;margin-bottom:10px">
    Katalog bebas (Apify, OpenRouter, EnsembleData, Firecrawl, dll). Satu API cuma boleh dipakai satu agent -- pilih agent tujuannya sekalian di sini (opsional, bisa dihubungkan belakangan).
  </div>
  <input type="text" id="tpa-new-name" placeholder="Nama (mis. Apify Akun 1)">
  <input type="text" id="tpa-new-provider" list="tpa-provider-list" placeholder="Provider (pilih dari daftar atau ketik sendiri)">
  <datalist id="tpa-provider-list">
    <option value="Apify">
    <option value="EnsembleData">
    <option value="OpenRouter">
    <option value="Anthropic">
    <option value="OpenAI">
    <option value="Firecrawl">
    <option value="Tavily">
    <option value="YouTube Data API v3">
    <option value="Facebook / Meta Graph API">
    <option value="Instagram (cookie)">
  </datalist>
  <input type="password" id="tpa-new-apikey" placeholder="API key (opsional)">
  <input type="text" id="tpa-new-baseurl" placeholder="Base URL (opsional)">
  <input type="text" id="tpa-new-account" placeholder="Akun/email (opsional)">
  <input type="text" id="tpa-new-desc" placeholder="Deskripsi singkat (opsional)">
  <select id="tpa-new-agent">
    <option value="">(Belum dihubungkan ke agent manapun)</option>
  </select>
  <button class="retry-btn" onclick="tpaAddNew()">Tambah API</button>
  <span id="tpa-add-msg" style="margin-left:10px;font-size:0.82rem"></span>
</div>

<div style="margin-top:20px;margin-bottom:10px">
  <button class="retry-btn" onclick="tpaLoad()">Muat / Refresh API Pihak Ketiga</button>
  <span id="tpa-msg" style="margin-left:10px;font-size:0.82rem;color:#64748b"></span>
</div>
<div id="tpa-list" style="margin-bottom:20px">
  <div style="color:#475569;font-style:italic;font-size:0.82rem">Klik "Muat / Refresh API Pihak Ketiga" utk mulai.</div>
</div>
</div>

<div id="tabpanel-curl" style="display:none">
<div style="max-width:640px;background:#1e293b;border-radius:8px;padding:16px">
  <div style="font-size:0.85rem;font-weight:600;margin-bottom:10px">+ Tambah Target Curl</div>
  <div style="font-size:0.72rem;color:#94a3b8;margin-bottom:10px">
    URL + parameter yg akan dipakai agent utk crawling. Satu agent bisa punya BEBERAPA target curl.
    Pakai <code>{{NOW}}</code> atau <code>{{NOW-24h}}</code> / <code>{{NOW-7d}}</code> di URL/header/body kalau butuh tanggal yg selalu ikut waktu terkini (dihitung ulang tiap kali dilihat/di-copy, bukan tanggal beku).
  </div>
  <select id="curl-new-agent">
    <option value="">-- pilih agent --</option>
  </select>
  <input type="text" id="curl-new-name" placeholder="Nama target (mis. Trending Page TikTok)">

  <div style="display:flex;gap:4px;margin-bottom:10px">
    <button type="button" class="retry-btn" id="curl-mode-btn-form" style="background:#1d4ed8" onclick="curlSwitchMode('form')">Form Terstruktur</button>
    <button type="button" class="retry-btn" id="curl-mode-btn-paste" style="background:#334155" onclick="curlSwitchMode('paste')">Tempel Command Curl</button>
  </div>

  <div id="curl-mode-form">
    <input type="text" id="curl-new-url" placeholder="URL (mis. https://api.example.com/v1/search?q=...)">
    <select id="curl-new-method">
      <option value="GET">GET</option>
      <option value="POST">POST</option>
      <option value="PUT">PUT</option>
      <option value="DELETE">DELETE</option>
    </select>
    <textarea id="curl-new-headers" placeholder="Header, 1 per baris (mis. Authorization: Bearer xxx)" rows="3" style="width:100%;padding:8px;background:#0f172a;border:1px solid #334155;border-radius:4px;color:#e2e8f0;font-size:0.8rem;margin-bottom:8px;font-family:monospace"></textarea>
    <textarea id="curl-new-body" placeholder="Body (opsional, utk POST/PUT)" rows="2" style="width:100%;padding:8px;background:#0f172a;border:1px solid #334155;border-radius:4px;color:#e2e8f0;font-size:0.8rem;margin-bottom:8px;font-family:monospace"></textarea>
  </div>

  <div id="curl-mode-paste" style="display:none">
    <div style="font-size:0.7rem;color:#94a3b8;margin-bottom:6px">Tempel command curl lengkap, URL/method/header/body-nya akan diambil otomatis.</div>
    <textarea id="curl-new-rawcmd" placeholder="curl -X POST 'https://api.example.com/v1/search' -H 'Authorization: Bearer xxx' --data '{&quot;q&quot;:&quot;test&quot;}'" rows="5" style="width:100%;padding:8px;background:#0f172a;border:1px solid #334155;border-radius:4px;color:#e2e8f0;font-size:0.8rem;margin-bottom:8px;font-family:monospace"></textarea>
  </div>

  <input type="text" id="curl-new-desc" placeholder="Deskripsi singkat (opsional)">
  <button class="retry-btn" onclick="curlAddNew()">Tambah Target</button>
  <span id="curl-add-msg" style="margin-left:10px;font-size:0.82rem"></span>
</div>

<div style="margin-top:20px;margin-bottom:10px">
  <button class="retry-btn" onclick="curlLoad()">Muat / Refresh Target Curl</button>
  <span id="curl-msg" style="margin-left:10px;font-size:0.82rem;color:#64748b"></span>
</div>
<div id="curl-list" style="margin-bottom:20px">
  <div style="color:#475569;font-style:italic;font-size:0.82rem">Klik "Muat / Refresh Target Curl" utk mulai.</div>
</div>
</div>

<div id="tabpanel-rotasi" style="display:none">
<div style="max-width:640px;background:#1e293b;border-radius:8px;padding:16px">
  <div style="font-size:0.85rem;font-weight:600;margin-bottom:10px">+ Tambah Key ke Bank Rotasi</div>
  <div style="font-size:0.72rem;color:#94a3b8;margin-bottom:10px">
    Bank key BERSAMA (OpenRouter, Grok/xAI, dll) -- kalau key aktif 1 agent gagal (401/402/429/dst) saat kerja, sistem OTOMATIS ambil key dari sini utk gantikan, tanpa perlu diklik manual.
  </div>
  <input type="text" id="rotasi-new-provider" list="tpa-provider-list" placeholder="Provider (mis. OpenRouter, Grok/xAI)">
  <input type="password" id="rotasi-new-apikey" placeholder="API key">
  <input type="text" id="rotasi-new-model" placeholder="Model (opsional, mis. openai/gpt-oss-20b:free)">
  <input type="text" id="rotasi-new-account" placeholder="Akun/email (opsional)">
  <button class="retry-btn" onclick="rotasiAddNew()">Tambah ke Bank</button>
  <span id="rotasi-add-msg" style="margin-left:10px;font-size:0.82rem"></span>
</div>

<div style="margin-top:20px;margin-bottom:10px">
  <button class="retry-btn" onclick="rotasiLoad()">Muat / Refresh Bank Rotasi</button>
  <span id="rotasi-msg" style="margin-left:10px;font-size:0.82rem;color:#64748b"></span>
</div>
<div id="rotasi-list" style="margin-bottom:20px">
  <div style="color:#475569;font-style:italic;font-size:0.82rem">Klik "Muat / Refresh Bank Rotasi" utk mulai.</div>
</div>
</div>

<script>
function switchMainTab(name) {
  document.getElementById('tabpanel-agents').style.display = name === 'agents' ? '' : 'none';
  document.getElementById('tabpanel-apis').style.display = name === 'apis' ? '' : 'none';
  document.getElementById('tabpanel-curl').style.display = name === 'curl' ? '' : 'none';
  document.getElementById('tabpanel-rotasi').style.display = name === 'rotasi' ? '' : 'none';
  ['agents', 'apis', 'curl', 'rotasi'].forEach(n => {
    const btn = document.getElementById('tabbtn-' + n);
    if (n === name) { btn.style.color = '#60a5fa'; btn.style.borderBottomColor = '#60a5fa'; }
    else { btn.style.color = '#64748b'; btn.style.borderBottomColor = 'transparent'; }
  });
  if (name === 'rotasi' && agToken()) rotasiLoad();
  if (name === 'apis' && agToken()) tpaLoad();
  if (name === 'curl' && agToken()) curlLoad();
}
function agToken() {
  return document.getElementById('ag-token').value || localStorage.getItem('ag_token') || '';
}
function agAuthHeaders() {
  const t = agToken();
  return t ? { 'Authorization': 'Bearer ' + t, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' };
}
function agSaveToken() {
  const t = document.getElementById('ag-token').value;
  if (t) { localStorage.setItem('ag_token', t); agRegLoad(); }
}

function agKeyTable(a) {
  return `
    <table>
      <thead><tr><th>Key</th><th>Nilai</th><th>Model</th><th>Akun</th><th>Aksi</th></tr></thead>
      <tbody>
      ${a.keys.map(k => `
        <tr>
          <td style="font-size:0.78rem">${k.key_label}</td>
          <td style="font-family:monospace;font-size:0.75rem">${k.masked_value || '-'}</td>
          <td style="font-size:0.75rem">
            ${k.editable_here
              ? `<div style="margin-bottom:2px">${k.model || '<i style="color:#475569">(kosong)</i>'}</div>
                 <input type="text" id="agreg-editmodel-${k.id}" placeholder="Model baru..." style="width:110px;display:inline-block;padding:4px 6px;background:#0f172a;border:1px solid #334155;border-radius:4px;color:#e2e8f0;font-size:0.7rem">
                 <button class="retry-btn" style="padding:4px 8px;font-size:0.7rem" onclick="agRegEditModel('${k.id}')">Ganti</button>`
              : (k.model || '-')}
          </td>
          <td style="font-size:0.75rem">${k.account_email || '-'}</td>
          <td style="font-size:0.72rem;color:#64748b">
            ${k.editable_here
              ? `<input type="password" id="agreg-edit-${k.id}" placeholder="Key baru..." style="width:110px;display:inline-block;padding:4px 6px;background:#0f172a;border:1px solid #334155;border-radius:4px;color:#e2e8f0;font-size:0.7rem">
                 <button class="retry-btn" style="padding:4px 8px;font-size:0.7rem" onclick="agRegEditCustom('${k.id}')">Ganti</button>
                 ${k.is_set ? `<button class="retry-btn" style="padding:4px 8px;font-size:0.7rem;background:#7f1d1d" onclick="agRegClearCustom('${k.id}')">Hapus Key</button>` : ''}`
              : (k.note || 'Lihat /api/v1/credentials')}
          </td>
        </tr>
        ${k.last_error ? `<tr><td colspan="5" style="font-size:0.7rem;color:#c96f5c;padding-top:0">&#9888; Error terakhir (${new Date(k.last_error_at).toLocaleString('id-ID')}): ${k.last_error.slice(0, 200)}</td></tr>` : ''}
      `).join('')}
      </tbody>
    </table>`;
}

function renderAgentTree(agents) {
  const parents = agents.filter(a => !a.parent_agent_name);
  const childrenByParent = {};
  agents.filter(a => a.parent_agent_name).forEach(a => {
    (childrenByParent[a.parent_agent_name] = childrenByParent[a.parent_agent_name] || []).push(a);
  });
  // Child yg parent_agent_name-nya tidak cocok agent manapun yg ada (orphan) --
  // tetap ditampilkan sbg parent sendiri drpd hilang diam-diam dari tampilan.
  const parentNames = new Set(parents.map(p => p.agent_name));
  Object.keys(childrenByParent).forEach(pname => {
    if (!parentNames.has(pname)) parents.push({ agent_name: pname, category: '?', description: '(parent tidak ditemukan)', keys: [] });
  });

  document.getElementById('agreg-list').innerHTML = parents.map(p => {
    const children = childrenByParent[p.agent_name] || [];
    return `
      <details class="ag-parent">
        <summary>
          <b>${p.agent_name}</b>
          <span class="pill" style="background:#1e3a5f;color:#60a5fa">${p.category}</span>
          <span class="ag-child-count">${children.length} child</span>
        </summary>
        <div style="font-size:0.72rem;color:#94a3b8;margin:8px 0">${p.description || ''}</div>
        ${p.keys.length ? agKeyTable(p) : ''}
        ${children.map(c => `
          <div class="ag-child-box">
            <div style="margin-bottom:6px;display:flex;align-items:center;gap:6px">
              <b style="font-size:0.85rem">${c.agent_name}</b>
              <span class="pill" style="background:#334155;color:#94a3b8">child</span>
              <button class="retry-btn" style="margin-left:auto;padding:4px 10px;font-size:0.7rem;background:#7f1d1d"
                onclick="agRegDeleteAgent('${c.agent_name}', '${c.keys.map(k => k.id).join(',')}')">Hapus</button>
            </div>
            <div style="font-size:0.7rem;color:#94a3b8;margin-bottom:6px">${c.description || ''}</div>
            ${agKeyTable(c)}
          </div>
        `).join('')}
      </details>`;
  }).join('');
}

function populateParentDropdown(agents) {
  const sel = document.getElementById('agreg-new-parent');
  const currentValue = sel.value;
  const parentNames = [...new Set(agents.filter(a => !a.parent_agent_name).map(a => a.agent_name))].sort();
  sel.innerHTML = '<option value="">(Tidak ada -- agent mandiri/parent baru)</option>' +
    parentNames.map(n => `<option value="${n}">${n}</option>`).join('');
  sel.value = currentValue;
}

async function agRegLoad() {
  const msgEl = document.getElementById('agreg-msg');
  msgEl.style.color = '#60a5fa';
  msgEl.textContent = 'Memuat...';
  try {
    const r = await fetch(window.location.origin + '/api/v1/agent-registry', { headers: agAuthHeaders() });
    const j = await r.json();
    if (!r.ok) {
      msgEl.style.color = '#f87171';
      msgEl.textContent = 'Gagal: ' + ((j.error && j.error.message) || j.detail || j.message || 'isi token login dulu');
      return;
    }
    const agents = j.data.agents;
    renderAgentTree(agents);
    populateParentDropdown(agents);
    window.__allAgentNames = agents.map(a => a.agent_name).sort();
    const tpaSel = document.getElementById('tpa-new-agent');
    if (tpaSel) {
      const cur = tpaSel.value;
      tpaSel.innerHTML = '<option value="">(Belum dihubungkan ke agent manapun)</option>' +
        window.__allAgentNames.map(n => `<option value="${n}">${n}</option>`).join('');
      tpaSel.value = cur;
    }
    const curlSel = document.getElementById('curl-new-agent');
    if (curlSel) {
      const cur2 = curlSel.value;
      curlSel.innerHTML = '<option value="">-- pilih agent --</option>' +
        window.__allAgentNames.map(n => `<option value="${n}">${n}</option>`).join('');
      curlSel.value = cur2;
    }
    msgEl.style.color = '#64748b';
    msgEl.textContent = 'Terakhir dimuat: ' + new Date().toLocaleTimeString('id-ID') + ` (${agents.length} agent)`;
  } catch (e) {
    msgEl.style.color = '#f87171';
    msgEl.textContent = 'Gagal: ' + e.message;
  }
}

async function agRegDeleteAgent(agentName, keyIdsCsv) {
  if (!agToken()) { alert('Isi token login (Bearer) dulu'); return; }
  if (!confirm(`Hapus agent "${agentName}" beserta semua key-nya? Tidak bisa dibatalkan.`)) return;
  const ids = keyIdsCsv.split(',').filter(Boolean);
  try {
    for (const id of ids) {
      await fetch(window.location.origin + '/api/v1/agent-registry/' + id, {
        method: 'DELETE', headers: agAuthHeaders(),
      });
    }
    agRegLoad();
  } catch (e) {
    alert('Gagal hapus: ' + e.message);
  }
}

async function agRegEditCustom(id) {
  const input = document.getElementById('agreg-edit-' + id);
  const value = input.value.trim();
  if (!value) return;
  if (!agToken()) { alert('Isi token login (Bearer) dulu'); return; }
  try {
    const r = await fetch(window.location.origin + '/api/v1/agent-registry/' + id, {
      method: 'PATCH', headers: agAuthHeaders(), body: JSON.stringify({ api_key: value }),
    });
    const j = await r.json();
    if (!r.ok) { alert('Gagal: ' + ((j.error && j.error.message) || j.detail || j.message || 'unknown')); return; }
    input.value = '';
    agRegLoad();
  } catch (e) {
    alert('Gagal: ' + e.message);
  }
}

async function agRegEditModel(id) {
  const input = document.getElementById('agreg-editmodel-' + id);
  const value = input.value.trim();
  if (!value) return;
  if (!agToken()) { alert('Isi token login (Bearer) dulu'); return; }
  try {
    const r = await fetch(window.location.origin + '/api/v1/agent-registry/' + id, {
      method: 'PATCH', headers: agAuthHeaders(), body: JSON.stringify({ model: value }),
    });
    const j = await r.json();
    if (!r.ok) { alert('Gagal: ' + ((j.error && j.error.message) || j.detail || j.message || 'unknown')); return; }
    input.value = '';
    agRegLoad();
  } catch (e) {
    alert('Gagal: ' + e.message);
  }
}

async function agRegClearCustom(id) {
  if (!agToken()) { alert('Isi token login (Bearer) dulu'); return; }
  if (!confirm('Hapus key agent ini? Agent akan tercatat tanpa key sampai diisi ulang.')) return;
  try {
    const r = await fetch(window.location.origin + '/api/v1/agent-registry/' + id, {
      method: 'PATCH', headers: agAuthHeaders(), body: JSON.stringify({ api_key: '' }),
    });
    const j = await r.json();
    if (!r.ok) { alert('Gagal: ' + ((j.error && j.error.message) || j.detail || j.message || 'unknown')); return; }
    agRegLoad();
  } catch (e) {
    alert('Gagal: ' + e.message);
  }
}

async function agRegAddNew() {
  const name = document.getElementById('agreg-new-name').value.trim();
  if (!name) { alert('Nama agent wajib diisi'); return; }
  if (!agToken()) { alert('Isi token login (Bearer) dulu'); return; }
  const body = {
    agent_name: name,
    category: document.getElementById('agreg-new-category').value.trim() || 'Umum',
    description: document.getElementById('agreg-new-desc').value.trim() || null,
    key_label: document.getElementById('agreg-new-keylabel').value.trim() || 'API Key',
    api_key: document.getElementById('agreg-new-apikey').value.trim() || null,
    model: document.getElementById('agreg-new-model').value.trim() || null,
    account_email: document.getElementById('agreg-new-account').value.trim() || null,
    parent_agent_name: document.getElementById('agreg-new-parent').value || null,
  };
  const msgEl = document.getElementById('agreg-add-msg');
  try {
    const r = await fetch(window.location.origin + '/api/v1/agent-registry', {
      method: 'POST', headers: agAuthHeaders(), body: JSON.stringify(body),
    });
    const j = await r.json();
    if (!r.ok) {
      msgEl.style.color = '#f87171';
      msgEl.textContent = 'Gagal: ' + ((j.error && j.error.message) || j.detail || j.message || 'unknown');
      return;
    }
    msgEl.style.color = '#4ade80';
    msgEl.textContent = 'Agent ditambahkan!';
    ['name', 'category', 'desc', 'keylabel', 'apikey', 'model', 'account', 'parent'].forEach(f => {
      document.getElementById('agreg-new-' + f).value = '';
    });
    agRegLoad();
  } catch (e) {
    msgEl.style.color = '#f87171';
    msgEl.textContent = 'Gagal: ' + e.message;
  }
}

// ── Tab API Pihak Ketiga (2026-07-22) ──
async function tpaLoad() {
  const msgEl = document.getElementById('tpa-msg');
  msgEl.style.color = '#60a5fa';
  msgEl.textContent = 'Memuat...';
  try {
    const r = await fetch(window.location.origin + '/api/v1/third-party-apis', { headers: agAuthHeaders() });
    const j = await r.json();
    if (!r.ok) {
      msgEl.style.color = '#f87171';
      msgEl.textContent = 'Gagal: ' + ((j.error && j.error.message) || j.detail || j.message || 'isi token login dulu');
      return;
    }
    const apis = j.data.apis;
    if (apis.length === 0) {
      document.getElementById('tpa-list').innerHTML = '<div style="color:#475569;font-style:italic;font-size:0.82rem">Belum ada API pihak ketiga terdaftar. Tambah lewat form di atas.</div>';
      msgEl.style.color = '#64748b';
      msgEl.textContent = 'Terakhir dimuat: ' + new Date().toLocaleTimeString('id-ID') + ' (0 API)';
      return;
    }
    document.getElementById('tpa-list').innerHTML = apis.map(a => `
      <div style="background:#1e293b;border-radius:8px;padding:12px 16px;margin-bottom:10px">
        <div style="margin-bottom:6px;display:flex;align-items:center;gap:6px">
          <input type="text" id="tpa-editname-${a.id}" value="${a.name}" style="width:180px;padding:4px 6px;background:#0f172a;border:1px solid #334155;border-radius:4px;color:#e2e8f0;font-size:0.8rem;font-weight:600;margin-bottom:0">
          <span class="pill" style="background:#1e3a5f;color:#60a5fa">${a.provider}</span>
          ${!a.enabled ? '<span class="pill" style="background:#450a0a;color:#f87171">nonaktif</span>' : ''}
          <button class="retry-btn" style="padding:4px 8px;font-size:0.7rem" onclick="tpaRename('${a.id}')">Simpan Nama</button>
          <button class="retry-btn" style="margin-left:auto;padding:4px 10px;font-size:0.7rem;background:#7f1d1d" onclick="tpaDelete('${a.id}')">Hapus</button>
        </div>
        <div style="font-size:0.72rem;color:#94a3b8;margin-bottom:4px">${a.description || ''}</div>
        <div style="font-size:0.75rem;color:#94a3b8;margin-bottom:8px">
          Key: <span style="font-family:monospace">${a.masked_key || '-'}</span>
          ${a.base_url ? ' &middot; URL: ' + a.base_url : ''}
          ${a.account_email ? ' &middot; Akun: ' + a.account_email : ''}
        </div>
        <div style="font-size:0.72rem;color:#64748b;margin-bottom:6px">Dipakai agent:
          ${a.linked_agent
            ? `<span class="pill" style="background:#334155;color:#e2e8f0">${a.linked_agent} <a href="#" onclick="tpaUnlink('${a.id}','${a.linked_agent}');return false" style="color:#f87171;text-decoration:none;margin-left:4px">&times;</a></span>`
            : '<i>belum ada</i>'}
        </div>
        ${a.last_error ? `<div style="font-size:0.7rem;color:#c96f5c;margin-bottom:6px">&#9888; Error terakhir (${new Date(a.last_error_at).toLocaleString('id-ID')}): ${a.last_error.slice(0, 200)}</div>` : ''}
        <div style="display:flex;gap:6px">
          <select id="tpa-linksel-${a.id}" style="width:auto;flex:1;margin-bottom:0">
            <option value="">-- tidak ada agent --</option>
            ${(window.__allAgentNames || []).map(n => `<option value="${n}" ${n === a.linked_agent ? 'selected' : ''}>${n}</option>`).join('')}
          </select>
          <button class="retry-btn" style="padding:6px 12px;font-size:0.75rem" onclick="tpaReassign('${a.id}', ${a.linked_agent ? `'${a.linked_agent}'` : 'null'})">${a.linked_agent ? 'Ganti Agent' : 'Hubungkan'}</button>
        </div>
      </div>
    `).join('');
    msgEl.style.color = '#64748b';
    msgEl.textContent = 'Terakhir dimuat: ' + new Date().toLocaleTimeString('id-ID') + ` (${apis.length} API)`;
  } catch (e) {
    msgEl.style.color = '#f87171';
    msgEl.textContent = 'Gagal: ' + e.message;
  }
}

async function tpaAddNew() {
  const name = document.getElementById('tpa-new-name').value.trim();
  const provider = document.getElementById('tpa-new-provider').value.trim();
  if (!name || !provider) { alert('Nama dan provider wajib diisi'); return; }
  if (!agToken()) { alert('Isi token login (Bearer) dulu'); return; }
  const body = {
    name, provider,
    api_key: document.getElementById('tpa-new-apikey').value.trim() || null,
    base_url: document.getElementById('tpa-new-baseurl').value.trim() || null,
    account_email: document.getElementById('tpa-new-account').value.trim() || null,
    description: document.getElementById('tpa-new-desc').value.trim() || null,
    agent_name: document.getElementById('tpa-new-agent').value || null,
  };
  const msgEl = document.getElementById('tpa-add-msg');
  try {
    const r = await fetch(window.location.origin + '/api/v1/third-party-apis', {
      method: 'POST', headers: agAuthHeaders(), body: JSON.stringify(body),
    });
    const j = await r.json();
    if (!r.ok) {
      msgEl.style.color = '#f87171';
      msgEl.textContent = 'Gagal: ' + ((j.error && j.error.message) || j.detail || j.message || 'unknown');
      return;
    }
    msgEl.style.color = '#4ade80';
    msgEl.textContent = 'API ditambahkan!';
    ['name', 'provider', 'apikey', 'baseurl', 'account', 'desc'].forEach(f => {
      document.getElementById('tpa-new-' + f).value = '';
    });
    document.getElementById('tpa-new-agent').value = '';
    tpaLoad();
  } catch (e) {
    msgEl.style.color = '#f87171';
    msgEl.textContent = 'Gagal: ' + e.message;
  }
}

async function tpaRename(apiId) {
  const input = document.getElementById('tpa-editname-' + apiId);
  const value = input.value.trim();
  if (!value) { alert('Nama tidak boleh kosong'); return; }
  if (!agToken()) { alert('Isi token login (Bearer) dulu'); return; }
  try {
    const r = await fetch(window.location.origin + '/api/v1/third-party-apis/' + apiId, {
      method: 'PATCH', headers: agAuthHeaders(), body: JSON.stringify({ name: value }),
    });
    const j = await r.json();
    if (!r.ok) { alert('Gagal: ' + ((j.error && j.error.message) || j.detail || j.message || 'unknown')); return; }
    tpaLoad();
  } catch (e) {
    alert('Gagal: ' + e.message);
  }
}

async function tpaReassign(apiId, currentAgent) {
  const sel = document.getElementById('tpa-linksel-' + apiId);
  const agentName = sel.value;
  if (!agToken()) { alert('Isi token login (Bearer) dulu'); return; }
  if (agentName === (currentAgent || '')) { return; }
  if (!agentName) { return tpaUnlink(apiId, currentAgent); }
  try {
    if (currentAgent) {
      const r1 = await fetch(window.location.origin + '/api/v1/third-party-apis/' + apiId + '/unlink', {
        method: 'POST', headers: agAuthHeaders(), body: JSON.stringify({ agent_name: currentAgent }),
      });
      if (!r1.ok) { const j1 = await r1.json(); alert('Gagal lepas agent lama: ' + ((j1.error && j1.error.message) || 'unknown')); return; }
    }
    const r2 = await fetch(window.location.origin + '/api/v1/third-party-apis/' + apiId + '/link', {
      method: 'POST', headers: agAuthHeaders(), body: JSON.stringify({ agent_name: agentName }),
    });
    const j2 = await r2.json();
    if (!r2.ok) { alert('Gagal: ' + ((j2.error && j2.error.message) || j2.detail || j2.message || 'unknown')); return; }
    tpaLoad();
  } catch (e) {
    alert('Gagal: ' + e.message);
  }
}

async function tpaUnlink(apiId, agentName) {
  if (!agToken()) { alert('Isi token login (Bearer) dulu'); return; }
  try {
    const r = await fetch(window.location.origin + '/api/v1/third-party-apis/' + apiId + '/unlink', {
      method: 'POST', headers: agAuthHeaders(), body: JSON.stringify({ agent_name: agentName }),
    });
    const j = await r.json();
    if (!r.ok) { alert('Gagal: ' + ((j.error && j.error.message) || j.detail || j.message || 'unknown')); return; }
    tpaLoad();
  } catch (e) {
    alert('Gagal: ' + e.message);
  }
}

async function tpaDelete(apiId) {
  if (!agToken()) { alert('Isi token login (Bearer) dulu'); return; }
  if (!confirm('Hapus API pihak ketiga ini beserta semua hubungannya ke agent? Tidak bisa dibatalkan.')) return;
  try {
    const r = await fetch(window.location.origin + '/api/v1/third-party-apis/' + apiId, {
      method: 'DELETE', headers: agAuthHeaders(),
    });
    const j = await r.json();
    if (!r.ok) { alert('Gagal: ' + ((j.error && j.error.message) || j.detail || j.message || 'unknown')); return; }
    tpaLoad();
  } catch (e) {
    alert('Gagal: ' + e.message);
  }
}

// ── Tab Target Curl (2026-07-22) ──
function resolveCurlPlaceholders(text) {
  if (!text) return text;
  let result = text.split('{{NOW}}').join(new Date().toISOString());
  let out = '';
  let i = 0;
  while (true) {
    const start = result.indexOf('{{NOW-', i);
    if (start === -1) { out += result.slice(i); break; }
    const end = result.indexOf('}}', start);
    if (end === -1) { out += result.slice(i); break; }
    out += result.slice(i, start);
    const token = result.slice(start + 6, end);
    const unit = token.slice(-1);
    const amount = parseInt(token.slice(0, -1), 10);
    let ms = NaN;
    if (unit === 'h') ms = amount * 60 * 60 * 1000;
    else if (unit === 'd') ms = amount * 24 * 60 * 60 * 1000;
    else if (unit === 'm') ms = amount * 60 * 1000;
    out += isNaN(ms) ? result.slice(start, end + 2) : new Date(Date.now() - ms).toISOString();
    i = end + 2;
  }
  return out;
}

function buildCurlCommand(t) {
  const NL = String.fromCharCode(10);
  const BS = String.fromCharCode(92);
  function shQuote(s) { return "'" + String(s).split("'").join("'" + BS + "''") + "'"; }
  const url = resolveCurlPlaceholders(t.url);
  const headersResolved = resolveCurlPlaceholders(t.headers);
  const bodyResolved = resolveCurlPlaceholders(t.body);
  let cmd = 'curl -X ' + t.method + ' ' + shQuote(url);
  if (headersResolved) {
    headersResolved.split(NL).map(h => h.trim()).filter(Boolean).forEach(h => {
      cmd += ' ' + BS + NL + '  -H ' + shQuote(h);
    });
  }
  if (bodyResolved) {
    cmd += ' ' + BS + NL + '  --data ' + shQuote(bodyResolved);
  }
  return cmd;
}

async function curlLoad() {
  const msgEl = document.getElementById('curl-msg');
  msgEl.style.color = '#60a5fa';
  msgEl.textContent = 'Memuat...';
  try {
    const r = await fetch(window.location.origin + '/api/v1/agent-curl-targets', { headers: agAuthHeaders() });
    const j = await r.json();
    if (!r.ok) {
      msgEl.style.color = '#f87171';
      msgEl.textContent = 'Gagal: ' + ((j.error && j.error.message) || j.detail || j.message || 'isi token login dulu');
      return;
    }
    const targets = j.data.targets;
    if (targets.length === 0) {
      document.getElementById('curl-list').innerHTML = '<div style="color:#475569;font-style:italic;font-size:0.82rem">Belum ada target curl terdaftar. Tambah lewat form di atas.</div>';
      msgEl.style.color = '#64748b';
      msgEl.textContent = 'Terakhir dimuat: ' + new Date().toLocaleTimeString('id-ID') + ' (0 target)';
      return;
    }
    let html = '';
    let lastAgent = null;
    targets.forEach(t => {
      if (t.agent_name !== lastAgent) {
        html += `<div style="font-size:0.78rem;font-weight:600;color:#60a5fa;margin:14px 0 6px">${t.agent_name}</div>`;
        lastAgent = t.agent_name;
      }
      const cmd = buildCurlCommand(t);
      html += `
        <div style="background:#1e293b;border-radius:8px;padding:12px 16px;margin-bottom:10px">
          <div style="margin-bottom:6px;display:flex;align-items:center;gap:6px">
            <b>${t.name}</b>
            <span class="pill" style="background:#1e3a5f;color:#60a5fa">${t.method}</span>
            ${!t.enabled ? '<span class="pill" style="background:#450a0a;color:#f87171">nonaktif</span>' : ''}
            <button class="retry-btn" style="margin-left:auto;padding:4px 10px;font-size:0.7rem;background:#7f1d1d" onclick="curlDelete('${t.id}')">Hapus</button>
          </div>
          <div style="font-size:0.72rem;color:#94a3b8;margin-bottom:6px">${t.description || ''}</div>
          <pre style="background:#0f172a;border:1px solid #334155;border-radius:4px;padding:8px;font-size:0.72rem;color:#a5f3fc;overflow-x:auto;white-space:pre-wrap;word-break:break-all;margin-bottom:6px">${cmd.replace(/</g, '&lt;')}</pre>
          <button class="retry-btn" style="padding:4px 10px;font-size:0.7rem" onclick="curlCopy('${t.id}')">Copy Command</button>
          <button class="retry-btn" style="padding:4px 10px;font-size:0.7rem;background:#166534" onclick="curlExecute('${t.id}')">Jalankan (Test)</button>
          <div id="curl-result-${t.id}" style="margin-top:8px"></div>
        </div>`;
    });
    document.getElementById('curl-list').innerHTML = html;
    window.__curlTargets = {};
    targets.forEach(t => { window.__curlTargets[t.id] = t; });
    msgEl.style.color = '#64748b';
    msgEl.textContent = 'Terakhir dimuat: ' + new Date().toLocaleTimeString('id-ID') + ` (${targets.length} target)`;
  } catch (e) {
    msgEl.style.color = '#f87171';
    msgEl.textContent = 'Gagal: ' + e.message;
  }
}

function curlCopy(id) {
  const t = (window.__curlTargets || {})[id];
  if (!t) return;
  const cmd = buildCurlCommand(t);
  navigator.clipboard.writeText(cmd).then(() => alert('Command curl disalin!')).catch(() => alert('Gagal menyalin, salin manual dari kotak di atas.'));
}

window.__curlMode = 'form';
function curlSwitchMode(mode) {
  window.__curlMode = mode;
  document.getElementById('curl-mode-form').style.display = mode === 'form' ? '' : 'none';
  document.getElementById('curl-mode-paste').style.display = mode === 'paste' ? '' : 'none';
  document.getElementById('curl-mode-btn-form').style.background = mode === 'form' ? '#1d4ed8' : '#334155';
  document.getElementById('curl-mode-btn-paste').style.background = mode === 'paste' ? '#1d4ed8' : '#334155';
}

function parseCurlCommand(raw) {
  const NL = String.fromCharCode(10);
  const TAB = String.fromCharCode(9);
  const CR = String.fromCharCode(13);
  function isWS(ch) { return ch === ' ' || ch === TAB || ch === NL || ch === CR; }
  const tokens = [];
  const s = raw.trim();
  let i = 0;
  while (i < s.length) {
    while (i < s.length && isWS(s[i])) i++;
    if (i >= s.length) break;
    if (s[i] === "'" || s[i] === '"') {
      const quote = s[i]; i++;
      let val = '';
      while (i < s.length && s[i] !== quote) { val += s[i]; i++; }
      i++;
      tokens.push(val);
    } else {
      let val = '';
      while (i < s.length && !isWS(s[i])) { val += s[i]; i++; }
      tokens.push(val);
    }
  }
  let method = 'GET';
  let url = '';
  const headerLines = [];
  let body = null;
  for (let idx = 0; idx < tokens.length; idx++) {
    const tok = tokens[idx];
    if (tok === 'curl') continue;
    if (tok === '-X' || tok === '--request') { method = (tokens[++idx] || method).toUpperCase(); continue; }
    if (tok === '-H' || tok === '--header') { const h = tokens[++idx]; if (h) headerLines.push(h); continue; }
    if (tok === '-d' || tok === '--data' || tok === '--data-raw' || tok === '--data-binary' || tok === '--data-urlencode') {
      body = tokens[++idx] || body;
      continue;
    }
    if (tok.indexOf('-') === 0) continue;
    if (!url && (tok.indexOf('http://') === 0 || tok.indexOf('https://') === 0)) { url = tok; continue; }
  }
  if (body && method === 'GET') method = 'POST';
  return { method, url, headers: headerLines.join(NL), body };
}

async function curlAddNew() {
  const agentName = document.getElementById('curl-new-agent').value;
  const name = document.getElementById('curl-new-name').value.trim();
  if (!agentName) { alert('Pilih agent dulu'); return; }
  if (!name) { alert('Nama target wajib diisi'); return; }
  if (!agToken()) { alert('Isi token login (Bearer) dulu'); return; }

  let url, method, headers, bodyData;
  if (window.__curlMode === 'paste') {
    const raw = document.getElementById('curl-new-rawcmd').value.trim();
    if (!raw) { alert('Tempel command curl dulu'); return; }
    const parsed = parseCurlCommand(raw);
    if (!parsed.url) { alert('URL tidak terbaca dari command curl, cek lagi formatnya'); return; }
    url = parsed.url; method = parsed.method; headers = parsed.headers || null; bodyData = parsed.body || null;
  } else {
    url = document.getElementById('curl-new-url').value.trim();
    if (!url) { alert('URL wajib diisi'); return; }
    method = document.getElementById('curl-new-method').value;
    headers = document.getElementById('curl-new-headers').value.trim() || null;
    bodyData = document.getElementById('curl-new-body').value.trim() || null;
  }

  const body = {
    agent_name: agentName, name, url, method, headers, body: bodyData,
    description: document.getElementById('curl-new-desc').value.trim() || null,
  };
  const msgEl = document.getElementById('curl-add-msg');
  try {
    const r = await fetch(window.location.origin + '/api/v1/agent-curl-targets', {
      method: 'POST', headers: agAuthHeaders(), body: JSON.stringify(body),
    });
    const j = await r.json();
    if (!r.ok) {
      msgEl.style.color = '#f87171';
      msgEl.textContent = 'Gagal: ' + ((j.error && j.error.message) || j.detail || j.message || 'unknown');
      return;
    }
    msgEl.style.color = '#4ade80';
    msgEl.textContent = 'Target ditambahkan!';
    ['name', 'url', 'headers', 'body', 'desc', 'rawcmd'].forEach(f => {
      const el = document.getElementById('curl-new-' + f);
      if (el) el.value = '';
    });
    document.getElementById('curl-new-agent').value = '';
    document.getElementById('curl-new-method').value = 'GET';
    curlLoad();
  } catch (e) {
    msgEl.style.color = '#f87171';
    msgEl.textContent = 'Gagal: ' + e.message;
  }
}

async function curlDelete(id) {
  if (!agToken()) { alert('Isi token login (Bearer) dulu'); return; }
  if (!confirm('Hapus target curl ini? Tidak bisa dibatalkan.')) return;
  try {
    const r = await fetch(window.location.origin + '/api/v1/agent-curl-targets/' + id, {
      method: 'DELETE', headers: agAuthHeaders(),
    });
    const j = await r.json();
    if (!r.ok) { alert('Gagal: ' + ((j.error && j.error.message) || j.detail || j.message || 'unknown')); return; }
    curlLoad();
  } catch (e) {
    alert('Gagal: ' + e.message);
  }
}

async function curlExecute(id) {
  if (!agToken()) { alert('Isi token login (Bearer) dulu'); return; }
  const resultEl = document.getElementById('curl-result-' + id);
  resultEl.innerHTML = '<div style="font-size:0.72rem;color:#60a5fa">Menjalankan...</div>';
  try {
    const r = await fetch(window.location.origin + '/api/v1/agent-curl-targets/' + id + '/execute', {
      method: 'POST', headers: agAuthHeaders(),
    });
    const j = await r.json();
    if (!r.ok) {
      resultEl.innerHTML = '<div style="font-size:0.72rem;color:#f87171">Gagal: ' + ((j.error && j.error.message) || j.detail || j.message || 'unknown') + '</div>';
      return;
    }
    const d = j.data;
    if (d.success) {
      resultEl.innerHTML =
        '<div style="font-size:0.72rem;color:#4ade80;margin-bottom:4px">Berhasil -- HTTP ' + d.status_code + ' (' + d.response_length + ' karakter respons)</div>' +
        '<div style="font-size:0.68rem;color:#64748b;margin-bottom:4px">URL yg benar-benar dikirim: ' + d.resolved_url + '</div>' +
        '<pre style="background:#0f172a;border:1px solid #334155;border-radius:4px;padding:8px;font-size:0.7rem;color:#94a3b8;overflow-x:auto;white-space:pre-wrap;word-break:break-all;max-height:200px">' + d.response_preview.replace(/</g, '&lt;') + '</pre>';
    } else {
      resultEl.innerHTML =
        '<div style="font-size:0.72rem;color:#f87171;margin-bottom:4px">Gagal dijalankan: ' + d.error + '</div>' +
        '<div style="font-size:0.68rem;color:#64748b">URL yg dicoba: ' + d.resolved_url + '</div>';
    }
  } catch (e) {
    resultEl.innerHTML = '<div style="font-size:0.72rem;color:#f87171">Gagal: ' + e.message + '</div>';
  }
}

// ── Tab Rotasi API Key (2026-07-22) ──
async function rotasiLoad() {
  const msgEl = document.getElementById('rotasi-msg');
  msgEl.style.color = '#60a5fa';
  msgEl.textContent = 'Memuat...';
  try {
    const r = await fetch(window.location.origin + '/api/v1/rotation-bank', { headers: agAuthHeaders() });
    const j = await r.json();
    if (!r.ok) {
      msgEl.style.color = '#f87171';
      msgEl.textContent = 'Gagal: ' + ((j.error && j.error.message) || j.detail || j.message || 'isi token login dulu');
      return;
    }
    const keys = j.data.keys;
    if (keys.length === 0) {
      document.getElementById('rotasi-list').innerHTML = '<div style="color:#475569;font-style:italic;font-size:0.82rem">Belum ada key di bank rotasi. Tambah lewat form di atas.</div>';
      msgEl.style.color = '#64748b';
      msgEl.textContent = 'Terakhir dimuat: ' + new Date().toLocaleTimeString('id-ID') + ' (0 key)';
      return;
    }
    const statusColor = { available: '#166534', assigned: '#1e3a5f', exhausted: '#7f1d1d', disabled: '#334155' };
    const statusLabel = { available: 'tersedia', assigned: 'terpakai', exhausted: 'habis/gagal', disabled: 'dimatikan' };
    const renderCard = (k, isLog) => `
      <div style="background:#1e293b;border-radius:8px;padding:12px 16px;margin-bottom:10px">
        <div style="margin-bottom:6px;display:flex;align-items:center;gap:6px;flex-wrap:wrap">
          <span class="pill" style="background:#1e3a5f;color:#60a5fa">${k.provider}</span>
          ${k.model ? `<span class="pill" style="background:#334155;color:#e2e8f0">${k.model}</span>` : '<span style="font-size:0.7rem;color:#475569;font-style:italic">model tidak diisi</span>'}
          <span class="pill" style="background:${statusColor[k.status] || '#334155'};color:#fff">${statusLabel[k.status] || k.status}</span>
          ${k.assigned_to_agent ? `<span class="pill" style="background:#3a2f18;color:#d1a441">dipakai: ${k.assigned_to_agent}</span>` : ''}
          <div style="margin-left:auto;display:flex;gap:6px">
            ${isLog
              ? `<button class="retry-btn" style="padding:4px 10px;font-size:0.7rem;background:#166534" onclick="rotasiReset('${k.id}')">Reload (masuk antrian lagi)</button>`
              : `<button class="retry-btn" style="padding:4px 10px;font-size:0.7rem;background:#78350f" onclick="rotasiDisable('${k.id}')">Matikan</button>`}
            <button class="retry-btn" style="padding:4px 10px;font-size:0.7rem;background:#7f1d1d" onclick="rotasiDelete('${k.id}')">Hapus</button>
          </div>
        </div>
        <div style="font-size:0.75rem;color:#94a3b8;margin-bottom:4px">
          Key: <span style="font-family:monospace">${k.masked_key}</span>
          ${k.account_email ? ' &middot; Akun: ' + k.account_email : ''}
        </div>
        ${k.last_error ? `<div style="font-size:0.7rem;color:#c96f5c">Error terakhir: ${k.last_error.slice(0, 200)}</div>` : ''}
      </div>`;

    const activeKeys = keys.filter(k => k.status === 'available' || k.status === 'assigned');
    const logKeys = keys.filter(k => k.status === 'exhausted' || k.status === 'disabled');

    let html = '';
    html += `<div style="font-size:0.78rem;font-weight:600;color:#60a5fa;margin-bottom:6px">Aktif (${activeKeys.length})</div>`;
    html += activeKeys.length
      ? activeKeys.map(k => renderCard(k, false)).join('')
      : '<div style="color:#475569;font-style:italic;font-size:0.82rem;margin-bottom:14px">Tidak ada key aktif.</div>';
    html += `<div style="font-size:0.78rem;font-weight:600;color:#d1a441;margin:16px 0 6px">Log: Sudah Habis / Diganti (${logKeys.length})</div>`;
    html += logKeys.length
      ? logKeys.map(k => renderCard(k, true)).join('')
      : '<div style="color:#475569;font-style:italic;font-size:0.82rem">Belum ada key yg habis/diganti.</div>';

    document.getElementById('rotasi-list').innerHTML = html;
    msgEl.style.color = '#64748b';
    msgEl.textContent = 'Terakhir dimuat: ' + new Date().toLocaleTimeString('id-ID') + ` (${keys.length} key)`;
  } catch (e) {
    msgEl.style.color = '#f87171';
    msgEl.textContent = 'Gagal: ' + e.message;
  }
}

async function rotasiAddNew() {
  const provider = document.getElementById('rotasi-new-provider').value.trim();
  const apiKey = document.getElementById('rotasi-new-apikey').value.trim();
  if (!provider || !apiKey) { alert('Provider dan API key wajib diisi'); return; }
  if (!agToken()) { alert('Isi token login (Bearer) dulu'); return; }
  const body = {
    provider, api_key: apiKey,
    model: document.getElementById('rotasi-new-model').value.trim() || null,
    account_email: document.getElementById('rotasi-new-account').value.trim() || null,
  };
  const msgEl = document.getElementById('rotasi-add-msg');
  try {
    const r = await fetch(window.location.origin + '/api/v1/rotation-bank', {
      method: 'POST', headers: agAuthHeaders(), body: JSON.stringify(body),
    });
    const j = await r.json();
    if (!r.ok) {
      msgEl.style.color = '#f87171';
      msgEl.textContent = 'Gagal: ' + ((j.error && j.error.message) || j.detail || j.message || 'unknown');
      return;
    }
    msgEl.style.color = '#4ade80';
    msgEl.textContent = 'Key ditambahkan ke bank!';
    ['provider', 'apikey', 'model', 'account'].forEach(f => {
      document.getElementById('rotasi-new-' + f).value = '';
    });
    rotasiLoad();
  } catch (e) {
    msgEl.style.color = '#f87171';
    msgEl.textContent = 'Gagal: ' + e.message;
  }
}

async function rotasiDisable(id) {
  if (!agToken()) { alert('Isi token login (Bearer) dulu'); return; }
  try {
    const r = await fetch(window.location.origin + '/api/v1/rotation-bank/' + id + '/disable', {
      method: 'POST', headers: agAuthHeaders(),
    });
    const j = await r.json();
    if (!r.ok) { alert('Gagal: ' + ((j.error && j.error.message) || j.detail || j.message || 'unknown')); return; }
    rotasiLoad();
  } catch (e) {
    alert('Gagal: ' + e.message);
  }
}

async function rotasiReset(id) {
  if (!agToken()) { alert('Isi token login (Bearer) dulu'); return; }
  try {
    const r = await fetch(window.location.origin + '/api/v1/rotation-bank/' + id + '/reset', {
      method: 'POST', headers: agAuthHeaders(),
    });
    const j = await r.json();
    if (!r.ok) { alert('Gagal: ' + ((j.error && j.error.message) || j.detail || j.message || 'unknown')); return; }
    rotasiLoad();
  } catch (e) {
    alert('Gagal: ' + e.message);
  }
}

async function rotasiDelete(id) {
  if (!agToken()) { alert('Isi token login (Bearer) dulu'); return; }
  if (!confirm('Hapus key ini dari bank rotasi? Tidak bisa dibatalkan.')) return;
  try {
    const r = await fetch(window.location.origin + '/api/v1/rotation-bank/' + id, {
      method: 'DELETE', headers: agAuthHeaders(),
    });
    const j = await r.json();
    if (!r.ok) { alert('Gagal: ' + ((j.error && j.error.message) || j.detail || j.message || 'unknown')); return; }
    rotasiLoad();
  } catch (e) {
    alert('Gagal: ' + e.message);
  }
}

const savedToken = localStorage.getItem('ag_token');
if (savedToken) { document.getElementById('ag-token').value = savedToken; agRegLoad(); }
</script>

</body>
</html>"""
    return HTMLResponse(content=html)


# ── API v1 Routers (SISA: auth, users, credentials, agent_registry) ───────────
API_PREFIX = "/api/v1"

app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(users.router, prefix=API_PREFIX)
app.include_router(credentials.router, prefix=API_PREFIX)
app.include_router(agent_registry.router, prefix=API_PREFIX)
app.include_router(third_party_apis.router, prefix=API_PREFIX)
app.include_router(agent_curl_targets.router, prefix=API_PREFIX)
app.include_router(youtube_pipeline.router, prefix=API_PREFIX)
app.include_router(youtube_metadata.router, prefix=API_PREFIX)
app.include_router(rotation_key_bank.router, prefix=API_PREFIX)
app.include_router(trend_recommendations.router, prefix=API_PREFIX)
app.include_router(tiktok_pipeline.router, prefix=API_PREFIX)
