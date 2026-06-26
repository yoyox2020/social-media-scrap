# Akses API — YouTube Intelligence

Base URL lokal: `http://localhost:8000`  
Base URL publik (ngrok): `https://a9fa-114-8-201-103.ngrok-free.app`  
Swagger UI: `http://localhost:8000/docs` atau `https://a9fa-114-8-201-103.ngrok-free.app/docs`

Semua endpoint butuh header: `Authorization: Bearer <token>`  
Token didapat dari `POST /api/v1/auth/login`

---

## Daftar Isi

- [Cara Login & Akses Token](#cara-login--akses-token)
- [Cara Login di Swagger UI](#cara-login-di-swagger-ui)
- [Cara Akses via PowerShell](#cara-akses-via-powershell)
- [Alur Pipeline Otomatis](#alur-pipeline-otomatis)
- [Trigger Manual via Swagger](#trigger-manual-via-swagger)
- [Trigger Manual via PowerShell](#trigger-manual-via-powershell)
- [Cara Lihat Data Hasil](#cara-lihat-data-hasil)
- [Daftar Endpoint YouTube](#daftar-endpoint-youtube)
- [Penjelasan Tiap Endpoint](#penjelasan-tiap-endpoint)
- [Masalah yang Ditemukan & Fix](#masalah-yang-ditemukan--fix)
- [Catatan Penting](#catatan-penting)

---

## Cara Login & Akses Token

### Via PowerShell (satu perintah)

```powershell
$TOKEN = (Invoke-RestMethod -Method POST -Uri "http://localhost:8000/api/v1/auth/login" `
  -ContentType "application/json" `
  -Body '{"email":"yahyamatoristmik@gmail.com","password":"Admin1234!"}').data.access_token
```

Token tersimpan di variabel `$TOKEN` dan siap dipakai untuk request berikutnya.

### Via curl di Linux/Mac/WSL

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"yahyamatoristmik@gmail.com","password":"Admin1234!"}' \
  | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
```

> **Jangan pakai PowerShell untuk perintah curl dengan `&&` atau `|` — gunakan `Invoke-RestMethod` di PowerShell.**

---

## Cara Login di Swagger UI

Swagger menampilkan form OAuth2 dengan field `username`, `password`, `client_id`, `client_secret`.

**Langkah:**
1. Buka `http://localhost:8000/docs`
2. Klik tombol **Authorize** (pojok kanan atas, icon gembok)
3. Isi field:
   - **username** → `yahyamatoristmik@gmail.com`
   - **password** → `Admin1234!`
   - `client_id` → **kosong**
   - `client_secret` → **kosong**
4. Klik **Authorize** → **Close**

> Field `client_id` dan `client_secret` dikosongkan saja — tidak dipakai oleh server, hanya muncul karena format OAuth2 Swagger.

> Setelah Authorize berhasil, semua endpoint bisa langsung di-Execute tanpa isi token manual.

---

## Cara Akses via PowerShell

### Template dasar

```powershell
# 1. Simpan token
$TOKEN = (Invoke-RestMethod -Method POST -Uri "http://localhost:8000/api/v1/auth/login" `
  -ContentType "application/json" `
  -Body '{"email":"yahyamatoristmik@gmail.com","password":"Admin1234!"}').data.access_token

# 2. Akses endpoint
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/youtube/ENDPOINT" `
  -Headers @{ Authorization = "Bearer $TOKEN" } | ConvertTo-Json -Depth 10
```

### Contoh akses dashboard

```powershell
$TOKEN = (Invoke-RestMethod -Method POST -Uri "http://localhost:8000/api/v1/auth/login" -ContentType "application/json" -Body '{"email":"yahyamatoristmik@gmail.com","password":"Admin1234!"}').data.access_token
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/youtube/dashboard?date_from=2026-06-01&date_to=2026-06-30" -Headers @{ Authorization = "Bearer $TOKEN" } | ConvertTo-Json -Depth 10
```

---

## Alur Pipeline Otomatis

Pipeline berjalan **tanpa perlu trigger manual**. Celery Beat menjalankan otomatis:

```
Setiap jam 00:00 / 06:00 / 12:00 / 18:00 WIB
        │
        ▼
fetch_trending_youtube_task
  → Ambil Google Trends RSS (geo=ID, period=24h)
  → Simpan ke trending_topics (histori tidak dihapus)
  → Buat keyword baru jika belum ada
  → Queue pipeline per keyword
        │
        ▼
collect_youtube_pipeline_task(keyword_id)
  → Scrape video YouTube via EnsembleData
  → Simpan URL + metadata ke posts (ON CONFLICT DO NOTHING)
  → Queue comment task per video
        │
        ▼
collect_youtube_comments_task(post_id, keyword_id)
  → Ambil komentar via EnsembleData (cursor pagination)
  → Simpan ke comments
  → Analisis lexicon sentiment
  → Simpan ke lexicon_analyses
```

**Yang tersimpan per video:** URL YouTube (`https://youtube.com/watch?v=VIDEO_ID`), judul, channel, views, thumbnail. **File video tidak disimpan.**

**Jadwal jam (WIB):**
| Jam WIB | Jam UTC | Task |
|---|---|---|
| 07:00 | 00:00 | fetch trending + pipeline |
| 13:00 | 06:00 | fetch trending + pipeline |
| 19:00 | 12:00 | fetch trending + pipeline |
| 01:00 | 18:00 | fetch trending + pipeline |
| 08:00 | 01:00 | daily report |
| 09:00 Senin | 02:00 | weekly report |

---

## Trigger Manual via Swagger

Gunakan trigger manual jika ingin collect sekarang tanpa nunggu jadwal Celery Beat.

### Langkah trigger satu keyword

1. Buka `http://localhost:8000/docs`
2. Login via **Authorize** (lihat [Cara Login di Swagger UI](#cara-login-di-swagger-ui))
3. Cari endpoint `POST /api/v1/youtube/collect`
4. Klik → **Try it out** → isi body:

```json
{
  "keyword_id": "714d037b-5d47-4694-b76c-fd39ebb41bb3",
  "max_pages": 1,
  "max_comment_pages": 2,
  "max_comments_per_video": 50
}
```

5. Klik **Execute**
6. Catat `job_id` dari response

### Cek status job

1. Cari `GET /api/v1/collectors/jobs/{job_id}`
2. Isi `job_id` dari langkah sebelumnya
3. Klik **Execute**

Response `status`:
- `PENDING` — antri di Redis
- `STARTED` — sedang berjalan
- `SUCCESS` — selesai
- `FAILURE` — error

### Lihat hasil setelah selesai

1. Cari `GET /api/v1/youtube/status`
2. Isi `keyword_id`
3. Klik **Execute**

---

## Trigger Manual via PowerShell

### Trigger satu keyword

```powershell
$TOKEN = (Invoke-RestMethod -Method POST -Uri "http://localhost:8000/api/v1/auth/login" -ContentType "application/json" -Body '{"email":"yahyamatoristmik@gmail.com","password":"Admin1234!"}').data.access_token

Invoke-RestMethod -Method POST -Uri "http://localhost:8000/api/v1/youtube/collect" `
  -Headers @{ Authorization = "Bearer $TOKEN" } `
  -ContentType "application/json" `
  -Body '{"keyword_id":"714d037b-5d47-4694-b76c-fd39ebb41bb3","max_pages":1,"max_comment_pages":2,"max_comments_per_video":50}'
```

### Trigger semua keyword sekaligus

```powershell
$TOKEN = (Invoke-RestMethod -Method POST -Uri "http://localhost:8000/api/v1/auth/login" -ContentType "application/json" -Body '{"email":"yahyamatoristmik@gmail.com","password":"Admin1234!"}').data.access_token

$keywords = @(
    "714d037b-5d47-4694-b76c-fd39ebb41bb3",  # tantri kotak (34 video)
    "76758a0f-ab46-4583-b415-ddfe6595df7b",  # fifa world cup games (39 video)
    "14bbba6c-dc17-4850-8596-630392f64b22"   # ao tanaka (23 video)
)

foreach ($kid in $keywords) {
    $result = Invoke-RestMethod -Method POST -Uri "http://localhost:8000/api/v1/youtube/collect" `
        -Headers @{ Authorization = "Bearer $TOKEN" } `
        -ContentType "application/json" `
        -Body "{`"keyword_id`":`"$kid`",`"max_pages`":1,`"max_comment_pages`":2,`"max_comments_per_video`":50}"
    Write-Host "Queued $kid → job:" $result.data.job_id
}
```

> **Kapan perlu trigger manual?** Hanya jika ingin collect sekarang tanpa nunggu jadwal. Jika EnsembleData sedang 495 (limit harian habis), trigger manual tetap tidak akan berhasil — tunggu limit reset tengah malam.

---

## Cara Lihat Data Hasil

### 1. Dashboard keseluruhan

**Via Swagger:** `GET /api/v1/youtube/dashboard` → isi `date_from=2026-06-01`, `date_to=2026-06-30` → Execute

**Via PowerShell:**
```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/youtube/dashboard?date_from=2026-06-01&date_to=2026-06-30" -Headers @{ Authorization = "Bearer $TOKEN" } | ConvertTo-Json -Depth 10
```

**Data yang tampil:**
```json
{
  "summary": {
    "total_trending_today": 26,
    "total_keywords": 16,
    "total_videos": 96,
    "total_comments": 342,
    "total_analyzed": 342,
    "last_updated": "2026-06-26T..."
  },
  "sentiment_overview": [
    { "label": "positif", "count": 178, "percentage": 52.0 },
    { "label": "negatif", "count": 24,  "percentage": 7.0  },
    { "label": "netral",  "count": 140, "percentage": 41.0 }
  ],
  "keyword_summaries": [...],
  "recent_trending": [...]
}
```

> **Kenapa `total_trending_today = 0` tanpa filter?** Default dashboard hanya hitung trending hari ini. Data trending ada di tanggal 25 Juni, bukan 26 Juni. Selalu tambahkan `date_from` dan `date_to` agar muncul.

### 2. Status pipeline per keyword

**Via Swagger:** `GET /api/v1/youtube/status` → isi `keyword_id` → Execute

**Via PowerShell:**
```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/youtube/status?keyword_id=714d037b-5d47-4694-b76c-fd39ebb41bb3" -Headers @{ Authorization = "Bearer $TOKEN" } | ConvertTo-Json
```

**Contoh hasil (tantri kotak):**
```json
{
  "keyword_text": "tantri kotak",
  "total_videos": 34,
  "total_comments": 342,
  "total_analyzed": 342,
  "coverage_pct": 100.0,
  "positif": 178,
  "negatif": 24,
  "netral": 140
}
```

### 3. Distribusi sentimen

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/youtube/sentiment/distribution?keyword_id=714d037b-5d47-4694-b76c-fd39ebb41bb3" -Headers @{ Authorization = "Bearer $TOKEN" } | ConvertTo-Json -Depth 5
```

### 4. Tabel detail sentimen per komentar

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/youtube/sentiment/table?keyword_id=714d037b-5d47-4694-b76c-fd39ebb41bb3&label=negatif&limit=10" -Headers @{ Authorization = "Bearer $TOKEN" } | ConvertTo-Json -Depth 5
```

### 5. Daftar video

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/youtube/videos?limit=10" -Headers @{ Authorization = "Bearer $TOKEN" } | ConvertTo-Json -Depth 5
```

### 6. Daftar komentar

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/youtube/comments?keyword_id=714d037b-5d47-4694-b76c-fd39ebb41bb3&limit=20" -Headers @{ Authorization = "Bearer $TOKEN" } | ConvertTo-Json -Depth 5
```

### 7. Word cloud

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/youtube/wordcloud?keyword_id=714d037b-5d47-4694-b76c-fd39ebb41bb3&sentiment=positif&top_n=20" -Headers @{ Authorization = "Bearer $TOKEN" } | ConvertTo-Json -Depth 5
```

---

## Daftar Endpoint YouTube

| No | Method | Path | Fungsi |
|---|---|---|---|
| 1 | POST | `/youtube/trending/fetch` | Fetch Google Trends dan jalankan pipeline |
| 2 | GET | `/youtube/trending` | Lihat trending yang tersimpan di DB |
| 3 | GET | `/youtube/videos` | Lihat daftar video (URL + metadata) |
| 4 | GET | `/youtube/comments` | Lihat komentar yang sudah di-scrape |
| 5 | GET | `/youtube/dashboard` | Ringkasan statistik semua pipeline |
| 6 | GET | `/youtube/status` | Progress pipeline per keyword |
| 7 | GET | `/youtube/sentiment/distribution` | Distribusi sentimen per keyword |
| 8 | GET | `/youtube/sentiment/table` | Tabel detail sentimen per komentar |
| 9 | GET | `/youtube/wordcloud` | Data frekuensi kata untuk word cloud |
| 10 | POST | `/youtube/collect` | Trigger pipeline manual per keyword |

---

## Penjelasan Tiap Endpoint

### 1. POST /youtube/trending/fetch

Fetch trending Google Trends → simpan ke DB → buat keyword → queue pipeline.

**Body:**
```json
{
  "geo": "ID",
  "period": "24h",
  "limit": 10,
  "project_id": "uuid-project",
  "auto_collect": true,
  "max_pages_per_keyword": 2
}
```

| Field | Nilai | Keterangan |
|---|---|---|
| `geo` | `ID` / `US` / `SG` | Kode negara |
| `period` | `4h` / `24h` / `48h` / `7d` | Jendela waktu |
| `auto_collect` | `true` | Langsung queue pipeline per keyword |

---

### 2. GET /youtube/trending

List trending topics dari DB. Semua histori tersimpan, tidak ada yang dihapus.

**Filter:**
| Param | Keterangan |
|---|---|
| `geo` | Default `ID` |
| `period` | Default `24h` |
| `date_from` | Format `YYYY-MM-DD` dengan tanda `-` (bukan `/`) |
| `date_to` | Format `YYYY-MM-DD` |
| `hour` | Jam 0–23 UTC (WIB = UTC+7) |
| `limit` | Default 50, maks 500 |

> **Penting:** Isi tanggal dengan tanda `-` bukan `/`. Format `2026-06-25` bukan `2026/06/25`. Kalau pakai `/` akan error 422.

---

### 3. GET /youtube/videos

List video yang tersimpan. **URL saja + metadata, bukan file video.**

**Filter:**
| Param | Keterangan |
|---|---|
| `keyword_id` | UUID keyword |
| `date_from` / `date_to` | Tanggal collect |
| `hour` | Jam collect (UTC) |
| `limit` | Default 20, maks 200 |

---

### 4. GET /youtube/comments

List komentar. Filter per keyword atau per video.

> `video_id` di sini adalah **UUID Post** (dari field `id` di `/youtube/videos`), bukan YouTube video ID seperti `cccW6vIPGzQ`.

---

### 5. GET /youtube/dashboard

Ringkasan 4 bagian: summary count, sentimen global, per-keyword summary, 10 trending terbaru.

**Wajib isi filter tanggal** agar `total_trending_today` tidak 0:
```
date_from=2026-06-01&date_to=2026-06-30
```

| Field | Catatan |
|---|---|
| `total_trending_today` | Difilter tanggal, default = hari ini |
| `total_videos` | Tidak difilter tanggal, hitung semua |
| `total_comments` | Tidak difilter tanggal, hitung semua |
| `keyword_summaries` | Maks 20 keyword, terbaru dulu |
| `recent_trending` | 10 trending terbaru tanpa filter |

---

### 6. GET /youtube/status

Progress pipeline satu keyword: video → komentar → analisis (coverage %).

**Wajib:** `keyword_id`

---

### 7. GET /youtube/sentiment/distribution

Distribusi positif/negatif/netral dari `lexicon_analyses`.

**Wajib:** `keyword_id`  
**Opsional:** `date_from`, `date_to`

---

### 8. GET /youtube/sentiment/table

Detail per komentar: kata yang cocok leksikon, skor, label. Berguna untuk audit.

**Wajib:** `keyword_id`  
**Opsional:** `label` (positif/negatif/netral), `date_from`, `date_to`, `hour`, `limit`, `offset`

---

### 9. GET /youtube/wordcloud

Frekuensi kata dari hasil lexicon, siap untuk render word cloud.

**Wajib:** `keyword_id`  
**Opsional:** `sentiment` (positif/negatif/netral), `date_from`, `date_to`, `top_n`

---

### 10. POST /youtube/collect

Trigger pipeline manual untuk satu keyword.

**Body:**
```json
{
  "keyword_id": "uuid",
  "max_pages": 1,
  "max_comment_pages": 2,
  "max_comments_per_video": 50
}
```

Response langsung balik `job_id`. Cek status via `GET /api/v1/collectors/jobs/{job_id}`.

---

## Masalah yang Ditemukan & Fix

### 1. Error 422 di Swagger pada field tanggal

**Masalah:** Swagger mengirim `2026-06/25` (pakai `/`) saat Swagger encode otomatis.  
**Fix:** Isi manual dengan format `2026-06-25` pakai tanda `-`.

---

### 2. Error "Unprocessable Entity" saat Authorize di Swagger

**Masalah:** Swagger OAuth2 form mengirim `username=email` sebagai form-data, tapi login endpoint menerima JSON.  
**Fix:** Ditambahkan endpoint `/api/v1/auth/token` khusus untuk Swagger yang menerima form-data dan meneruskan ke service login.  
Sekarang Swagger Authorize langsung bisa pakai `username` + `password` tanpa error.

---

### 3. Worker gagal collect: `InterfaceError: another operation is in progress`

**Masalah:** Celery `ForkPoolWorker` mewarisi `AsyncEngine` (connection pool asyncpg) dari parent process. Ketika child process memanggil `asyncio.run()`, event loop baru dibuat tapi koneksi pool masih terikat ke event loop parent yang sudah tidak valid — menyebabkan bentrok.  
**Fix:** Setiap Celery task async sekarang membuat `AsyncEngine` baru (`create_async_engine`) dan membuangnya (`dispose()`) setelah selesai, sehingga tidak ada koneksi warisan dari parent process.

---

### 4. Sentiment kosong karena EnsembleData limit (HTTP 495)

**Masalah:** EnsembleData API mengembalikan HTTP 495 (`Maximum requests limit reached for today`) saat kuota harian habis. Komentar tidak bisa di-fetch.  
**Perilaku:** Task tetap `succeeded` tapi dengan `errors: ["EnsembleData error: HTTP 495"]` dan `comments_new: 0`.  
**Solusi:** Tunggu kuota reset (tengah malam). Celery Beat akan otomatis retry di jadwal berikutnya. Tidak perlu tindakan manual.

---

### 5. Token expired saat copy-paste

**Masalah:** JWT access token expired setelah 30 menit. Copy-paste token lama menyebabkan error 401.  
**Fix:** Selalu login ulang untuk dapat token baru. Gunakan variabel `$TOKEN` di PowerShell agar tidak perlu copy-paste manual.

---

## Catatan Penting

| Hal | Keterangan |
|---|---|
| **Data tidak pernah dihapus** | Semua endpoint GET menyimpan histori. Filter `date_from`/`date_to`/`hour` untuk mempersempit |
| **Video = URL saja** | File video tidak disimpan, hanya link + metadata |
| **`hour` selalu UTC** | WIB = UTC+7. Jam 12:00 WIB = `hour=5` UTC |
| **EnsembleData 495** | Kuota harian habis. Auto-reset tengah malam |
| **Trigger manual kapan?** | Hanya jika ingin collect sekarang. Jika 495, trigger manual pun tidak berhasil |
| **ngrok URL berubah** | Setiap ngrok restart dapat URL baru. URL saat ini: `https://a9fa-114-8-201-103.ngrok-free.app` |
| **Format tanggal di Swagger** | Selalu pakai `-` bukan `/`: `2026-06-25` bukan `2026/06/25` |
| **`keyword_id` untuk tantri kotak** | `714d037b-5d47-4694-b76c-fd39ebb41bb3` |
| **`keyword_id` untuk fifa world cup** | `76758a0f-ab46-4583-b415-ddfe6595df7b` |
| **`keyword_id` untuk ao tanaka** | `14bbba6c-dc17-4850-8596-630392f64b22` |

---

## Status Data Saat Ini (26 Juni 2026)

| Keyword | Video | Komentar | Teranalisis | Positif | Negatif | Netral |
|---|---|---|---|---|---|---|
| tantri kotak | 34 | 342 | 342 (100%) | 178 (52%) | 24 (7%) | 140 (41%) |
| fifa world cup games | 39 | 0 | 0 | — | — | — |
| ao tanaka | 23 | 0 | 0 | — | — | — |
| keyword lainnya (13) | 0 | 0 | 0 | — | — | — |

FIFA dan ao tanaka kosong karena kena 495 saat pipeline berjalan. Akan terisi otomatis setelah kuota reset.
