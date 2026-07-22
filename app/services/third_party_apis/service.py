"""
Katalog API pihak ketiga -- CRUD + link/unlink ke agent (2026-07-22).
Lihat docstring app/domain/third_party_apis/models.py utk desain.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.third_party_apis.models import ThirdPartyApi, ThirdPartyApiAgentLink
from app.shared.exceptions import ConflictError


async def add_api(
    db: AsyncSession, name: str, provider: str, api_key: str | None = None,
    base_url: str | None = None, account_email: str | None = None, description: str | None = None,
    agent_name: str | None = None,
) -> ThirdPartyApi:
    agent_name = agent_name.strip() if agent_name else None
    if agent_name:
        # Validasi DULU sebelum bikin baris -- supaya kalau agent sudah
        # punya API lain, tidak ada baris yatim yg ke-commit tanpa link.
        await _check_agent_available(db, agent_name)

    now = datetime.now(timezone.utc)
    entry = ThirdPartyApi(
        name=name.strip(), provider=provider.strip(),
        api_key=(api_key or "").strip() or None,
        base_url=(base_url or "").strip() or None,
        account_email=(account_email or "").strip() or None,
        description=(description or "").strip() or None,
        enabled=True, created_at=now, updated_at=now,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    if agent_name:
        await link_agent(db, entry.id, agent_name)
    return entry


async def update_api(
    db: AsyncSession, api_id: uuid.UUID, name: str | None = None, provider: str | None = None,
    api_key: str | None = None, base_url: str | None = None, account_email: str | None = None,
    description: str | None = None, enabled: bool | None = None,
) -> ThirdPartyApi | None:
    entry = await db.get(ThirdPartyApi, api_id)
    if not entry:
        return None
    if name is not None:
        entry.name = name.strip()
    if provider is not None:
        entry.provider = provider.strip()
    if api_key is not None:
        entry.api_key = api_key.strip() or None
    if base_url is not None:
        entry.base_url = base_url.strip() or None
    if account_email is not None:
        entry.account_email = account_email.strip() or None
    if description is not None:
        entry.description = description.strip() or None
    if enabled is not None:
        entry.enabled = enabled
    entry.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(entry)
    return entry


async def delete_api(db: AsyncSession, api_id: uuid.UUID) -> bool:
    entry = await db.get(ThirdPartyApi, api_id)
    if not entry:
        return False
    await db.delete(entry)  # cascade hapus link jg (ON DELETE CASCADE)
    await db.commit()
    return True


async def _check_agent_available(db: AsyncSession, agent_name: str) -> None:
    agent_already_has = await db.scalar(
        select(ThirdPartyApiAgentLink).where(ThirdPartyApiAgentLink.agent_name == agent_name)
    )
    if agent_already_has:
        raise ConflictError(f"Agent '{agent_name}' sudah punya API pihak ketiga lain, unlink dulu sebelum tambah yang baru")


async def link_agent(db: AsyncSession, api_id: uuid.UUID, agent_name: str) -> ThirdPartyApiAgentLink | None:
    """Hubungkan 1 API pihak ketiga ke 1 agent -- relasi EKSKLUSIF 1:1
    (satu key cuma boleh dipakai satu agent, satu agent cuma boleh
    pakai satu key pihak ketiga), sesuai keputusan user 2026-07-22."""
    api = await db.get(ThirdPartyApi, api_id)
    if not api:
        return None
    agent_name = agent_name.strip()

    existing = await db.scalar(
        select(ThirdPartyApiAgentLink).where(
            ThirdPartyApiAgentLink.third_party_api_id == api_id,
            ThirdPartyApiAgentLink.agent_name == agent_name,
        )
    )
    if existing:
        return existing

    other_agent = await db.scalar(
        select(ThirdPartyApiAgentLink).where(ThirdPartyApiAgentLink.third_party_api_id == api_id)
    )
    if other_agent:
        raise ConflictError(f"API ini sudah dipakai agent '{other_agent.agent_name}', unlink dulu sebelum pindah ke agent lain")

    await _check_agent_available(db, agent_name)

    link = ThirdPartyApiAgentLink(
        third_party_api_id=api_id, agent_name=agent_name,
        created_at=datetime.now(timezone.utc),
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)
    return link


async def unlink_agent(db: AsyncSession, api_id: uuid.UUID, agent_name: str) -> bool:
    result = await db.execute(
        delete(ThirdPartyApiAgentLink).where(
            ThirdPartyApiAgentLink.third_party_api_id == api_id,
            ThirdPartyApiAgentLink.agent_name == agent_name.strip(),
        )
    )
    await db.commit()
    return result.rowcount > 0


def _mask(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


async def list_apis(db: AsyncSession) -> list[dict]:
    apis = (await db.scalars(select(ThirdPartyApi).order_by(ThirdPartyApi.provider, ThirdPartyApi.created_at))).all()
    links = (await db.scalars(select(ThirdPartyApiAgentLink))).all()
    agent_by_api: dict[uuid.UUID, str] = {link.third_party_api_id: link.agent_name for link in links}

    return [
        {
            "id": str(a.id), "name": a.name, "provider": a.provider,
            "masked_key": _mask(a.api_key), "is_set": bool(a.api_key),
            "base_url": a.base_url, "account_email": a.account_email,
            "description": a.description, "enabled": a.enabled,
            "linked_agent": agent_by_api.get(a.id),
            "last_error": a.last_error,
            "last_error_at": a.last_error_at.isoformat() if a.last_error_at else None,
        }
        for a in apis
    ]


async def mark_api_error(db: AsyncSession, api_id: uuid.UUID, error_message: str) -> None:
    """Catat error TERAKHIR (2026-07-22, permintaan user) -- tampil di
    kartu list, TIDAK ubah `enabled` (bukan sistem status/reload
    terpisah, cuma info log per kotak)."""
    entry = await db.get(ThirdPartyApi, api_id)
    if not entry:
        return
    entry.last_error = error_message[:2000]
    entry.last_error_at = datetime.now(timezone.utc)
    entry.updated_at = datetime.now(timezone.utc)
    await db.commit()


async def find_api_id_by_agent(db: AsyncSession, agent_name: str) -> uuid.UUID | None:
    link = await db.scalar(
        select(ThirdPartyApiAgentLink).where(ThirdPartyApiAgentLink.agent_name == agent_name.strip())
    )
    return link.third_party_api_id if link else None


async def get_next_available_key(db: AsyncSession, provider: str) -> ThirdPartyApi | None:
    """Rotasi GENERIK lintas SEMUA entry `provider` ini yg enabled=True
    (2026-07-23, permintaan user "auto rotasi berapapun API key yang
    saya daftarkan") -- utamakan yg BELUM PERNAH error, lalu yg PALING
    LAMA error (kasih 'istirahat' otomatis tanpa permanen dimatikan --
    cocok utk kuota yg reset bulanan spt Apify, BUKAN exhausted
    permanen spt token dicabut). Dipakai execute_target() (curl target)
    via placeholder {{ROTATE:<Provider>}} -- generik lintas provider
    APA PUN yg terdaftar di katalog ini, BUKAN Apify-only, supaya
    provider baru tinggal didaftarkan lewat /manage-api-keys, TANPA
    kode Python baru."""
    result = await db.execute(
        select(ThirdPartyApi)
        .where(ThirdPartyApi.provider == provider, ThirdPartyApi.enabled.is_(True), ThirdPartyApi.api_key.isnot(None))
        .order_by(ThirdPartyApi.last_error_at.asc().nulls_first())
    )
    return result.scalars().first()
