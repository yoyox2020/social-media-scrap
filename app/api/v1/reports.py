from fastapi import APIRouter

router = APIRouter(prefix="/reports", tags=["reports"])


@router.post("/generate")
async def generate_report():
    # TODO: Phase 7 - trigger report generation
    pass


@router.get("/")
async def list_reports():
    # TODO: Phase 7 - list reports per project
    pass


@router.get("/{report_id}")
async def get_report(report_id: str):
    # TODO: Phase 7 - get report detail
    pass


@router.get("/{report_id}/download")
async def download_report(report_id: str):
    # TODO: Phase 7 - download PDF/DOCX/JSON report
    pass
