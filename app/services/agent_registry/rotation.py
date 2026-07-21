"""
Rotasi API key per agent (2026-07-22, permintaan user) -- pola SAMA
dgn pool Apify/EnsembleData yg sudah terbukti dipakai sebelumnya:
prioritas token "segar" dulu, tandai exhausted (TTL, auto-pulih sendiri
tanpa reset manual) saat gagal, disabled kalau memang mau dimatikan
permanen, dan "rotate_now" utk paksa ganti manual kapan saja.

Dipakai OLEH KODE AGENT NANTI (belum ada scraping asli sekarang, lihat
project_api_v2_restructure) via pola:

    key = await rotation.get_active_key(db, "agent_youtube")
    if key is None:
        # semua key habis/nonaktif, tidak ada yg bisa dipakai
        ...
    try:
        hasil = panggil_api_pakai(key.api_key, key.model)
        await rotation.mark_success(db, key.id)
    except QuotaError as exc:
        await rotation.mark_exhausted(db, key.id, reason=str(exc))
        # retry dgn get_active_key() lagi -- otomatis dapat key LAIN
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_registry.pool_models import AgentKeyPool

DEFAULT_EXHAUSTED_TTL_HOURS = 24


async def add_key(
    db: AsyncSession, agent_name: str, api_key: str,
    model: str | None = None, account_email: str | None = None, priority: int = 0,
) -> AgentKeyPool:
    """Tambah 1 key kandidat ke pool rotasi agent ini."""
    now = datetime.now(timezone.utc)
    entry = AgentKeyPool(
        agent_name=agent_name.strip(), api_key=api_key.strip(),
        model=(model or "").strip() or None,
        account_email=(account_email or "").strip() or None,
        priority=priority, status="active",
        created_at=now, updated_at=now,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


async def get_active_key(db: AsyncSession, agent_name: str) -> AgentKeyPool | None:
    """Pilih 1 key TERBAIK yg bisa dipakai SEKARANG utk agent ini:
    - status='active' diutamakan
    - status='exhausted' TAPI `exhausted_until` sudah lewat -> otomatis
      dipulihkan (status balik 'active') dan boleh dipakai lagi
    - status='disabled' TIDAK PERNAH dipilih (perlu reset manual)
    Urut: priority ASC, lalu paling lama tidak dipakai (fair rotation)."""
    now = datetime.now(timezone.utc)
    candidates = (await db.scalars(
        select(AgentKeyPool)
        .where(AgentKeyPool.agent_name == agent_name, AgentKeyPool.status != "disabled")
        .order_by(AgentKeyPool.priority.asc(), AgentKeyPool.last_used_at.asc().nulls_first())
    )).all()

    for key in candidates:
        if key.status == "active":
            return key
        if key.status == "exhausted" and key.exhausted_until and key.exhausted_until <= now:
            key.status = "active"
            key.updated_at = now
            await db.commit()
            await db.refresh(key)
            return key
    return None


async def mark_success(db: AsyncSession, key_id: uuid.UUID) -> None:
    """Panggilan pakai key ini BERHASIL -- catat waktu pakai, hapus error lama."""
    key = await db.get(AgentKeyPool, key_id)
    if not key:
        return
    key.last_used_at = datetime.now(timezone.utc)
    key.last_error = None
    if key.status != "disabled":
        key.status = "active"
    key.updated_at = datetime.now(timezone.utc)
    await db.commit()


async def mark_exhausted(
    db: AsyncSession, key_id: uuid.UUID, reason: str | None = None,
    ttl_hours: int = DEFAULT_EXHAUSTED_TTL_HOURS,
) -> AgentKeyPool | None:
    """Key ini kena limit/gagal -- tandai 'exhausted' dgn TTL, OTOMATIS
    pulih sendiri setelah `ttl_hours` (tidak perlu reset manual). Dipanggil
    setelah 1x percobaan gagal krn kuota/rate-limit."""
    key = await db.get(AgentKeyPool, key_id)
    if not key:
        return None
    now = datetime.now(timezone.utc)
    key.status = "exhausted"
    key.exhausted_until = now + timedelta(hours=ttl_hours)
    key.last_used_at = now
    key.last_error = (reason or "")[:1000] or None
    key.updated_at = now
    await db.commit()
    await db.refresh(key)
    return key


async def mark_disabled(db: AsyncSession, key_id: uuid.UUID, reason: str | None = None) -> AgentKeyPool | None:
    """Matikan key ini PERMANEN (mis. memang mau diganti API baru) --
    TIDAK PERNAH otomatis dipulihkan, beda dari exhausted. Perlu
    `reset_key()` manual utk aktifkan lagi."""
    key = await db.get(AgentKeyPool, key_id)
    if not key:
        return None
    key.status = "disabled"
    key.last_error = (reason or "")[:1000] or None
    key.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(key)
    return key


async def reset_key(db: AsyncSession, key_id: uuid.UUID) -> AgentKeyPool | None:
    """Kembalikan key (exhausted ATAU disabled) jadi 'active' lagi,
    hapus status habis/error -- dipakai tombol "Reset" di dashboard."""
    key = await db.get(AgentKeyPool, key_id)
    if not key:
        return None
    key.status = "active"
    key.exhausted_until = None
    key.last_error = None
    key.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(key)
    return key


async def rotate_now(db: AsyncSession, agent_name: str) -> AgentKeyPool | None:
    """Paksa ganti key SEKARANG JUGA (permintaan user: "ingin diganti
    dengan API baru") -- tandai key yg SEDANG aktif (dipakai TERAKHIR)
    sbg exhausted dgn TTL PENDEK (5 menit, bukan 24 jam -- ini bukan
    krn kuota beneran habis, cuma user mau pindah), lalu balikin key
    berikutnya yg tersedia."""
    current = (await db.scalars(
        select(AgentKeyPool)
        .where(AgentKeyPool.agent_name == agent_name, AgentKeyPool.status == "active")
        .order_by(AgentKeyPool.last_used_at.desc().nulls_last())
        .limit(1)
    )).first()
    if current:
        now = datetime.now(timezone.utc)
        current.status = "exhausted"
        current.exhausted_until = now + timedelta(minutes=5)
        current.last_error = "Dirotasi manual oleh user"
        current.updated_at = now
        await db.commit()

    return await get_active_key(db, agent_name)


async def list_pool(db: AsyncSession, agent_name: str) -> list[AgentKeyPool]:
    return list((await db.scalars(
        select(AgentKeyPool)
        .where(AgentKeyPool.agent_name == agent_name)
        .order_by(AgentKeyPool.priority.asc(), AgentKeyPool.created_at.asc())
    )).all())


async def remove_key(db: AsyncSession, key_id: uuid.UUID) -> bool:
    key = await db.get(AgentKeyPool, key_id)
    if not key:
        return False
    await db.delete(key)
    await db.commit()
    return True
