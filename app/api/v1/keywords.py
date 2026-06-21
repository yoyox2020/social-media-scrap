from fastapi import APIRouter, Depends

from app.domain.users.models import User
from app.services.auth.dependencies import get_current_user

router = APIRouter(prefix="/keywords", tags=["keywords"])


@router.get("/")
async def list_keywords(current_user: User = Depends(get_current_user)):
    # TODO: Phase 2 - implement keyword list per project
    pass


@router.post("/", status_code=201)
async def create_keyword(current_user: User = Depends(get_current_user)):
    # TODO: Phase 2 - implement keyword creation
    pass


@router.get("/{keyword_id}")
async def get_keyword(keyword_id: str, current_user: User = Depends(get_current_user)):
    # TODO: Phase 2 - implement get keyword
    pass


@router.put("/{keyword_id}")
async def update_keyword(keyword_id: str, current_user: User = Depends(get_current_user)):
    # TODO: Phase 2 - implement update keyword
    pass


@router.delete("/{keyword_id}")
async def delete_keyword(keyword_id: str, current_user: User = Depends(get_current_user)):
    # TODO: Phase 2 - implement delete keyword
    pass
