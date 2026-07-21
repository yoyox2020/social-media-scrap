"""
"Kelola Agent" -- katalog terpusat semua agent AI (2026-07-22, permintaan
user). Mengelompokkan key/model per agent dalam satu tampilan, TANPA
menduplikasi penyimpanan key yang sudah ada di Redis (lihat docstring
app/domain/agent_registry/models.py).

SEED_AGENTS: daftar AWAL agent yang SUDAH py kode scraping asli, disamakan
dengan app/services/credentials/registry.py -- dijalankan SEKALI (idempotent,
cek nama+key_label dulu sebelum insert) lewat `ensure_seeded()`, supaya
tabel ini otomatis terisi tanpa migrasi data manual/kaku.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_registry.models import AgentRegistryEntry

SEED_AGENTS: list[dict] = [
    {"agent_name": "YouTube Discovery Agent 1", "category": "YouTube",
     "description": "Cari video viral/trending baru otomatis (mode bebas + topic-guided), validasi kandidat via LLM.",
     "key_label": "YouTube Data API", "linked_credential_id": "yt_discovery1_youtube"},
    {"agent_name": "YouTube Discovery Agent 1", "category": "YouTube",
     "description": "Cari video viral/trending baru otomatis (mode bebas + topic-guided), validasi kandidat via LLM.",
     "key_label": "OpenRouter (utama)", "linked_credential_id": "yt_discovery1_openrouter"},
    {"agent_name": "YouTube Discovery Agent 1", "category": "YouTube",
     "description": "Cari video viral/trending baru otomatis (mode bebas + topic-guided), validasi kandidat via LLM.",
     "key_label": "OpenRouter (cadangan)", "linked_credential_id": "yt_discovery1_openrouter_fallback"},

    {"agent_name": "YouTube Discovery Agent 2", "category": "YouTube",
     "description": "SAMA seperti Agent 1, TERPISAH TOTAL, HANYA topic-guided, jadwal tiap 1 jam.",
     "key_label": "YouTube Data API", "linked_credential_id": "yt_discovery2_youtube"},
    {"agent_name": "YouTube Discovery Agent 2", "category": "YouTube",
     "description": "SAMA seperti Agent 1, TERPISAH TOTAL, HANYA topic-guided, jadwal tiap 1 jam.",
     "key_label": "OpenRouter", "linked_credential_id": "yt_discovery2_openrouter"},

    {"agent_name": "YouTube Metadata Agent", "category": "YouTube",
     "description": "Lengkapi info video+channel+komentar dari YouTube API + viral_context (LLM).",
     "key_label": "OpenRouter", "linked_credential_id": "yt_metadata_openrouter"},
    {"agent_name": "YouTube Metadata Agent", "category": "YouTube",
     "description": "Lengkapi info video+channel+komentar dari YouTube API + viral_context (LLM).",
     "key_label": "YouTube Data API (global, fallback)", "linked_credential_id": "youtube_data_api_key"},

    {"agent_name": "Views Refresh Agent", "category": "YouTube",
     "description": "HANYA update views/likes/comments (cepat), kuota YouTube API terpisah dari Metadata Agent.",
     "key_label": "YouTube Data API", "linked_credential_id": "views_refresh_youtube"},

    {"agent_name": "Sentiment Agent", "category": "Sentimen",
     "description": "Opini kedua LLM utk komentar yang lexicon rule-based kemungkinan salah label.",
     "key_label": "OpenRouter (primer)", "linked_credential_id": "sentiment_openrouter"},
    {"agent_name": "Sentiment Agent", "category": "Sentimen",
     "description": "Opini kedua LLM utk komentar yang lexicon rule-based kemungkinan salah label.",
     "key_label": "OpenRouter (tie-breaker)", "linked_credential_id": "sentiment_openrouter_tiebreaker"},

    {"agent_name": "Instagram Thumbnail Backfill Agent", "category": "Instagram",
     "description": "Isi ulang foto post Instagram lama, provider diacak Apify/EnsembleData per akun. Pakai POOL rotasi (bukan 1 token), lihat /manage-api-keys utk kelola semua token.",
     "key_label": "Apify (pool)", "linked_credential_id": "apify_api_token"},
    {"agent_name": "Instagram Thumbnail Backfill Agent", "category": "Instagram",
     "description": "Isi ulang foto post Instagram lama, provider diacak Apify/EnsembleData per akun. Pakai POOL rotasi (bukan 1 token), lihat /manage-api-keys utk kelola semua token.",
     "key_label": "EnsembleData (pool)", "linked_credential_id": "ensemble_data_api_token"},

    {"agent_name": "Threads", "category": "Threads",
     "description": "Scrape post+balasan berbasis keyword (tier cache->live->antrian). Pakai POOL rotasi EnsembleData, lihat /manage-api-keys.",
     "key_label": "EnsembleData (pool)", "linked_credential_id": "ensemble_data_api_token"},
]


async def ensure_seeded(db: AsyncSession) -> int:
    """Isi baris awal (idempotent) kalau tabel masih kosong utk
    agent_name+key_label tsb -- aman dipanggil berkali-kali."""
    existing = (await db.execute(select(AgentRegistryEntry.agent_name, AgentRegistryEntry.key_label))).all()
    existing_pairs = {(r[0], r[1]) for r in existing}

    inserted = 0
    now = datetime.now(timezone.utc)
    for seed in SEED_AGENTS:
        key = (seed["agent_name"], seed["key_label"])
        if key in existing_pairs:
            continue
        db.add(AgentRegistryEntry(
            agent_name=seed["agent_name"], category=seed["category"],
            description=seed["description"], key_label=seed["key_label"],
            linked_credential_id=seed["linked_credential_id"],
            is_custom=False, enabled=True, created_at=now, updated_at=now,
        ))
        inserted += 1
    if inserted:
        await db.commit()
    return inserted


async def list_agents(db: AsyncSession) -> list[dict]:
    """Kelompokkan baris per agent_name -- 1 agent bisa py beberapa key."""
    from app.services.credentials.registry import ALL_ENTRIES, get_credential_value, mask_value

    await ensure_seeded(db)
    rows = (await db.scalars(select(AgentRegistryEntry).order_by(AgentRegistryEntry.agent_name, AgentRegistryEntry.created_at))).all()

    cred_by_id = {e.id: e for e in ALL_ENTRIES}
    agents: dict[str, dict] = {}
    for row in rows:
        bucket = agents.setdefault(row.agent_name, {
            "agent_name": row.agent_name, "category": row.category,
            "description": row.description, "keys": [],
        })
        if row.linked_credential_id and row.linked_credential_id in cred_by_id:
            entry = cred_by_id[row.linked_credential_id]
            value = await get_credential_value(entry)
            bucket["keys"].append({
                "id": str(row.id), "key_label": row.key_label,
                "masked_value": mask_value(value), "is_set": bool(value),
                "linked_credential_id": row.linked_credential_id,
                "model": None, "editable_here": False,
                "note": "Diedit lewat /manage-api-keys (shared/pool) atau tab Pengaturan agent ini.",
            })
        else:
            bucket["keys"].append({
                "id": str(row.id), "key_label": row.key_label,
                "masked_value": mask_value(row.custom_api_key), "is_set": bool(row.custom_api_key),
                "linked_credential_id": None,
                "model": row.custom_model, "editable_here": True,
                "note": "Agent custom -- belum tentu py kode scraping asli, ini cuma pencatatan." if row.is_custom else None,
            })
    return list(agents.values())


async def add_custom_agent(
    db: AsyncSession, agent_name: str, category: str, description: str | None,
    key_label: str, api_key: str | None, model: str | None,
) -> AgentRegistryEntry:
    """Registrasi agent BARU dari form dashboard -- key/model disimpan
    LANGSUNG di baris ini (tidak ada kode scraping otomatis yang
    terbentuk, murni pencatatan/rencana sampai ada kode asli ditulis)."""
    now = datetime.now(timezone.utc)
    entry = AgentRegistryEntry(
        agent_name=agent_name.strip(), category=category.strip() or "Umum",
        description=(description or "").strip() or None,
        key_label=key_label.strip() or "API Key",
        linked_credential_id=None,
        custom_api_key=(api_key or "").strip() or None,
        custom_model=(model or "").strip() or None,
        is_custom=True, enabled=True, created_at=now, updated_at=now,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


async def update_custom_agent_key(
    db: AsyncSession, entry_id: uuid.UUID, api_key: str | None, model: str | None,
) -> AgentRegistryEntry | None:
    """Ganti key/model utk baris CUSTOM (linked_credential_id NULL) --
    utk baris yang linked ke credential existing, ganti lewat
    /api/v1/credentials/{id} yang sudah ada, BUKAN lewat sini."""
    entry = await db.get(AgentRegistryEntry, entry_id)
    if not entry or entry.linked_credential_id is not None:
        return None
    if api_key is not None:
        entry.custom_api_key = api_key.strip() or None
    if model is not None:
        entry.custom_model = model.strip() or None
    entry.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(entry)
    return entry


async def delete_agent_entry(db: AsyncSession, entry_id: uuid.UUID) -> bool:
    entry = await db.get(AgentRegistryEntry, entry_id)
    if not entry:
        return False
    await db.delete(entry)
    await db.commit()
    return True
