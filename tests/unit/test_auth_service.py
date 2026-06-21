"""Unit tests for Auth Service — mocks repository, no DB required."""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.users.models import User
from app.infrastructure.security.password import hash_password
from app.repositories.api_key_repository import ApiKeyRepository
from app.repositories.user_repository import UserRepository
from app.services.auth.service import AuthService
from app.shared.exceptions import ConflictError, UnauthorizedError


def _make_user(**kwargs) -> User:
    defaults = dict(
        id=uuid.uuid4(),
        email="test@example.com",
        username="testuser",
        hashed_password=hash_password("secret123"),
        role="user",
        is_active=True,
        is_superuser=False,
    )
    defaults.update(kwargs)
    user = MagicMock(spec=User)
    for k, v in defaults.items():
        setattr(user, k, v)
    return user


def _build_service(user_repo: AsyncMock, api_key_repo: AsyncMock) -> AuthService:
    return AuthService(user_repo, api_key_repo)


# ── register ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_success():
    user_repo = AsyncMock(spec=UserRepository)
    user_repo.get_by_email.return_value = None
    user_repo.get_by_username.return_value = None
    user_repo.create.side_effect = lambda u: u

    service = _build_service(user_repo, AsyncMock(spec=ApiKeyRepository))
    result = await service.register("new@example.com", "newuser", "password123")

    assert result.email == "new@example.com"
    assert result.username == "newuser"
    user_repo.create.assert_called_once()


@pytest.mark.asyncio
async def test_register_duplicate_email_raises_conflict():
    user_repo = AsyncMock(spec=UserRepository)
    user_repo.get_by_email.return_value = _make_user()

    service = _build_service(user_repo, AsyncMock(spec=ApiKeyRepository))
    with pytest.raises(ConflictError, match="Email already registered"):
        await service.register("test@example.com", "other", "password123")


@pytest.mark.asyncio
async def test_register_duplicate_username_raises_conflict():
    user_repo = AsyncMock(spec=UserRepository)
    user_repo.get_by_email.return_value = None
    user_repo.get_by_username.return_value = _make_user()

    service = _build_service(user_repo, AsyncMock(spec=ApiKeyRepository))
    with pytest.raises(ConflictError, match="Username already taken"):
        await service.register("other@example.com", "testuser", "password123")


# ── login ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_success():
    user_repo = AsyncMock(spec=UserRepository)
    user_repo.get_by_email.return_value = _make_user()

    service = _build_service(user_repo, AsyncMock(spec=ApiKeyRepository))
    result = await service.login("test@example.com", "secret123")

    assert result.access_token
    assert result.refresh_token
    assert result.token_type == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password_raises_unauthorized():
    user_repo = AsyncMock(spec=UserRepository)
    user_repo.get_by_email.return_value = _make_user()

    service = _build_service(user_repo, AsyncMock(spec=ApiKeyRepository))
    with pytest.raises(UnauthorizedError):
        await service.login("test@example.com", "wrongpassword")


@pytest.mark.asyncio
async def test_login_unknown_email_raises_unauthorized():
    user_repo = AsyncMock(spec=UserRepository)
    user_repo.get_by_email.return_value = None

    service = _build_service(user_repo, AsyncMock(spec=ApiKeyRepository))
    with pytest.raises(UnauthorizedError):
        await service.login("nobody@example.com", "secret123")


@pytest.mark.asyncio
async def test_login_inactive_user_raises_unauthorized():
    user_repo = AsyncMock(spec=UserRepository)
    user_repo.get_by_email.return_value = _make_user(is_active=False)

    service = _build_service(user_repo, AsyncMock(spec=ApiKeyRepository))
    with pytest.raises(UnauthorizedError, match="deactivated"):
        await service.login("test@example.com", "secret123")
