"""Status scraping per-platform utk dashboard "Monitoring" (2026-07-23).
Admin-only, sama pola dgn agent_registry/third_party_apis. Lihat
app/services/scrape_monitoring/service.py."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.services.auth.dependencies import require_admin
from app.services.scrape_monitoring import service
from app.shared.utils import build_success_response

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


@router.get("/status", response_model=dict)
async def get_monitoring_status(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Status scraping tiap platform -- run terakhir, statistik 24 jam,
    run yg macet (status 'running' >15 menit). Platform baru otomatis
    muncul begitu ada run pertamanya, tanpa kode baru."""
    summary = await service.get_monitoring_summary(db)
    return build_success_response({"platforms": summary})
