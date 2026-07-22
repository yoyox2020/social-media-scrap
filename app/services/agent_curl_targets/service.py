"""Target curl per agent -- CRUD (2026-07-22). 1 agent bisa punya
BANYAK target, beda dari third_party_apis (1:1).

`resolve_placeholders()` + `execute_target()` (ditambah 2026-07-22,
sesi sama) -- SEBELUM ini placeholder {{NOW}}/{{NOW-Nh}} cuma di-resolve
di JavaScript dashboard (browser), jadi kalau nanti ada worker/agent
Python yg baca `url` mentah dari tabel ini, dia akan dapat literal
teks "{{NOW-24h}}" bukan tanggal beneran -- SALAH. Fungsi di sini
mem-port ulang logika yg SAMA (unit h/d/m) ke Python, dipakai baik oleh
`execute_target()` (test manual via endpoint) MAUPUN oleh agent/worker
mana pun nanti yg mau benar2 menjalankan curl ini terjadwal."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_curl_targets.models import AgentCurlTarget

# Status code yg berarti "key ini kena limit/expired/ditolak" -- SAMA
# persis dgn AI_KEY_FAILURE_STATUS_CODES di agent_struktur_data.py,
# dipakai jg utk auto-rotasi {{ROTATE:<Provider>}} (2026-07-23).
ROTATION_FAILURE_STATUS_CODES = {401, 402, 403, 429}
_ROTATE_RE = re.compile(r"\{\{ROTATE:([A-Za-z0-9_ -]+)\}\}")


async def add_target(
    db: AsyncSession, agent_name: str, name: str, url: str, method: str = "GET",
    headers: str | None = None, body: str | None = None, description: str | None = None,
) -> AgentCurlTarget:
    now = datetime.now(timezone.utc)
    entry = AgentCurlTarget(
        agent_name=agent_name.strip(), name=name.strip(), url=url.strip(),
        method=(method or "GET").strip().upper() or "GET",
        headers=(headers or "").strip() or None,
        body=(body or "").strip() or None,
        description=(description or "").strip() or None,
        enabled=True, created_at=now, updated_at=now,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


async def update_target(
    db: AsyncSession, target_id: uuid.UUID, agent_name: str | None = None, name: str | None = None,
    url: str | None = None, method: str | None = None, headers: str | None = None,
    body: str | None = None, description: str | None = None, enabled: bool | None = None,
) -> AgentCurlTarget | None:
    entry = await db.get(AgentCurlTarget, target_id)
    if not entry:
        return None
    if agent_name is not None:
        entry.agent_name = agent_name.strip()
    if name is not None:
        entry.name = name.strip()
    if url is not None:
        entry.url = url.strip()
    if method is not None:
        entry.method = method.strip().upper() or "GET"
    if headers is not None:
        entry.headers = headers.strip() or None
    if body is not None:
        entry.body = body.strip() or None
    if description is not None:
        entry.description = description.strip() or None
    if enabled is not None:
        entry.enabled = enabled
    entry.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(entry)
    return entry


async def delete_target(db: AsyncSession, target_id: uuid.UUID) -> bool:
    entry = await db.get(AgentCurlTarget, target_id)
    if not entry:
        return False
    await db.delete(entry)
    await db.commit()
    return True


async def list_targets(db: AsyncSession) -> list[dict]:
    targets = (await db.scalars(
        select(AgentCurlTarget).order_by(AgentCurlTarget.agent_name, AgentCurlTarget.created_at)
    )).all()
    return [
        {
            "id": str(t.id), "agent_name": t.agent_name, "name": t.name, "url": t.url,
            "method": t.method, "headers": t.headers, "body": t.body,
            "description": t.description, "enabled": t.enabled,
        }
        for t in targets
    ]


async def get_targets_for_agent(db: AsyncSession, agent_name: str) -> list[AgentCurlTarget]:
    """Dipakai agent/worker Python nanti utk ambil SEMUA target curl
    miliknya sendiri (by nama, cocok pola agent_registry/agent_key_pool)."""
    return list((await db.scalars(
        select(AgentCurlTarget).where(
            AgentCurlTarget.agent_name == agent_name.strip(),
            AgentCurlTarget.enabled.is_(True),
        )
    )).all())


def resolve_placeholders(text: str | None, keyword: str | None = None) -> str | None:
    """Ganti {{NOW}}/{{NOW-<n>h}}/{{NOW-<n>d}}/{{NOW-<n>m}} jadi timestamp
    RFC3339 SUNGGUHAN, dihitung dari waktu saat fungsi ini dipanggil --
    versi Python dari resolveCurlPlaceholders() di app/main.py (JS).
    HARUS disinkronkan manual kalau salah satu diubah.

    {{KEYWORD}} (BARU, 2026-07-22) -- diganti keyword yg SEDANG dibagi
    ke agent ini oleh coordinator (lihat app/agents/youtube/coordinator.py)
    -- di-url-encode dulu krn biasanya dipakai di query string (?q=...).
    Kalau dipanggil TANPA keyword (mis. tombol "Jalankan (Test)" manual
    di dashboard, tidak ada konteks pipeline), placeholder ini dibiarkan
    APA ADANYA (tidak diganti) -- INI YG MEMUNGKINKAN 1 curl target yg
    sama dipakai baik utk test manual (placeholder terlihat) MAUPUN
    dijalankan otomatis oleh pipeline dgn keyword asli."""
    if not text:
        return text
    if keyword:
        from urllib.parse import quote
        text = text.replace("{{KEYWORD}}", quote(keyword))
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    result = text.replace("{{NOW}}", now_str)

    out: list[str] = []
    i = 0
    while True:
        start = result.find("{{NOW-", i)
        if start == -1:
            out.append(result[i:])
            break
        end = result.find("}}", start)
        if end == -1:
            out.append(result[i:])
            break
        out.append(result[i:start])
        token = result[start + 6:end]
        unit = token[-1:] if token else ""
        amount_str = token[:-1] if token else ""
        try:
            amount = int(amount_str)
        except ValueError:
            out.append(result[start:end + 2])
            i = end + 2
            continue
        if unit == "h":
            delta = timedelta(hours=amount)
        elif unit == "d":
            delta = timedelta(days=amount)
        elif unit == "m":
            delta = timedelta(minutes=amount)
        else:
            out.append(result[start:end + 2])
            i = end + 2
            continue
        out.append((now - delta).strftime("%Y-%m-%dT%H:%M:%SZ"))
        i = end + 2
    return "".join(out)


def _parse_headers(headers_text: str | None) -> dict[str, str]:
    result: dict[str, str] = {}
    if not headers_text:
        return result
    for line in headers_text.split("\n"):
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if key:
            result[key] = value.strip()
    return result


async def _resolve_rotating_keys(db: AsyncSession, *texts: str | None) -> tuple[dict[str, str], dict[str, uuid.UUID]]:
    """Cari SEMUA {{ROTATE:<Provider>}} di url/headers/body, ambil 1 key
    yg SEDANG paling layak pakai utk tiap provider yg disebut (lihat
    get_next_available_key -- generik, BUKAN Apify-only). Balikin
    substitusi teks + id key yg dipakai (utk dilaporkan gagal nanti
    kalau requestnya emang gagal)."""
    from app.services.third_party_apis.service import get_next_available_key

    providers: set[str] = set()
    for text in texts:
        if text:
            providers.update(m.strip() for m in _ROTATE_RE.findall(text))

    substitutions: dict[str, str] = {}
    used_key_ids: dict[str, uuid.UUID] = {}
    for provider in providers:
        entry = await get_next_available_key(db, provider)
        if entry:
            substitutions[f"{{{{ROTATE:{provider}}}}}"] = entry.api_key
            used_key_ids[provider] = entry.id
    return substitutions, used_key_ids


def _apply_substitutions(text: str | None, substitutions: dict[str, str]) -> str | None:
    if not text:
        return text
    for placeholder, value in substitutions.items():
        text = text.replace(placeholder, value)
    return text


async def execute_target(db: AsyncSession, target_id: uuid.UUID, keyword: str | None = None) -> dict | None:
    """Jalankan target curl ini SUNGGUHAN -- dipanggil baik dari tombol
    "Jalankan (Test)" dashboard (keyword=None) MAUPUN dari pipeline
    agent (keyword=milik agent ini utk run tsb). Resolve placeholder
    dulu ({{NOW}}/{{KEYWORD}}/{{ROTATE:<Provider>}}/dst), baru kirim
    request beneran, balikin hasil asli (status code + response)
    supaya jelas apakah curl-nya jalan tanpa error atau datanya benar2
    muncul.

    {{ROTATE:<Provider>}} (2026-07-23, permintaan user "auto rotasi
    berapapun API key yang saya daftarkan") -- diganti 1 key yg SEDANG
    paling layak dari katalog `third_party_apis` provider itu. Kalau
    request GAGAL dgn status yg nunjuk key bermasalah (401/402/403/429),
    key itu OTOMATIS dicatat error-nya (mark_api_error) -- panggilan
    BERIKUTNYA (via get_next_available_key) otomatis lompat ke key lain
    yg belum/paling lama error, TANPA campur tangan manual."""
    target = await db.get(AgentCurlTarget, target_id)
    if not target:
        return None

    rotate_substitutions, used_key_ids = await _resolve_rotating_keys(db, target.url, target.headers, target.body)

    url = _apply_substitutions(resolve_placeholders(target.url, keyword=keyword), rotate_substitutions)
    headers_resolved = _apply_substitutions(resolve_placeholders(target.headers, keyword=keyword), rotate_substitutions)
    body_resolved = _apply_substitutions(resolve_placeholders(target.body, keyword=keyword), rotate_substitutions)
    headers_dict = _parse_headers(headers_resolved)

    async def _mark_rotating_keys_failed(error_message: str) -> None:
        from app.services.third_party_apis.service import mark_api_error
        for key_id in used_key_ids.values():
            await mark_api_error(db, key_id, error_message)

    try:
        # 90s (bukan 15s) -- Apify run-sync-get-dataset-items (dipakai TikTok,
        # 2026-07-23) genuinely butuh puluhan detik-2 menit utk 1 actor run
        # sungguhan, 15s selalu timeout sblm actor selesai.
        async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as client:
            resp = await client.request(
                target.method, url, headers=headers_dict,
                content=body_resolved.encode() if body_resolved else None,
            )
        if used_key_ids and resp.status_code in ROTATION_FAILURE_STATUS_CODES:
            await _mark_rotating_keys_failed(f"HTTP {resp.status_code}: {resp.text[:500]}")
        return {
            "success": True,
            "status_code": resp.status_code,
            "resolved_url": url,
            "response_text": resp.text,
            "response_preview": resp.text[:2000],
            "response_length": len(resp.text),
        }
    except Exception as exc:
        if used_key_ids:
            await _mark_rotating_keys_failed(str(exc)[:500])
        return {
            "success": False,
            "status_code": None,
            "resolved_url": url,
            "error": str(exc),
        }
