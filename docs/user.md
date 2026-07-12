# User Management API

Semua endpoint di sini **khusus admin** (`role="admin"` atau `is_superuser=true`
pada akun yang sedang login). Base URL: `https://api.dismi.xyz/api/v1`.
Header wajib di semua request: `Authorization: Bearer <token>` (dapat dari
`POST /auth/login`).

Semua contoh di bawah **hasil pengujian langsung** ke API produksi (bukan
contoh dikarang) — request dan response persis apa adanya.

---

## Bentuk objek User (`UserResponse`)

```json
{
  "id": "uuid",
  "email": "string",
  "username": "string",
  "role": "string",
  "is_active": true,
  "is_superuser": false,
  "created_at": "2026-07-12T16:28:49.673989Z",
  "updated_at": "2026-07-12T16:28:49.673989Z"
}
```

Password **tidak pernah** muncul di response mana pun (cuma hash yang
tersimpan di database, tidak pernah dikirim balik).

---

## 1. Input user baru — `POST /users`

**Request:**
```json
{
  "email": "dokumentasi.test@example.com",
  "username": "dokumentasi_test",
  "password": "testpass123",
  "role": "user",
  "is_active": true
}
```
`role` dan `is_active` opsional (default `"user"` dan `true`). `password` minimal 8 karakter.

**Response — sukses (`201 Created`):**
```json
{
  "success": true,
  "data": {
    "id": "30a66fb2-3070-461f-9ec3-5062980b83b7",
    "email": "dokumentasi.test@example.com",
    "username": "dokumentasi_test",
    "role": "user",
    "is_active": true,
    "is_superuser": false,
    "created_at": "2026-07-12T16:28:49.673989Z",
    "updated_at": "2026-07-12T16:28:49.673989Z"
  }
}
```

**Response — email sudah dipakai (`409 Conflict`):**
```json
{ "success": false, "error": { "code": "CONFLICT", "message": "Email sudah terdaftar" } }
```
(Kalau username-nya yang duplikat, pesannya `"Username sudah dipakai"`.)

---

## 2. Cari/daftar user — `GET /users`

Query param opsional: `q` (cari di email ATAU username, ILIKE substring),
`limit` (default 50, maks 200), `offset` (default 0).

**Request:** `GET /users?limit=3`

**Response (`200 OK`):**
```json
{
  "success": true,
  "data": {
    "total": 8,
    "offset": 0,
    "items": [
      {
        "id": "30a66fb2-3070-461f-9ec3-5062980b83b7",
        "email": "dokumentasi.test@example.com",
        "username": "dokumentasi_test",
        "role": "user",
        "is_active": true,
        "is_superuser": false,
        "created_at": "2026-07-12T16:28:49.673989Z",
        "updated_at": "2026-07-12T16:28:49.673989Z"
      },
      { "...": "item ke-2, ke-3, dst" }
    ]
  }
}
```
`total` = jumlah SELURUH user yang cocok filter (bukan cuma yang ada di halaman ini) — dipakai utk hitung total halaman.

**Request dengan pencarian:** `GET /users?q=dokumentasi`

**Response:** sama bentuknya, `items` cuma berisi yang cocok:
```json
{ "success": true, "data": { "total": 1, "offset": 0, "items": [ { "...": "1 user yang emailnya/username-nya mengandung 'dokumentasi'" } ] } }
```

---

## 3. Detail satu user — `GET /users/{id}`

**Request:** `GET /users/30a66fb2-3070-461f-9ec3-5062980b83b7`

**Response — sukses (`200 OK`):**
```json
{
  "success": true,
  "data": {
    "id": "30a66fb2-3070-461f-9ec3-5062980b83b7",
    "email": "dokumentasi.test@example.com",
    "username": "dokumentasi_test",
    "role": "user",
    "is_active": true,
    "is_superuser": false,
    "created_at": "2026-07-12T16:28:49.673989Z",
    "updated_at": "2026-07-12T16:28:49.673989Z"
  }
}
```

**Response — ID tidak ditemukan (`404 Not Found`):**
```json
{ "success": false, "error": { "code": "NOT_FOUND", "message": "User not found: 00000000-0000-0000-0000-000000000000" } }
```

---

## 4. Edit user — `PATCH /users/{id}`

Kirim HANYA field yang mau diubah — field yang tidak dikirim (atau `null`) tidak disentuh.

**Request:**
```json
{ "username": "dokumentasi_edited", "role": "editor", "is_active": true }
```
Field yang boleh diubah: `username`, `email`, `role`, `is_active`, `is_superuser`.

**Response (`200 OK`):**
```json
{
  "success": true,
  "data": {
    "id": "30a66fb2-3070-461f-9ec3-5062980b83b7",
    "email": "dokumentasi.test@example.com",
    "username": "dokumentasi_edited",
    "role": "editor",
    "is_active": true,
    "is_superuser": false,
    "created_at": "2026-07-12T16:28:49.673989Z",
    "updated_at": "2026-07-12T16:30:48.901667Z"
  }
}
```
Perhatikan `updated_at` berubah, `created_at` tetap. Kalau `email`/`username` baru ternyata sudah dipakai user lain, balasannya sama seperti create (`409 CONFLICT`).

---

## 5. Ubah/reset password — `PATCH /users/{id}/password`

**TIDAK perlu password lama** — endpoint ini khusus admin, jadi langsung set password baru.

**Request:**
```json
{ "new_password": "newsecurepass456" }
```
Minimal 8 karakter.

**Response — sukses (`200 OK`):**
```json
{ "success": true, "data": { "message": "Password berhasil diubah" } }
```

**Response — password kurang dari 8 karakter (`422 Unprocessable Entity`):**
```json
{
  "detail": [
    {
      "type": "string_too_short",
      "loc": ["body", "new_password"],
      "msg": "String should have at least 8 characters",
      "input": "short",
      "ctx": { "min_length": 8 }
    }
  ]
}
```
**Perhatikan bentuknya BEDA** dari error lain (tidak ada `success`/`error` wrapper) — ini format bawaan FastAPI utk validasi input SEBELUM request sampai ke logic kita. Selalu status `422` dan selalu ada field `detail` (array).

---

## 6. Hapus user — `DELETE /users/{id}`

**Bukan hapus permanen** — cuma menonaktifkan (`is_active=false`). Data/riwayat
milik user itu TIDAK hilang, cuma user itu tidak bisa login lagi setelah ini.

**Request:** `DELETE /users/30a66fb2-3070-461f-9ec3-5062980b83b7`

**Response — sukses (`200 OK`):**
```json
{ "success": true, "data": { "message": "User 'dokumentasi_edited' dinonaktifkan" } }
```

**Response — coba hapus akun sendiri (`422 Unprocessable Entity`):**
```json
{ "success": false, "error": { "code": "VALIDATION_ERROR", "message": "Tidak bisa menghapus akun sendiri" } }
```

---

## Error umum yang bisa muncul di endpoint MANAPUN di atas

| Situasi | Status | Body |
|---|---|---|
| Tidak kirim token sama sekali | `401` | `{"success":false,"error":{"code":"UNAUTHORIZED","message":"Authentication required"}}` |
| Token valid tapi akunnya BUKAN admin | `403` | `{"success":false,"error":{"code":"FORBIDDEN","message":"Admin access required"}}` |
| Token kadaluarsa/rusak | `401` | `{"success":false,"error":{"code":"UNAUTHORIZED","message":"..."}}` |

Contoh nyata (403, login pakai akun biasa lalu coba akses `GET /users`):
```json
{ "success": false, "error": { "code": "FORBIDDEN", "message": "Admin access required" } }
```

---

## Ringkasan cepat

| Method | Path | Body | Sukses |
|---|---|---|---|
| POST | `/users` | `{email, username, password, role?, is_active?}` | `201` |
| GET | `/users?q=&limit=&offset=` | — | `200` |
| GET | `/users/{id}` | — | `200` |
| PATCH | `/users/{id}` | `{username?, email?, role?, is_active?, is_superuser?}` | `200` |
| PATCH | `/users/{id}/password` | `{new_password}` | `200` |
| DELETE | `/users/{id}` | — | `200` |
