"""
Monitoring kuota YouTube Data API v3 -- permintaan user 2026-07-20
("buatkan monitoringnya pengaturannya di scraping-status agar kita bisa
maintenance ganti dan pastikan dia berotasi", lalu diperjelas "tanpa
ganggu system yg existing di api, jd fokus di scraping-status monitoring
kan").

SENGAJA TIDAK menyentuh titik panggilan agent manapun (Discovery
Agent 1/2, Metadata Agent, Views Refresh Agent) -- modul ini CUMA baca
key yg SUDAH dikonfigurasi tiap agent (get_youtube_api_key() masing2,
persis fungsi yg SUDAH dipakai agent itu sendiri) lalu melakukan SATU
panggilan tes ringan (videos.list, 1 unit kuota) ke YouTube Data API v3
untuk mengecek status LANGSUNG SEKARANG (OK / kuota habis / rate-limit /
key tidak valid). Read-only, tidak mengubah perilaku pipeline manapun.
"""
from __future__ import annotations

import httpx

_BASE_URL = "https://www.googleapis.com/youtube/v3"
# Video publik yg (hampir) pasti selalu ada -- dipakai sbg target tes
# videos.list?part=id, cuma 1 unit kuota per cek, termurah yg tersedia.
_PROBE_VIDEO_ID = "dQw4w9WgXcQ"


async def _resolve_slots() -> list[dict]:
    """Kumpulkan slot key YouTube yg SUDAH ada di aplikasi (baca via
    fungsi get_* masing2 config module, TIDAK duplikasi logic resolusi
    key apa pun)."""
    from app.services.youtube_discovery import config as da1_cfg
    from app.services.youtube_discovery import agent2_config as da2_cfg
    from app.services.views_refresh_agent import config as vr_cfg
    from app.shared.config import settings

    global_key = settings.youtube_data_api_key or None
    da1_key = (await da1_cfg.get_youtube_api_key()) or global_key
    da2_key = await da2_cfg.get_youtube_api_key()
    vr_key = (await vr_cfg.get_api_key()) or None

    return [
        {"id": "yt_discovery1_youtube", "label": "Discovery Agent 1", "key": da1_key},
        {"id": "yt_discovery2_youtube", "label": "Discovery Agent 2", "key": da2_key},
        {"id": "views_refresh_youtube", "label": "Views Refresh Agent", "key": vr_key},
        {"id": "youtube_data_api_key", "label": "Global / Metadata Agent (fallback)", "key": global_key},
    ]


def _mask(key: str | None) -> str | None:
    if not key:
        return None
    if len(key) <= 4:
        return "*" * len(key)
    return "*" * (len(key) - 4) + key[-4:]


async def _probe_key(key: str) -> dict:
    """Satu panggilan tes ringan, kembalikan status terkini key ini."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{_BASE_URL}/videos",
                params={"part": "id", "id": _PROBE_VIDEO_ID, "key": key},
            )
    except httpx.RequestError as exc:
        return {"status": "error", "detail": f"Gagal menghubungi Google: {exc}"}

    if resp.status_code == 200:
        return {"status": "ok", "detail": "Normal"}

    body = resp.text[:300]
    body_lower = body.lower()
    if resp.status_code == 403 and ("quotaexceeded" in body_lower or "dailylimitexceeded" in body_lower):
        return {"status": "quota_exceeded", "detail": "Kuota harian habis (403 quotaExceeded)"}
    if resp.status_code == 403 and "keyinvalid" in body_lower:
        return {"status": "invalid_key", "detail": "Key tidak valid/API belum di-enable"}
    if resp.status_code == 429:
        return {"status": "rate_limited", "detail": "429 Too Many Requests (rate-limit sesaat)"}
    if resp.status_code == 403:
        return {"status": "forbidden", "detail": f"403: {body}"}
    return {"status": "error", "detail": f"HTTP {resp.status_code}: {body}"}


async def check_all_keys_health() -> dict:
    """Cek status TERKINI semua slot key YouTube yg dikenal aplikasi ini.
    Key yg SAMA dipakai >1 slot cuma di-tes SEKALI (hemat kuota), hasilnya
    dipakai bersama utk semua slot yg berbagi key tsb."""
    slots = await _resolve_slots()

    unique_keys = list({s["key"] for s in slots if s["key"]})
    results_by_key: dict[str, dict] = {}
    for key in unique_keys:
        results_by_key[key] = await _probe_key(key)

    items = []
    for slot in slots:
        key = slot["key"]
        shared_with = [
            other["label"] for other in slots
            if other["id"] != slot["id"] and other["key"] and other["key"] == key
        ]
        if not key:
            health = {"status": "not_set", "detail": "Belum diisi"}
        else:
            health = results_by_key[key]
        items.append({
            "id": slot["id"],
            "label": slot["label"],
            "masked_key": _mask(key),
            "shared_with": shared_with,
            "status": health["status"],
            "detail": health["detail"],
        })

    return {"items": items}
