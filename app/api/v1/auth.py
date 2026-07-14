import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User
from app.domain.users.schemas import UserResponse
from app.infrastructure.database.connection import get_db
from app.repositories.api_key_repository import ApiKeyRepository
from app.repositories.user_repository import UserRepository
from app.services.auth.dependencies import get_current_user
from app.services.auth.schemas import (
    AccessTokenResponse,
    ApiKeyCreateRequest,
    ApiKeyCreatedResponse,
    ApiKeyResponse,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
)
from app.services.auth.service import AuthService
from app.shared.exceptions import AppException
from app.shared.utils import build_success_response

router = APIRouter(prefix="/auth", tags=["auth"])


def _service(db: AsyncSession = Depends(get_db)) -> AuthService:
    return AuthService(UserRepository(db), ApiKeyRepository(db))


@router.post("/register", response_model=dict, status_code=201)
async def register(body: RegisterRequest, service: AuthService = Depends(_service)):
    user = await service.register(body.email, body.username, body.password)
    return build_success_response(UserResponse.model_validate(user).model_dump())


@router.post("/login", response_model=dict)
async def login(body: LoginRequest, service: AuthService = Depends(_service)):
    tokens = await service.login(body.email, body.password)
    return build_success_response(tokens.model_dump())


@router.post("/token", response_model=dict, include_in_schema=False)
async def login_swagger(
    form_data: OAuth2PasswordRequestForm = Depends(),
    service: AuthService = Depends(_service),
):
    """Endpoint khusus untuk Swagger UI OAuth2 form — username = email.

    Error di-raise sebagai HTTPException(detail=...) alih-alih format
    {success,error} biasa -- Swagger UI cuma paham shape {"detail": "..."}
    untuk OAuth2 authorize, kalau tidak pesannya jadi "[object Object]".
    """
    try:
        tokens = await service.login(form_data.username, form_data.password)
    except AppException as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"access_token": tokens.access_token, "token_type": "bearer"}


@router.post("/refresh", response_model=dict)
async def refresh_token(body: RefreshRequest, service: AuthService = Depends(_service)):
    token = await service.refresh(body.refresh_token)
    return build_success_response(token.model_dump())


@router.post("/logout", response_model=dict)
async def logout():
    # JWT is stateless — client discards the token.
    # Future: blacklist in Redis for stricter invalidation.
    return build_success_response({"message": "Logged out successfully"})


@router.get("/me", response_model=dict)
async def get_me(current_user: User = Depends(get_current_user)):
    return build_success_response(UserResponse.model_validate(current_user).model_dump())


# ── API Key management ────────────────────────────────────────────────────────

@router.post("/api-keys", response_model=dict, status_code=201)
async def create_api_key(
    body: ApiKeyCreateRequest,
    current_user: User = Depends(get_current_user),
    service: AuthService = Depends(_service),
):
    result = await service.create_api_key(current_user.id, body.name)
    return build_success_response(result.model_dump())


@router.get("/api-keys", response_model=dict)
async def list_api_keys(
    current_user: User = Depends(get_current_user),
    service: AuthService = Depends(_service),
):
    keys = await service.list_api_keys(current_user.id)
    return build_success_response([k.model_dump() for k in keys])


@router.delete("/api-keys/{key_id}", response_model=dict)
async def revoke_api_key(
    key_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: AuthService = Depends(_service),
):
    await service.revoke_api_key(key_id, current_user.id)
    return build_success_response({"message": "API key revoked"})
