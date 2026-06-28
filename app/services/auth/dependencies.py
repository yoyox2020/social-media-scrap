"""FastAPI dependency injection for authentication and RBAC."""
from fastapi import Depends, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.repositories.api_key_repository import ApiKeyRepository
from app.repositories.user_repository import UserRepository
from app.services.auth.service import AuthService
from app.shared.exceptions import ForbiddenError, UnauthorizedError

# OAuth2: tampilkan form username+password di Swagger → user login langsung
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)

# HTTPBearer: fallback — user paste token manual di Swagger jika OAuth2 bermasalah
bearer_scheme = HTTPBearer(auto_error=False)

# API Key: alternatif permanen tanpa token
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _build_auth_service(db: AsyncSession) -> AuthService:
    return AuthService(UserRepository(db), ApiKeyRepository(db))


async def get_current_user(
    oauth_token: str | None = Depends(oauth2_scheme),
    bearer: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    api_key: str | None = Security(api_key_header),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Terima token dari salah satu cara berikut (prioritas urut):
    1. OAuth2 form login di Swagger (username + password → auto-dapat token)
    2. HTTPBearer — paste token manual di Swagger
    3. X-API-Key header — API key permanen
    """
    service = _build_auth_service(db)

    token = oauth_token or (bearer.credentials if bearer else None)

    if token:
        return await service.get_user_from_token(token)
    if api_key:
        return await service.get_user_from_api_key(api_key)

    raise UnauthorizedError("Authentication required")


async def require_active_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_active:
        raise UnauthorizedError("Account is deactivated")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin" and not user.is_superuser:
        raise ForbiddenError("Admin access required")
    return user
