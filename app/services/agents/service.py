"""
AgentService — orkestrasi pipeline multi-agent.

Flow:
  1. PlannerAgent menentukan agents yang perlu dipanggil
  2. Agents dijalankan secara sequential (search terlebih dahulu, lalu analytics)
  3. SummaryAgent mengagregasi semua hasil menjadi jawaban akhir
"""
from __future__ import annotations

import asyncio
import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.agents.planner import PlannerAgent
from app.services.agents.schemas import (
    AgentContext,
    AgentResponse,
    AgentResult,
    AskRequest,
)

# Registry: nama → class
_AGENT_REGISTRY: dict[str, type] = {}


def _register():
    from app.services.agents.entity_agent import EntityAgent
    from app.services.agents.search_agent import SearchAgent
    from app.services.agents.sentiment_agent import SentimentAgent
    from app.services.agents.summary_agent import SummaryAgent
    from app.services.agents.trend_agent import TrendAgent

    return {
        "search": SearchAgent,
        "sentiment": SentimentAgent,
        "entity": EntityAgent,
        "trend": TrendAgent,
        "summary": SummaryAgent,
    }


class AgentService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.planner = PlannerAgent(db)

    async def ask(self, request: AskRequest) -> AgentResponse:
        """
        Jalankan multi-agent pipeline untuk menjawab pertanyaan user.

        Returns:
            AgentResponse dengan jawaban, detail per-agent, dan waktu proses
        """
        start = time.monotonic()
        registry = _register()

        # ── Fetch keyword info untuk context ──────────────────────────────────
        keyword_text = await self._get_keyword_text(request.keyword_id)

        context = AgentContext(
            question=request.question,
            keyword_id=request.keyword_id,
            keyword_text=keyword_text,
            platform=request.platform,
            date_from=request.date_from,
            date_to=request.date_to,
        )

        # ── Planning ──────────────────────────────────────────────────────────
        plan = await self.planner.plan(request.question, use_llm=request.use_llm_planner)

        # ── Agent execution (sequential, kecuali agents paralel) ──────────────
        results: dict[str, AgentResult] = {}
        errors: list[str] = []

        # Search agent pertama karena hasilnya mungkin dipakai agent lain
        non_summary_plan = [a for a in plan if a != "summary"]
        for agent_name in non_summary_plan:
            agent_cls = registry.get(agent_name)
            if not agent_cls:
                continue
            try:
                agent = agent_cls(self.db)
                result = await agent.run(context)
                results[agent_name] = result
                if result.error:
                    errors.append(f"{agent_name}: {result.error}")
            except Exception as exc:
                errors.append(f"{agent_name}: {exc}")

        # ── Summary agent selalu terakhir ─────────────────────────────────────
        summary_agent_cls = registry.get("summary")
        if summary_agent_cls:
            try:
                summary_agent = summary_agent_cls(self.db)
                # Pass semua results ke summary agent
                summary_result = await summary_agent.run(context, agent_results=results)
                results["summary"] = summary_result
                final_answer = summary_result.summary
            except Exception as exc:
                errors.append(f"summary: {exc}")
                final_answer = _fallback_answer(results)
        else:
            final_answer = _fallback_answer(results)

        elapsed_ms = int((time.monotonic() - start) * 1000)

        return AgentResponse(
            question=request.question,
            keyword_id=request.keyword_id,
            answer=final_answer,
            agent_plan=plan,
            details=results,
            processing_time_ms=elapsed_ms,
            errors=errors,
        )

    async def _get_keyword_text(self, keyword_id: uuid.UUID) -> str:
        """Ambil teks keyword dari DB untuk konteks."""
        try:
            from sqlalchemy import select
            from app.domain.keywords.models import Keyword
            result = await self.db.execute(
                select(Keyword.keyword).where(Keyword.id == keyword_id)
            )
            row = result.scalar_one_or_none()
            return row or ""
        except Exception:
            return ""


def _fallback_answer(results: dict[str, AgentResult]) -> str:
    """Template answer jika summary agent gagal."""
    parts = []
    for name, r in results.items():
        if r.summary and not r.error:
            parts.append(f"• {r.summary}")
    return "\n".join(parts) if parts else "Analisis selesai, tidak ada data yang dapat ditampilkan."
