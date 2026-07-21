"""
"Kelola Agent" -- katalog terpusat semua agent AI (2026-07-22, permintaan
user). Mengelompokkan key/model per agent dalam satu tampilan, TANPA
menduplikasi penyimpanan key yang sudah ada di Redis (lihat docstring
app/domain/agent_registry/models.py).

CATATAN RESTRUKTURISASI (2026-07-22): SEMUA agent lama (YouTube
Discovery/Metadata/Views Refresh/Sentiment/Instagram Backfill/Threads)
DIHAPUS kodenya sekaligus (lihat docstring app/main.py) -- SEED_AGENTS
sengaja DIKOSONGKAN, tabel ini mulai dari NOL. Riwayat agent lama tetap
bisa dirujuk di branch `main` (GitHub) kalau perlu. Agent BARU ditambah
lewat form dashboard "Kelola Agent" (POST /api/v1/agent-registry),
TIDAK ada seeding otomatis lagi.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_registry.models import AgentRegistryEntry

SEED_AGENTS: list[dict] = []


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
