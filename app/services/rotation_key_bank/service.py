"""Rotasi API key OTOMATIS lintas agent (2026-07-22, permintaan user)
-- bank key BERSAMA (OpenRouter/Grok/dll), dipakai SEMUA agent yg
enabled=True DAN sudah punya key terpasang (BUKAN agent kosong).

Alur: agent jalan pakai `get_working_key_for_agent()` (bukan langsung
`agent_registry.get_key_for_agent()`) -- kalau bank sudah pernah
assign key pengganti ke agent ini, itu yg dipakai; kalau belum, key
ASLI di agent_registry yg dipakai (TIDAK ada perubahan perilaku kalau
belum pernah gagal). Begitu kode agent lapor kegagalan nyata (401/402/
429/dst) via `report_key_failure()`, sistem OTOMATIS: tandai key
assigned SEKARANG (kalau ada) exhausted, ambil 1 key `available`
berikutnya dari bank, assign ke agent itu -- run BERIKUTNYA otomatis
pakai key baru, TANPA klik manual."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_registry.models import AgentRegistryEntry
from app.domain.rotation_key_bank.models import RotationKeyBank
from app.services.agent_registry.service import get_key_for_agent


async def add_bank_key(
    db: AsyncSession, provider: str, api_key: str, model: str | None = None, account_email: str | None = None,
) -> RotationKeyBank:
    now = datetime.now(timezone.utc)
    entry = RotationKeyBank(
        provider=provider.strip(), api_key=api_key.strip(),
        model=(model or "").strip() or None, account_email=(account_email or "").strip() or None,
        status="available", created_at=now, updated_at=now,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


def _mask(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


async def list_bank_keys(db: AsyncSession) -> list[dict]:
    rows = (await db.scalars(select(RotationKeyBank).order_by(RotationKeyBank.created_at))).all()
    return [
        {
            "id": str(r.id), "provider": r.provider, "masked_key": _mask(r.api_key),
            "model": r.model, "account_email": r.account_email, "status": r.status,
            "assigned_to_agent": r.assigned_to_agent,
            "assigned_at": r.assigned_at.isoformat() if r.assigned_at else None,
            "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
            "last_error": r.last_error,
        }
        for r in rows
    ]


async def delete_bank_key(db: AsyncSession, key_id: uuid.UUID) -> bool:
    entry = await db.get(RotationKeyBank, key_id)
    if not entry:
        return False
    await db.delete(entry)
    await db.commit()
    return True


async def disable_bank_key(db: AsyncSession, key_id: uuid.UUID) -> RotationKeyBank | None:
    entry = await db.get(RotationKeyBank, key_id)
    if not entry:
        return None
    entry.status = "disabled"
    entry.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(entry)
    return entry


async def reset_bank_key(db: AsyncSession, key_id: uuid.UUID) -> RotationKeyBank | None:
    """Kembalikan 1 key (exhausted/disabled) jadi 'available' lagi --
    masuk antrian rotasi dari awal, lepas dari agent yg tadi
    pakai (kalau ada), bersihkan last_error."""
    entry = await db.get(RotationKeyBank, key_id)
    if not entry:
        return None
    entry.status = "available"
    entry.assigned_to_agent = None
    entry.assigned_at = None
    entry.last_error = None
    entry.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(entry)
    return entry


async def get_working_key_for_agent(db: AsyncSession, agent_name: str) -> dict | None:
    """Key yg BENERAN dipakai agent ini sekarang -- cek dulu apakah
    bank sudah assign key pengganti (krn key asli pernah dilaporkan
    mati), kalau belum pakai key ASLI dari agent_registry (perilaku
    default, tidak berubah)."""
    assigned = await db.scalar(
        select(RotationKeyBank).where(
            RotationKeyBank.assigned_to_agent == agent_name.strip(),
            RotationKeyBank.status == "assigned",
        )
    )
    if assigned:
        assigned.last_used_at = datetime.now(timezone.utc)
        await db.commit()
        return {"api_key": assigned.api_key, "model": assigned.model, "account_email": assigned.account_email, "source": "rotation_bank"}
    return await get_key_for_agent(db, agent_name)


async def report_key_failure(db: AsyncSession, agent_name: str, error_message: str) -> dict | None:
    """Dipanggil kode agent SAAT BENERAN gagal pakai key-nya (401/402/
    429/dst). Cuma proses agent yg enabled=True DAN sudah punya key
    terpasang (bukan agent kosong) -- sesuai keputusan user. Balikin
    key baru kalau berhasil dapat pengganti, None kalau bank kosong
    atau agent tidak memenuhi syarat."""
    agent_name = agent_name.strip()

    entry = await db.scalar(
        select(AgentRegistryEntry).where(AgentRegistryEntry.agent_name == agent_name, AgentRegistryEntry.enabled.is_(True))
    )
    if not entry:
        return None
    current = await get_working_key_for_agent(db, agent_name)
    if not current or not current.get("api_key"):
        return None  # agent kosong -- bukan cakupan rotasi ini

    old_assigned = await db.scalar(
        select(RotationKeyBank).where(
            RotationKeyBank.assigned_to_agent == agent_name,
            RotationKeyBank.status == "assigned",
        )
    )
    if old_assigned:
        old_assigned.status = "exhausted"
        old_assigned.assigned_to_agent = None
        old_assigned.last_error = error_message[:2000]
        old_assigned.updated_at = datetime.now(timezone.utc)

    replacement = await db.scalar(
        select(RotationKeyBank).where(RotationKeyBank.status == "available").order_by(RotationKeyBank.created_at)
    )
    if not replacement:
        await db.commit()
        return None

    replacement.status = "assigned"
    replacement.assigned_to_agent = agent_name
    replacement.assigned_at = datetime.now(timezone.utc)
    replacement.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(replacement)
    return {"api_key": replacement.api_key, "model": replacement.model, "account_email": replacement.account_email, "source": "rotation_bank"}
