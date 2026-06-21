# Phase 1 — Auth Service: DONE

**Tanggal selesai:** 2026-06-21

---

## Deliverable

Authentication working — register, login JWT, refresh token, API Key management, RBAC dependency.

---

## Yang Diimplementasi

### Domain

| File | Perubahan |
|------|-----------|
| `app/domain/users/models.py` | Ditambah model `ApiKey` (relasi many-to-one ke User) |

**ApiKey columns:** `id`, `user_id`, `key_hash`, `name`, `is_active`, `last_used_at`, `expires_at`, `created_at`, `updated_at`

---

### Infrastructure

| File | Keterangan |
|------|-----------|
| `app/infrastructure/security/password.py` | `hash_password()` + `verify_password()` via bcrypt (passlib) |

---

### Repositories

| File | Methods |
|------|---------|
| `app/repositories/user_repository.py` | `get_by_id`, `get_by_email`, `get_by_username`, `create`, `update`, `delete` |
| `app/repositories/api_key_repository.py` | `get_by_id`, `get_by_hash`, `list_by_user`, `create`, `deactivate`, `update_last_used` |

---

### Services

#### `app/services/auth/schemas.py`
| Schema | Keterangan |
|--------|-----------|
| `RegisterRequest` | Validasi: username alphanumeric 3–50 char, password min 8 char |
| `LoginRequest` | email + password |
| `RefreshRequest` | refresh_token |
| `TokenResponse` | access_token + refresh_token + token_type |
| `AccessTokenResponse` | access_token + token_type |
| `ApiKeyCreateRequest` | name |
| `ApiKeyCreatedResponse` | id + name + **raw key** (hanya muncul sekali) |
| `ApiKeyResponse` | id + name + is_active + last_used_at + expires_at |

#### `app/services/auth/service.py` — `AuthService`
| Method | Keterangan |
|--------|-----------|
| `register(email, username, password)` | Cek duplikat → hash password → create user |
| `login(email, password)` | Verify password → issue JWT access + refresh |
| `refresh(refresh_token)` | Validate refresh token → issue access token baru |
| `get_user_from_token(token)` | Decode JWT → get user |
| `get_user_from_api_key(raw_key)` | Hash key → lookup DB → update last_used |
| `create_api_key(user_id, name)` | Generate raw+hash → store hash → return raw key |
| `list_api_keys(user_id)` | List semua API key milik user |
| `revoke_api_key(key_id, user_id)` | Set is_active=False |

#### `app/services/auth/dependencies.py` — FastAPI DI
| Dependency | Keterangan |
|-----------|-----------|
| `get_current_user` | Menerima **JWT Bearer** ATAU **X-API-Key** header |
| `require_active_user` | Guard: user harus aktif |
| `require_admin` | Guard: role == admin atau is_superuser == True |

---

### API Endpoints

**Prefix:** `/api/v1/auth`

| Method | Path | Auth | Keterangan |
|--------|------|------|-----------|
| `POST` | `/register` | — | Daftar user baru |
| `POST` | `/login` | — | Login, dapat access + refresh token |
| `POST` | `/refresh` | — | Tukar refresh token → access token baru |
| `POST` | `/logout` | — | Stateless logout (client drop token) |
| `GET` | `/me` | ✓ | Data user yang sedang login |
| `POST` | `/api-keys` | ✓ | Buat API key baru |
| `GET` | `/api-keys` | ✓ | List API keys milik user |
| `DELETE` | `/api-keys/{id}` | ✓ | Revoke API key |

**Auth bisa via:**
- `Authorization: Bearer <access_token>`
- `X-API-Key: <raw_api_key>`

---

### Standard Response Format

**Success:**
```json
{
  "success": true,
  "data": { ... }
}
```

**Error:**
```json
{
  "success": false,
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Invalid email or password"
  }
}
```

---

### Tests

| File | Tests |
|------|-------|
| `tests/unit/test_auth_service.py` | 7 tests: register duplikat, login sukses/gagal/inactive |
| `tests/unit/test_password.py` | 3 tests: hash berbeda (salt), verify benar/salah |
| `tests/unit/test_jwt.py` | 3 tests: access/refresh token encode-decode, invalid token |

**Total: 13 unit tests** — semua mock repository, tidak butuh DB.

---

## Contoh Penggunaan

### Register + Login
```bash
# Register
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","username":"myuser","password":"secret123"}'

# Login
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"secret123"}'
# → {"success":true,"data":{"access_token":"eyJ...","refresh_token":"eyJ...","token_type":"bearer"}}
```

### Akses endpoint terproteksi
```bash
# Via JWT
curl http://localhost:8000/api/v1/auth/me \
  -H "Authorization: Bearer eyJ..."

# Via API Key
curl http://localhost:8000/api/v1/auth/me \
  -H "X-API-Key: <raw_key>"
```

### Buat API Key
```bash
curl -X POST http://localhost:8000/api/v1/auth/api-keys \
  -H "Authorization: Bearer eyJ..." \
  -H "Content-Type: application/json" \
  -d '{"name":"my-service-key"}'
# → {"success":true,"data":{"id":"...","name":"my-service-key","key":"<raw_key_only_shown_once>","created_at":"..."}}
```

---

## Keputusan Implementasi

- **Raw API key tidak disimpan** — hanya SHA-256 hash yang disimpan di DB. Raw key hanya dikembalikan satu kali saat create.
- **Dual auth** — satu dependency `get_current_user` mendukung JWT dan API Key, sehingga semua endpoint otomatis mendukung keduanya.
- **JWT stateless logout** — tidak ada blacklist Redis untuk sekarang. Bisa ditambah di Phase 8 (Production Hardening) jika dibutuhkan.
- **RBAC sederhana** — role string (`admin`, `user`, `viewer`) + flag `is_superuser`. Extend di phase berikutnya jika perlu permission granular.

---

## Phase Berikutnya

**Phase 2 — Collector Service:** Implementasi koneksi ke EnsembleData API, collect posts per keyword, simpan ke DB, push ke Redis queue untuk processing.
