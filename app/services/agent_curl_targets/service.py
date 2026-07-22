"""Target curl per agent -- CRUD (2026-07-22). 1 agent bisa punya
BANYAK target, beda dari third_party_apis (1:1)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_curl_targets.models import AgentCurlTarget


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
