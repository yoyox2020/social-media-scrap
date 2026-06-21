"""Auth service: register, login, token refresh, API key management."""
import uuid

from app.domain.users.models import ApiKey, User
from app.infrastructure.security.api_key import generate_api_key, hash_api_key
from app.infrastructure.security.jwt import create_access_token, create_refresh_token, decode_token
from app.infrastructure.security.password import hash_password, verify_password
from app.repositories.api_key_repository import ApiKeyRepository
from app.repositories.user_repository import UserRepository
from app.services.auth.schemas import (
    AccessTokenResponse,
    ApiKeyCreatedResponse,
    ApiKeyResponse,
    TokenResponse,
)
from app.shared.exceptions import ConflictError, NotFoundError, UnauthorizedError


class AuthService:
    def __init__(self, user_repo: UserRepository, api_key_repo: ApiKeyRepository):
        self.user_repo = user_repo
        self.api_key_repo = api_key_repo

    async def register(self, email: str, username: str, password: str) -> User:
        if await self.user_repo.get_by_email(email):
            raise ConflictError("Email already registered")
        if await self.user_repo.get_by_username(username):
            raise ConflictError("Username already taken")

        user = User(
            id=uuid.uuid4(),
            email=email,
            username=username,
            hashed_password=hash_password(password),
        )
        return await self.user_repo.create(user)

    async def login(self, email: str, password: str) -> TokenResponse:
        user = await self.user_repo.get_by_email(email)
        if not user or not verify_password(password, user.hashed_password):
            raise UnauthorizedError("Invalid email or password")
        if not user.is_active:
            raise UnauthorizedError("Account is deactivated")

        return TokenResponse(
            access_token=create_access_token(str(user.id), {"role": user.role}),
            refresh_token=create_refresh_token(str(user.id)),
        )

    async def refresh(self, refresh_token: str) -> AccessTokenResponse:
        payload = decode_token(refresh_token)
        if payload.get("type") != "refresh":
            raise UnauthorizedError("Invalid token type")

        user = await self.user_repo.get_by_id(uuid.UUID(payload["sub"]))
        if not user or not user.is_active:
            raise UnauthorizedError("User not found or deactivated")

        return AccessTokenResponse(
            access_token=create_access_token(str(user.id), {"role": user.role})
        )

    async def get_user_from_token(self, token: str) -> User:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise UnauthorizedError("Invalid token type")

        user = await self.user_repo.get_by_id(uuid.UUID(payload["sub"]))
        if not user or not user.is_active:
            raise UnauthorizedError("User not found or deactivated")
        return user

    async def get_user_from_api_key(self, raw_key: str) -> User:
        key_hash = hash_api_key(raw_key)
        api_key = await self.api_key_repo.get_by_hash(key_hash)
        if not api_key:
            raise UnauthorizedError("Invalid API key")

        await self.api_key_repo.update_last_used(api_key.id)

        user = await self.user_repo.get_by_id(api_key.user_id)
        if not user or not user.is_active:
            raise UnauthorizedError("User not found or deactivated")
        return user

    async def create_api_key(self, user_id: uuid.UUID, name: str) -> ApiKeyCreatedResponse:
        raw_key, key_hash = generate_api_key()
        api_key = ApiKey(id=uuid.uuid4(), user_id=user_id, key_hash=key_hash, name=name)
        created = await self.api_key_repo.create(api_key)
        return ApiKeyCreatedResponse(id=created.id, name=created.name, key=raw_key, created_at=created.created_at)

    async def list_api_keys(self, user_id: uuid.UUID) -> list[ApiKeyResponse]:
        keys = await self.api_key_repo.list_by_user(user_id)
        return [ApiKeyResponse.model_validate(k) for k in keys]

    async def revoke_api_key(self, key_id: uuid.UUID, user_id: uuid.UUID) -> None:
        await self.api_key_repo.deactivate(key_id, user_id)
