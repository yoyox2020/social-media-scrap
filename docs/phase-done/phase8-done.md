# Phase 8 — Auth Fix, Viral Videos, Date Parsing, Keyword Search Fix

Tanggal selesai: 2026-06-28

---

## Ringkasan

Phase ini fokus pada perbaikan bug dan peningkatan kualitas data yang ditemukan setelah Phase 7 selesai. Semua perubahan bersifat bugfix dan improvement, tidak ada fitur besar baru.

---

## 1. Auth Swagger — Sekali Login, Berlaku Semua Endpoint

**Masalah:** Swagger menampilkan "Authorized" tapi token tidak dikirim ke endpoint → semua POST/GET protected mengembalikan `Authentication required`.

**Penyebab:** `OAuth2PasswordBearer` menyimpan token secara internal Swagger tapi kadang tidak mengirimnya lewat Authorization header karena konflik localStorage antara skema lama dan baru.

**Solusi:** Gabungkan dua skema sekaligus di [app/services/auth/dependencies.py](../../app/services/auth/dependencies.py):

```python
# OAuth2: tampilkan form username+password di Swagger → token otomatis
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)

# HTTPBearer: fallback — user paste token manual
bearer_scheme = HTTPBearer(auto_error=False)

async def get_current_user(
    oauth_token: str | None = Depends(oauth2_scheme),
    bearer: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    api_key: str | None = Security(api_key_header),
    db: AsyncSession = Depends(get_db),
) -> User:
    token = oauth_token or (bearer.credentials if bearer else None)
    ...
```

**Cara pakai di Swagger:**
1. Buka `http://localhost:8000/docs`
2. Klik **Authorize** → bagian **OAuth2PasswordBearer**
3. Isi username (email) + password → klik Authorize
4. Token otomatis tersimpan 30 hari di localStorage browser (`persistAuthorization: True`)

**Cara pakai di frontend:**
```js
const res = await fetch('/api/v1/auth/login', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({email, password})
})
const { data } = await res.json()
localStorage.setItem('token', data.access_token)  // simpan sekali

// Pakai di semua request
fetch('/api/v1/youtube/videos/viral', {
  headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` }
})
```

Token berlaku **30 hari**. Satu token berlaku untuk semua endpoint (YouTube, TikTok nanti, Instagram nanti) — auth di level app, bukan per platform.

---

## 2. Endpoint Baru: Top Viral Videos

**Endpoint:** `GET /api/v1/youtube/videos/viral`

Menampilkan top N video dengan view_count terbanyak dari seluruh data yang tersimpan di DB, lintas semua keyword.

```bash
GET /api/v1/youtube/videos/viral?limit=20
GET /api/v1/youtube/videos/viral?limit=10&keyword_id=<uuid>
```

**Contoh response:**
```json
{
  "data": {
    "total": 20,
    "note": "Diurutkan berdasarkan view count tertinggi dari semua data di DB",
    "items": [
      {
        "rank": 1,
        "video_id": "OK1nbyGmCyw",
        "url": "https://www.youtube.com/watch?v=OK1nbyGmCyw",
        "title": "Some people should just watch the game ⚽️😂 #ad #fifaworldcup",
        "channel": "Anwar Jibawi",
        "view_count": 113077316,
        "published_at": "2022-11-28T00:00:00+00:00",
        "keyword": "fifa world cup games"
      }
    ]
  }
}
```

**Top 5 video terviral saat ini di DB:**

| Rank | Views | Judul | Keyword |
|------|-------|-------|---------|
| 1 | 113 juta | FIFA World Cup — Anwar Jibawi | fifa world cup games |
| 2 | 76 juta | KOTAK - Pelan-Pelan Saja | tantri kotak |
| 3 | 60 juta | DPR LIVE - Jasmine | dpr |
| 4 | 59 juta | DPR IAN - Don't Go Insane | dpr |
| 5 | 29 juta | Text Me — DPR LIVE | dpr |

---

## 3. GET /videos — Parameter sort_by + Filter Tanggal Publish

**Sebelumnya:** `GET /youtube/videos` hanya bisa urut berdasarkan `collected_at` (kapan di-scrape) dan filter tanggal berdasarkan collected_at.

**Sesudahnya:**

```bash
# Urut terviral (default)
GET /api/v1/youtube/videos?sort_by=views&limit=20

# Urut video paling baru berdasarkan tanggal publish
GET /api/v1/youtube/videos?sort_by=newest&limit=20

# Urut video paling lama
GET /api/v1/youtube/videos?sort_by=oldest&limit=20

# Filter berdasarkan tanggal PUBLISH video (bukan tanggal dikumpulkan)
GET /api/v1/youtube/videos?date_from=2024-01-01&date_to=2024-12-31&sort_by=views
```

| sort_by | Urutan |
|---------|--------|
| `views` (default) | View count terbanyak dulu |
| `newest` | Tanggal publish terbaru dulu |
| `oldest` | Tanggal publish terlama dulu |

---

## 4. Parse published_at — Live Search & Smart Search

**Masalah:** `GET /youtube/search` (live search) mengembalikan:
- `view_count`: `"5,945 views"` (string, tidak bisa di-sort/filter)
- `published`: `"6 months ago"` (teks relatif, bukan tanggal)

**Sesudahnya** ([app/api/v1/youtube/router.py](../../app/api/v1/youtube/router.py)):

```json
{
  "view_count": 5945,
  "published_at": "2025-12-28T07:03:23+00:00",
  "published_text": "6 months ago"
}
```

- `view_count` selalu integer (strip koma dan kata "views")
- `published_at` dihitung dari teks relatif menggunakan `_parse_relative_time()` dengan referensi waktu sekarang
- `published_text` tetap ada sebagai referensi teks asli

Berlaku juga untuk `GET /youtube/smart-search` — field `published_at` sekarang ikut dikembalikan di list video.

---

## 5. GET smart-search — Pencarian Keyword Fleksibel (LIKE)

**Masalah:** `GET /youtube/smart-search?q=pendakwah+oki` mengembalikan `not_found` padahal keyword `"pendakwah oki setiana dewi"` sudah ada di DB. Pencarian lama pakai **exact match**.

**Sesudahnya** — tiga lapis pencarian:

```python
# 1. Exact match (case-insensitive)
keyword = select(Keyword).where(func.lower(Keyword.keyword) == q_clean)

# 2. Stored keyword mengandung query
keyword = select(Keyword).where(func.lower(Keyword.keyword).like(f"%{q_clean}%"))

# 3. Semua kata dalam query ada di keyword tersimpan
conditions = [func.lower(Keyword.keyword).contains(w) for w in words]
keyword = select(Keyword).where(and_(*conditions))
```

**Contoh:**
| Query | Keyword di DB | Hasil |
|-------|--------------|-------|
| `pendakwah oki setiana dewi` | `pendakwah oki setiana dewi` | ✓ exact match |
| `pendakwah oki` | `pendakwah oki setiana dewi` | ✓ LIKE match |
| `oki setiana` | `pendakwah oki setiana dewi` | ✓ all words match |

---

## File yang Diubah

| File | Perubahan |
|------|-----------|
| `app/services/auth/dependencies.py` | Gabungkan OAuth2 + HTTPBearer, terima keduanya |
| `app/api/v1/youtube/router.py` | Endpoint viral, sort_by, parse date/views, LIKE search |
| `app/main.py` | `persistAuthorization: True` di Swagger UI |

---

## Catatan Penting

- **access_token vs refresh_token:** Yang dipakai untuk akses API adalah `access_token` (pertama), bukan `refresh_token` (kedua). Keduanya ada di response login tapi fungsi berbeda.
- **Docker Desktop:** Sempat crash, harus restart manual. Kalau `docker ps` error 500, matikan semua proses Docker lalu start ulang Docker Desktop.
- **EnsembleData limit:** HTTP 495 = kuota harian habis, coba lagi esok hari.
