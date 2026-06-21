import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.services.agents.schemas import TopicDetectRequest
from app.services.auth.dependencies import get_current_user
from app.shared.utils import build_success_response

router = APIRouter(prefix="/topics", tags=["topics"])


@router.post("/detect", response_model=dict, status_code=202)
async def detect_topics(
    body: TopicDetectRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Trigger deteksi topik untuk semua post satu keyword via Celery.
    Menggunakan Qwen3 8B untuk mengidentifikasi topik dominan.
    """
    from app.workers.topic_worker import detect_topics_task

    task = detect_topics_task.delay(
        str(body.keyword_id),
        body.num_topics,
        body.force,
    )
    return build_success_response({"job_id": task.id, "status": "queued"})


@router.post("/detect-sync", response_model=dict)
async def detect_topics_sync(
    body: TopicDetectRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deteksi topik secara sinkron — untuk dev/debug."""
    from app.services.agents.topic_agent import TopicAgent
    from app.services.agents.schemas import AgentContext

    agent = TopicAgent(db, num_topics=body.num_topics)
    context = AgentContext(
        question=f"Deteksi topik untuk keyword_id {body.keyword_id}",
        keyword_id=body.keyword_id,
    )
    result = await agent.run(context)
    return build_success_response(result.to_dict())


@router.get("/keyword/{keyword_id}", response_model=dict)
async def list_topics_by_keyword(
    keyword_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    List topik yang terdeteksi untuk satu keyword.
    Topik dideteksi oleh TopicAgent dan disimpan ke cache di DB.
    """
    from sqlalchemy import select
    from app.domain.topics.models import Topic

    result = await db.execute(
        select(Topic)
        .where(Topic.keywords.contains([str(keyword_id)]))
        .order_by(Topic.post_count.desc())
        .limit(20)
    )
    topics = result.scalars().all()
    return build_success_response([
        {
            "id": str(t.id),
            "name": t.name,
            "description": t.description,
            "post_count": t.post_count,
        }
        for t in topics
    ])
