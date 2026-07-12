"""
User management API -- admin only (semua endpoint di sini pakai
require_admin, lihat app/services/auth/dependencies.py). Melengkapi
app/api/v1/auth.py yang cuma punya self-service (register/login/me/api-keys),
TIDAK ada CRUD utk admin mengelola user LAIN.

Hapus user = SOFT DELETE (is_active=False), BUKAN UserRepository.delete()
(hard delete) -- Project.user_id & ApiKey.user_id pakai ondelete="CASCADE",
jadi hard delete bisa ikut menghapus seluruh Project (+keyword/post/comment
turunannya) milik user itu. Soft-delete juga otomatis memblokir login (lihat
AuthService.login()/get_user_from_token() yang cek is_active).
"""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User
from app.domain.users.schemas import (
    AdminUserCreate,
    AdminUserUpdate,
    ChangePasswordRequest,
    UserResponse,
)
from app.infrastructure.database.connection import get_db
from app.infrastructure.security.password import hash_password
from app.repositories.user_repository import UserRepository
from app.services.auth.dependencies import require_admin
from app.shared.exceptions import ConflictError, NotFoundError, ValidationError
from app.shared.utils import build_success_response

router = APIRouter(prefix="/users", tags=["users"])


def _repo(db: AsyncSession = Depends(get_db)) -> UserRepository:
    return UserRepository(db)


@router.post("", response_model=dict, status_code=201)
async def create_user(
    body: AdminUserCreate,
    _admin: User = Depends(require_admin),
    repo: UserRepository = Depends(_repo),
):
    """Input user baru."""
    if await repo.get_by_email(body.email):
        raise ConflictError("Email sudah terdaftar")
    if await repo.get_by_username(body.username):
        raise ConflictError("Username sudah dipakai")

    user = User(
        id=uuid.uuid4(),
        email=body.email,
        username=body.username,
        hashed_password=hash_password(body.password),
        role=body.role,
        is_active=body.is_active,
    )
    created = await repo.create(user)
    return build_success_response(UserResponse.model_validate(created).model_dump())


@router.get("", response_model=dict)
async def search_users(
    q: str | None = Query(default=None, description="Cari berdasarkan email/username (ILIKE)"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin),
    repo: UserRepository = Depends(_repo),
):
    """Cari/daftar user. `q` kosong = tampilkan semua (terbaru dulu)."""
    users, total = await repo.search(q, limit, offset)
    return build_success_response({
        "total": total,
        "offset": offset,
        "items": [UserResponse.model_validate(u).model_dump() for u in users],
    })


@router.get("/{user_id}", response_model=dict)
async def get_user(
    user_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    repo: UserRepository = Depends(_repo),
):
    user = await repo.get_by_id(user_id)
    if not user:
        raise NotFoundError("User", str(user_id))
    return build_success_response(UserResponse.model_validate(user).model_dump())


@router.patch("/{user_id}", response_model=dict)
async def update_user(
    user_id: uuid.UUID,
    body: AdminUserUpdate,
    _admin: User = Depends(require_admin),
    repo: UserRepository = Depends(_repo),
):
    """Edit user -- username/email/role/is_active/is_superuser (kirim field
    yang mau diubah saja, sisanya biarkan null)."""
    user = await repo.get_by_id(user_id)
    if not user:
        raise NotFoundError("User", str(user_id))

    if body.email is not None and body.email != user.email:
        existing = await repo.get_by_email(body.email)
        if existing and existing.id != user.id:
            raise ConflictError("Email sudah dipakai user lain")
        user.email = body.email
    if body.username is not None and body.username != user.username:
        existing = await repo.get_by_username(body.username)
        if existing and existing.id != user.id:
            raise ConflictError("Username sudah dipakai user lain")
        user.username = body.username
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.is_superuser is not None:
        user.is_superuser = body.is_superuser

    updated = await repo.update(user)
    return build_success_response(UserResponse.model_validate(updated).model_dump())


@router.patch("/{user_id}/password", response_model=dict)
async def change_password(
    user_id: uuid.UUID,
    body: ChangePasswordRequest,
    _admin: User = Depends(require_admin),
    repo: UserRepository = Depends(_repo),
):
    """Ubah/reset password user -- TIDAK perlu password lama (admin sudah
    lolos require_admin, beda dgn ganti password akun sendiri)."""
    user = await repo.get_by_id(user_id)
    if not user:
        raise NotFoundError("User", str(user_id))

    user.hashed_password = hash_password(body.new_password)
    await repo.update(user)
    return build_success_response({"message": "Password berhasil diubah"})


@router.delete("/{user_id}", response_model=dict)
async def delete_user(
    user_id: uuid.UUID,
    admin: User = Depends(require_admin),
    repo: UserRepository = Depends(_repo),
):
    """Nonaktifkan user (soft delete -- data/project TIDAK ikut terhapus,
    lihat catatan di atas modul). Otomatis memblokir login setelah ini."""
    if user_id == admin.id:
        raise ValidationError("Tidak bisa menghapus akun sendiri")

    user = await repo.get_by_id(user_id)
    if not user:
        raise NotFoundError("User", str(user_id))

    user.is_active = False
    await repo.update(user)
    return build_success_response({"message": f"User '{user.username}' dinonaktifkan"})
