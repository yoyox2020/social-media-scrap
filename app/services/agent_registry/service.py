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
            "description": row.description, "parent_agent_name": row.parent_agent_name, "keys": [],
        })
        if row.linked_credential_id and row.linked_credential_id in cred_by_id:
            entry = cred_by_id[row.linked_credential_id]
            value = await get_credential_value(entry)
            bucket["keys"].append({
                "id": str(row.id), "key_label": row.key_label,
                "masked_value": mask_value(value), "is_set": bool(value),
                "linked_credential_id": row.linked_credential_id,
                "model": None, "account_email": row.account_email, "editable_here": False,
                "note": "Diedit lewat /manage-api-keys (shared/pool) atau tab Pengaturan agent ini.",
            })
        else:
            bucket["keys"].append({
                "id": str(row.id), "key_label": row.key_label,
                "masked_value": mask_value(row.custom_api_key), "is_set": bool(row.custom_api_key),
                "linked_credential_id": None,
                "model": row.custom_model, "account_email": row.account_email, "editable_here": True,
                "note": "Agent custom -- belum tentu py kode scraping asli, ini cuma pencatatan." if row.is_custom else None,
            })
    return list(agents.values())


async def add_custom_agent(
    db: AsyncSession, agent_name: str, category: str, description: str | None,
    key_label: str, api_key: str | None, model: str | None, account_email: str | None = None,
    parent_agent_name: str | None = None,
) -> AgentRegistryEntry:
    """Registrasi agent BARU dari form dashboard -- key/model disimpan
    LANGSUNG di baris ini (tidak ada kode scraping otomatis yang
    terbentuk, murni pencatatan/rencana sampai ada kode asli ditulis).
    `parent_agent_name` opsional -- isi dgn agent_name INDUK kalau ini
    child (mis. "agent_youtube01" -> parent "agent_youtube")."""
    now = datetime.now(timezone.utc)
    entry = AgentRegistryEntry(
        agent_name=agent_name.strip(), category=category.strip() or "Umum",
        description=(description or "").strip() or None,
        key_label=key_label.strip() or "API Key",
        account_email=(account_email or "").strip() or None,
        parent_agent_name=(parent_agent_name or "").strip() or None,
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
    account_email: str | None = None,
) -> AgentRegistryEntry | None:
    """Ganti key/model/akun utk baris CUSTOM (linked_credential_id NULL) --
    utk baris yang linked ke credential existing, ganti lewat
    /api/v1/credentials/{id} yang sudah ada, BUKAN lewat sini."""
    entry = await db.get(AgentRegistryEntry, entry_id)
    if not entry or entry.linked_credential_id is not None:
        return None
    if api_key is not None:
        entry.custom_api_key = api_key.strip() or None
    if model is not None:
        entry.custom_model = model.strip() or None
    if account_email is not None:
        entry.account_email = account_email.strip() or None
    entry.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(entry)
    return entry


async def get_key_for_agent(db: AsyncSession, agent_name: str) -> dict | None:
    """Ambil key AKTIF SEKARANG milik 1 agent (dipanggil oleh kode
    agent/pipeline yg BENERAN jalan, 2026-07-22) -- SELALU query ulang
    ke DB tiap dipanggil, TIDAK di-cache, supaya kalau user ganti key
    lewat dashboard, pemanggilan BERIKUTNYA otomatis pakai yg baru
    tanpa perlu redeploy kode. Ambil baris PERTAMA yg enabled+ada key."""
    entries = (await db.scalars(
        select(AgentRegistryEntry).where(
            AgentRegistryEntry.agent_name == agent_name.strip(),
            AgentRegistryEntry.enabled.is_(True),
        )
    )).all()
    for entry in entries:
        if entry.linked_credential_id:
            from app.services.credentials import registry as cred_registry
            cred_entry = next((e for e in cred_registry.ALL_ENTRIES if e.id == entry.linked_credential_id), None)
            if cred_entry:
                value = await cred_registry.get_credential_value(cred_entry)
                if value:
                    return {"api_key": value, "model": entry.custom_model, "account_email": entry.account_email}
        elif entry.custom_api_key:
            return {"api_key": entry.custom_api_key, "model": entry.custom_model, "account_email": entry.account_email}

    # Fallback: cek katalog "API Pihak Ketiga" (third_party_apis) -- agent
    # bisa juga dapat key dari sana (link 1:1), BUKAN cuma dari baris
    # agent_registry-nya sendiri. Ditambah 2026-07-22 krn user simpan
    # sebagian key lewat katalog itu, bukan langsung di sini.
    from app.domain.third_party_apis.models import ThirdPartyApi, ThirdPartyApiAgentLink
    link = await db.scalar(
        select(ThirdPartyApiAgentLink).where(ThirdPartyApiAgentLink.agent_name == agent_name.strip())
    )
    if link:
        api = await db.get(ThirdPartyApi, link.third_party_api_id)
        if api and api.enabled and api.api_key:
            return {"api_key": api.api_key, "model": None, "account_email": api.account_email}
    return None


async def get_enabled_children(db: AsyncSession, parent_agent_name: str) -> list[str]:
    """Nama semua child agent yg AKTIF (enabled=True) milik 1 parent --
    dipakai utk membagi kerja (mis. keyword pencarian) antar child."""
    rows = (await db.scalars(
        select(AgentRegistryEntry.agent_name).where(
            AgentRegistryEntry.parent_agent_name == parent_agent_name.strip(),
            AgentRegistryEntry.enabled.is_(True),
        ).distinct()
    )).all()
    return list(rows)


async def delete_agent_entry(db: AsyncSession, entry_id: uuid.UUID) -> bool:
    entry = await db.get(AgentRegistryEntry, entry_id)
    if not entry:
        return False
    await db.delete(entry)
    await db.commit()
    return True
