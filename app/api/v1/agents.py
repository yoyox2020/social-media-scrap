from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.services.agents.schemas import AskJobResponse, AskRequest
from app.services.auth.dependencies import get_current_user
from app.shared.utils import build_success_response

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("/ask", response_model=dict, status_code=202)
async def ask_agent(
    body: AskRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Tanyakan pertanyaan ke multi-agent system.
    Task dijalankan async via Celery dan hasilnya bisa diambil via /collectors/jobs/{job_id}.
    """
    from app.workers.agent_worker import ask_agent_task

    task = ask_agent_task.delay(body.model_dump(mode="json"))
    response = AskJobResponse(keyword_id=body.keyword_id, job_id=task.id)
    return build_success_response(response.model_dump())


@router.post("/ask-sync", response_model=dict)
async def ask_agent_sync(
    body: AskRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Jalankan agent pipeline secara sinkron — untuk dev/debug.
    Response bisa lambat tergantung jumlah agent yang dipanggil.
    """
    from app.services.agents.service import AgentService

    service = AgentService(db)
    response = await service.ask(body)
    return build_success_response(response.to_dict())
