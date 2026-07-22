"""agent_youtube -- parent/Coordinator (2026-07-22). Terima keyword dari
agent_search, BAGI ke SEMUA child yg AKTIF (enabled=True) dan PUNYA
CARA KERJA yg jelas -- baik yg py key YouTube Data API asli (dipanggil
via app.agents.youtube.api_client) MAUPUN yg py target curl terdaftar
(dijalankan via app.agents.youtube.crawler_client, keyword disubstitusi
lewat placeholder {{KEYWORD}}). Semua child jalan PARALEL, hasil
digabung, kirim ke agent-struktur-data.

INI YG BIKIN "TANPA HARDCODE": agent BARU (child baru, atau bahkan
platform lain) otomatis IKUT DIPAKAI begitu dia (a) enabled=True di
agent_registry, DAN (b) py salah satu dari: key API asli, ATAU minimal
1 curl target terdaftar. TIDAK perlu tulis kode Python baru per agent
-- cukup daftar via dashboard (Kelola Agent / Target Curl)."""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.activity_log import log_activity
from app.agents.youtube import api_client, crawler_client
from app.services.agent_curl_targets.service import get_targets_for_agent
from app.services.agent_registry.service import get_enabled_children, get_key_for_agent

AGENT_NAME = "agent_youtube"


async def _discover_candidates(db: AsyncSession) -> dict[str, dict]:
    """Semua child enabled=True milik agent_youtube, ditandai KEMAMPUAN
    apa yg dipunyai masing2 (key API asli dan/atau curl target)."""
    children = await get_enabled_children(db, AGENT_NAME)
    candidates: dict[str, dict] = {}
    for name in children:
        key_info = await get_key_for_agent(db, name)
        has_api_key = bool(key_info and api_client.looks_like_youtube_key(key_info.get("api_key")))
        curl_targets = await get_targets_for_agent(db, name)
        has_curl = len(curl_targets) > 0
        if has_api_key or has_curl:
            candidates[name] = {"api": has_api_key, "curl": has_curl}
    return candidates


def _distribute_keywords(keywords: list[str], children: list[str]) -> dict[str, list[str]]:
    """Bagi rata keyword ke child secara round-robin."""
    assignment: dict[str, list[str]] = {c: [] for c in children}
    for i, kw in enumerate(keywords):
        child = children[i % len(children)]
        assignment[child].append(kw)
    return assignment


async def _run_child(db: AsyncSession, agent_name: str, caps: dict, kws: list[str], max_results: int) -> dict:
    """Lacak video dari jalur API dan jalur curl TERPISAH (2026-07-23,
    ditemukan saat audit menyeluruh) -- sebelumnya keduanya digabung
    jadi 1 list lalu SEMUA dilabeli "api_videos" di run_children()
    (crawler_videos selalu dilaporkan 0 walau curl beneran dapat data).
    Totalnya tetap benar (tidak ada video hilang), tapi pelaporan
    asal-data jadi salah -- penting utk monitoring yg diminta user."""
    api_videos: list[dict] = []
    curl_videos: list[dict] = []
    channels: dict = {}
    errors: list[str] = []
    curl_targets_run = 0
    curl_targets_failed = 0

    if caps["api"]:
        for kw in kws:
            r = await api_client.fetch_videos_by_keyword(db, kw, agent_name=agent_name, max_results=max_results)
            if r.get("success"):
                api_videos.extend(r.get("videos", []))
                channels.update(r.get("channels", {}))
            else:
                errors.append(f"[api] {kw}: {r.get('error')}")

    if caps["curl"]:
        r = await crawler_client.fetch_via_curl_targets(db, agent_name, keywords=kws)
        curl_videos.extend(r.get("videos", []))
        channels.update(r.get("channels", {}))
        curl_targets_run += r.get("targets_run", 0)
        curl_targets_failed += r.get("targets_failed", 0)
        for e in r.get("errors", []):
            errors.append(f"[curl] {e.get('target_name')} ({e.get('keyword')}): {e.get('error')}")

    return {
        "agent_name": agent_name, "keywords": kws, "api_videos": api_videos, "curl_videos": curl_videos,
        "channels": channels, "errors": errors,
        "curl_targets_run": curl_targets_run, "curl_targets_failed": curl_targets_failed,
    }


async def run_children(db: AsyncSession, run_id: uuid.UUID, keywords: list[dict], max_results: int = 15) -> dict:
    keyword_list = [k["keyword"] for k in keywords]
    candidates = await _discover_candidates(db)

    if not candidates:
        await log_activity(
            db, run_id, AGENT_NAME, "no_valid_children",
            "Tidak ada child agent_youtube yg aktif+punya key asli/curl target -- pencarian dilewati",
            level="warning",
        )
        return {"api_videos": [], "api_channels": {}, "crawler_videos": [], "crawler_targets_run": 0, "crawler_targets_failed": 0}

    names = list(candidates.keys())
    assignment = _distribute_keywords(keyword_list, names)
    assignment = {k: v for k, v in assignment.items() if v}

    await log_activity(
        db, run_id, AGENT_NAME, "dispatch_children",
        f"Bagi {len(keyword_list)} keyword ke {len(assignment)} child aktif: "
        + ", ".join(f"{name}({'+'.join(k for k,v in candidates[name].items() if v)})={kws}" for name, kws in assignment.items())
        + " -- semua jalan paralel",
    )

    tasks = [_run_child(db, name, candidates[name], kws, max_results) for name, kws in assignment.items()]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_api_videos: list[dict] = []
    all_curl_videos: list[dict] = []
    all_channels: dict = {}
    total_curl_targets_run = 0
    total_curl_targets_failed = 0
    for r in results:
        if isinstance(r, Exception):
            await log_activity(db, run_id, AGENT_NAME, "fetch_error", f"exception: {r}", level="error")
            continue
        total_videos_this_child = len(r["api_videos"]) + len(r["curl_videos"])
        level = "error" if (r["errors"] and not total_videos_this_child) else "info"
        await log_activity(
            db, run_id, r["agent_name"], "fetch_done",
            f"{r['agent_name']} (keyword={r['keywords']}): {total_videos_this_child} video mentah "
            f"(api={len(r['api_videos'])}, curl={len(r['curl_videos'])})"
            + (f", error: {r['errors']}" if r["errors"] else ""),
            level=level,
        )
        all_api_videos.extend(r["api_videos"])
        all_curl_videos.extend(r["curl_videos"])
        all_channels.update(r["channels"])
        total_curl_targets_run += r["curl_targets_run"]
        total_curl_targets_failed += r["curl_targets_failed"]

    await log_activity(
        db, run_id, AGENT_NAME, "children_merged",
        f"Semua child selesai, total video mentah (blm dedupe): {len(all_api_videos) + len(all_curl_videos)} "
        f"(api={len(all_api_videos)}, curl={len(all_curl_videos)})",
    )

    return {
        "api_videos": all_api_videos, "api_channels": all_channels, "crawler_videos": all_curl_videos,
        "crawler_targets_run": total_curl_targets_run, "crawler_targets_failed": total_curl_targets_failed,
    }
