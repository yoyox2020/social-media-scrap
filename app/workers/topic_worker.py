"""
Celery tasks untuk topic detection + multi-agent ask pipeline.
Dijalankan di worker-ai container (butuh akses ke Ollama).
"""
import asyncio
import uuid

from app.workers.celery_app import celery_app


@celery_app.task(
    name="workers.detect_topics",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def detect_topics_task(
    self,
    keyword_id: str,
    num_topics: int = 5,
    force: bool = False,
) -> dict:
    """
    Deteksi topik untuk semua post satu keyword menggunakan Qwen3 via Ollama.

    Args:
        keyword_id: UUID keyword
        num_topics: jumlah topik yang ingin dideteksi
        force:      True = deteksi ulang meskipun sudah ada
    """
    try:
        return asyncio.run(_run_topic_detection(keyword_id, num_topics, force))
    except Exception as exc:
        raise self.retry(exc=exc)


@celery_app.task(
    name="workers.ask_agent",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def ask_agent_task(self, request_dict: dict) -> dict:
    """Jalankan multi-agent pipeline untuk menjawab pertanyaan user."""
    try:
        return asyncio.run(_run_agent_ask(request_dict))
    except Exception as exc:
        raise self.retry(exc=exc)


async def _run_topic_detection(keyword_id: str, num_topics: int, force: bool) -> dict:
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.services.agents.schemas import AgentContext
    from app.services.agents.topic_agent import TopicAgent

    async with AsyncSessionLocal() as db:
        agent = TopicAgent(db, num_topics=num_topics)
        context = AgentContext(
            question=f"detect topics for keyword {keyword_id}",
            keyword_id=uuid.UUID(keyword_id),
        )
        result = await agent.run(context)
        return result.to_dict()


async def _run_agent_ask(request_dict: dict) -> dict:
    from datetime import datetime

    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.services.agents.schemas import AskRequest
    from app.services.agents.service import AgentService

    for field in ("date_from", "date_to"):
        if request_dict.get(field) and isinstance(request_dict[field], str):
            request_dict[field] = datetime.fromisoformat(request_dict[field])

    request = AskRequest(**request_dict)
    async with AsyncSessionLocal() as db:
        service = AgentService(db)
        response = await service.ask(request)
        return response.to_dict()
