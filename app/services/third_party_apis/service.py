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


async def add_api(
    db: AsyncSession, name: str, provider: str, api_key: str | None = None,
    base_url: str | None = None, account_email: str | None = None, description: str | None = None,
) -> ThirdPartyApi:
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


async def link_agent(db: AsyncSession, api_id: uuid.UUID, agent_name: str) -> ThirdPartyApiAgentLink | None:
    api = await db.get(ThirdPartyApi, api_id)
    if not api:
        return None
    existing = await db.scalar(
        select(ThirdPartyApiAgentLink).where(
            ThirdPartyApiAgentLink.third_party_api_id == api_id,
            ThirdPartyApiAgentLink.agent_name == agent_name.strip(),
        )
    )
    if existing:
        return existing
    link = ThirdPartyApiAgentLink(
        third_party_api_id=api_id, agent_name=agent_name.strip(),
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
    links_by_api: dict[uuid.UUID, list[str]] = {}
    for link in links:
        links_by_api.setdefault(link.third_party_api_id, []).append(link.agent_name)

    return [
        {
            "id": str(a.id), "name": a.name, "provider": a.provider,
            "masked_key": _mask(a.api_key), "is_set": bool(a.api_key),
            "base_url": a.base_url, "account_email": a.account_email,
            "description": a.description, "enabled": a.enabled,
            "linked_agents": sorted(links_by_api.get(a.id, [])),
        }
        for a in apis
    ]
