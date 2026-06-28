# Contoh Penggunaan API — Social Media Intelligence

Dokumentasi ini menjelaskan cara memanggil setiap endpoint utama beserta contoh request dan response nyata.

**Base URL:** `http://localhost:8000` (lokal) | `http://187.77.125.10:8000` (server)
**Docs interaktif:** `http://localhost:8000/docs`

---

## 1. Autentikasi

### Login & Dapatkan Token

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "yahyamatoristmik@gmail.com", "password": "admin123"}'
```

**Response:**
```json
{
  "success": true,
  "data": {
    "access_token": "eyJhbGci...",
    "refresh_token": "eyJhbGci...",
    "token_type": "bearer"
  }
}
```

> Gunakan `access_token` di header: `Authorization: Bearer <token>`
> Token berlaku **30 hari**.

---

## 2. YouTube — Smart Search (Endpoint Utama)

Endpoint cerdas: cek DB dulu, jika belum ada → crawl otomatis → simpan → kembalikan hasil.

### POST — Cari (dengan crawl otomatis jika baru)

```bash
curl -X POST http://localhost:8000/api/v1/youtube/smart-search \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "q": "demo dprd",
    "max_pages": 2,
    "max_comments_per_video": 50,
    "max_comment_pages": 2,
    "force_refresh": false
  }'
```

**Response (keyword baru — baru dikrawl):**
```json
{
  "status": "collected",
  "keyword": "demo dprd",
  "keyword_id": "uuid...",
  "videos_found": 19,
  "comments_collected": 23,
  "comments_analyzed": 23,
  "sentiment_summary": {
    "positif": 4,
    "negatif": 3,
    "netral": 16
  },
  "videos": [
    {
      "video_id": "abc123",
      "title": "Demo mahasiswa DPRD ...",
      "channel": "Nama Channel",
      "url": "https://youtube.com/watch?v=abc123",
      "published_at": "2026-06-25T12:00:00+00:00",
      "views": 15000,
      "comment_count": 12
    }
  ],
  "sample_comments": [
    {
      "author": "user123",
      "text": "Demo ini penting untuk aspirasi rakyat",
      "sentiment": "positif",
      "score": 2.5,
      "published_at": "2026-06-25T13:30:00+00:00"
    }
  ]
}
```

**Response (keyword sudah ada di DB):**
```json
{
  "status": "from_cache",
  "keyword": "demo dprd",
  ...
}
```

**Response (force_refresh — trigger crawl ulang di background):**
```json
{
  "status": "refreshing",
  "message": "Data lama dikembalikan, crawl baru berjalan di background"
}
```

---

### GET — Ambil Data yang Sudah Ada di DB

```bash
curl "http://localhost:8000/api/v1/youtube/smart-search?q=tantri+kotak" \
  -H "Authorization: Bearer <token>"
```

**Response (ada data):**
```json
{
  "status": "found",
  "keyword": "tantri kotak",
  "videos_found": 34,
  "comments_collected": 342,
  "comments_analyzed": 342,
  "sentiment_summary": {
    "positif": 280,
    "negatif": 15,
    "netral": 47
  }
}
```

**Response (tidak ada):**
```json
{
  "status": "not_found",
  "message": "Keyword 'xxx' belum ada. Gunakan POST /youtube/smart-search untuk crawl."
}
```

---

## 3. YouTube — Video

### GET Daftar Video per Keyword

```bash
curl "http://localhost:8000/api/v1/youtube/videos?keyword_id=<uuid>&limit=10&offset=0" \
  -H "Authorization: Bearer <token>"
```

**Filter tambahan:**
```bash
# Filter berdasarkan tanggal publish
curl "http://localhost:8000/api/v1/youtube/videos?keyword_id=<uuid>&date_from=2025-01-01&date_to=2026-06-28" \
  -H "Authorization: Bearer <token>"
```

**Response:**
```json
{
  "success": true,
  "data": {
    "total": 34,
    "items": [
      {
        "id": "uuid...",
        "title": "Tantri Kotak - Masih Cinta",
        "channel": "Kotak Official",
        "url": "https://youtube.com/watch?v=xyz",
        "published_at": "2023-08-15T00:00:00+00:00",
        "collected_at": "2026-06-25T23:13:00+00:00",
        "views": 5200000,
        "published_text": "2 years ago"
      }
    ]
  }
}
```

---

## 4. YouTube — Komentar

### GET Komentar dengan Filter Tanggal

```bash
# Semua komentar dari keyword
curl "http://localhost:8000/api/v1/youtube/comments?keyword_id=<uuid>&limit=50" \
  -H "Authorization: Bearer <token>"

# Filter rentang tanggal (berdasarkan published_at komentar)
curl "http://localhost:8000/api/v1/youtube/comments?keyword_id=<uuid>&date_from=2024-01-01&date_to=2024-12-31" \
  -H "Authorization: Bearer <token>"

# Filter jam tertentu dalam sehari
curl "http://localhost:8000/api/v1/youtube/comments?keyword_id=<uuid>&hour=20" \
  -H "Authorization: Bearer <token>"

# Komentar dari 1 video spesifik
curl "http://localhost:8000/api/v1/youtube/comments?video_id=<post-uuid>&limit=100" \
  -H "Authorization: Bearer <token>"
```

**Response:**
```json
{
  "success": true,
  "data": {
    "total": 342,
    "items": [
      {
        "id": "uuid...",
        "content": "Suara Tantri emang keren banget!",
        "author": "fans_kotak",
        "published_at": "2023-09-01T14:22:00+00:00",
        "metadata": {
          "like_count": 15,
          "published_time": "2 years ago"
        }
      }
    ]
  }
}
```

---

## 5. Sentimen YouTube

### GET Distribusi Sentimen per Keyword

```bash
curl "http://localhost:8000/api/v1/youtube/sentiment/distribution?keyword_id=<uuid>" \
  -H "Authorization: Bearer <token>"
```

**Response:**
```json
{
  "keyword_id": "uuid...",
  "keyword_text": "tantri kotak",
  "total_comments": 342,
  "distribution": [
    {"label": "positif", "count": 280, "percentage": 81.87},
    {"label": "netral",  "count": 47,  "percentage": 13.74},
    {"label": "negatif", "count": 15,  "percentage": 4.39}
  ]
}
```

---

### GET Tabel Sentimen Detail (per komentar)

```bash
curl "http://localhost:8000/api/v1/youtube/sentiment/table?keyword_id=<uuid>&limit=20" \
  -H "Authorization: Bearer <token>"
```

**Response:**
```json
{
  "keyword_id": "uuid...",
  "keyword_text": "tantri kotak",
  "total": 342,
  "rows": [
    {
      "comment_id": "uuid...",
      "comment_text": "Suara Tantri emang keren banget!",
      "author": "fans_kotak",
      "video_url": "https://youtube.com/watch?v=xyz",
      "matched_positive": ["keren", "banget"],
      "matched_negative": [],
      "removed_stopwords": ["emang"],
      "score": 2.0,
      "label": "positif",
      "analyzed_at": "2026-06-25T23:30:00+00:00"
    }
  ]
}
```

---

### GET Word Cloud

```bash
# Semua kata
curl "http://localhost:8000/api/v1/youtube/wordcloud?keyword_id=<uuid>" \
  -H "Authorization: Bearer <token>"

# Hanya kata dari komentar positif
curl "http://localhost:8000/api/v1/youtube/wordcloud?keyword_id=<uuid>&sentiment=positif" \
  -H "Authorization: Bearer <token>"

# Hanya kata negatif
curl "http://localhost:8000/api/v1/youtube/wordcloud?keyword_id=<uuid>&sentiment=negatif" \
  -H "Authorization: Bearer <token>"
```

**Response:**
```json
{
  "keyword_id": "uuid...",
  "sentiment_filter": "positif",
  "words": [
    {"word": "keren", "count": 45},
    {"word": "bagus", "count": 32},
    {"word": "mantap", "count": 28}
  ]
}
```

---

## 6. Dashboard

### GET Ringkasan Semua Data

```bash
curl "http://localhost:8000/api/v1/youtube/dashboard" \
  -H "Authorization: Bearer <token>"
```

**Response:**
```json
{
  "summary": {
    "total_trending_today": 26,
    "total_keywords": 20,
    "total_videos": 166,
    "total_comments": 783,
    "total_analyzed": 783,
    "last_updated": "2026-06-28T04:30:00+00:00"
  },
  "sentiment_overview": [
    {"label": "positif", "count": 621, "percentage": 79.3},
    {"label": "netral",  "count": 120, "percentage": 15.3},
    {"label": "negatif", "count": 42,  "percentage": 5.4}
  ],
  "keyword_summaries": [
    {
      "keyword_id": "uuid...",
      "keyword_text": "tantri kotak",
      "total_videos": 34,
      "total_comments": 342,
      "positif": 280, "negatif": 15, "netral": 47,
      "dominant_sentiment": "positif"
    }
  ],
  "recent_trending": [...]
}
```

---

## 7. Trending Topics

### GET Topik Trending Hari Ini

```bash
curl "http://localhost:8000/api/v1/youtube/trending" \
  -H "Authorization: Bearer <token>"
```

### POST Fetch Trending dari Google Trends

```bash
curl -X POST http://localhost:8000/api/v1/youtube/trending/fetch \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "geo": "ID",
    "period": "24h",
    "limit": 10,
    "project_id": "<uuid>",
    "auto_collect": true,
    "max_pages_per_keyword": 1
  }'
```

---

## 8. Collect Manual (Pipeline Lengkap)

### POST Trigger Crawl untuk Keyword yang Sudah Ada

```bash
curl -X POST http://localhost:8000/api/v1/youtube/collect \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "keyword_id": "<uuid>",
    "max_pages": 2,
    "max_comment_pages": 2,
    "max_comments_per_video": 50
  }'
```

**Response:**
```json
{
  "success": true,
  "data": {
    "job_id": "uuid...",
    "status": "queued",
    "message": "Pipeline dimulai di background"
  }
}
```

### GET Cek Status Job

```bash
curl "http://localhost:8000/api/v1/collectors/jobs/<job_id>" \
  -H "Authorization: Bearer <token>"
```

---

## 9. Status Keyword

### GET Status per Keyword

```bash
curl "http://localhost:8000/api/v1/youtube/status?keyword_id=<uuid>" \
  -H "Authorization: Bearer <token>"
```

**Response:**
```json
{
  "keyword_id": "uuid...",
  "keyword_text": "tantri kotak",
  "is_active": true,
  "total_videos": 34,
  "total_comments": 342,
  "total_analyzed": 342,
  "coverage_pct": 100.0,
  "positif": 280,
  "negatif": 15,
  "netral": 47
}
```

---

## 10. Live Search (tidak disimpan ke DB)

### GET Cari Langsung dari YouTube (real-time)

```bash
curl "http://localhost:8000/api/v1/youtube/search?q=banjir+jakarta&max_pages=1" \
  -H "Authorization: Bearer <token>"
```

> Hasil **tidak disimpan ke DB**. Gunakan `POST /youtube/smart-search` jika ingin disimpan.

---

## Panduan Alur Lengkap

### Skenario: Mulai dari keyword baru

```bash
# 1. Login
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"yahyamatoristmik@gmail.com","password":"admin123"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['access_token'])")

# 2. Smart search — crawl otomatis jika belum ada
curl -X POST http://localhost:8000/api/v1/youtube/smart-search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"q": "kemacetan jakarta", "max_pages": 2}'

# 3. Lihat distribusi sentimen
KEYWORD_ID="<uuid-dari-response-step-2>"
curl "http://localhost:8000/api/v1/youtube/sentiment/distribution?keyword_id=$KEYWORD_ID" \
  -H "Authorization: Bearer $TOKEN"

# 4. Filter komentar berdasarkan tanggal
curl "http://localhost:8000/api/v1/youtube/comments?keyword_id=$KEYWORD_ID&date_from=2025-01-01" \
  -H "Authorization: Bearer $TOKEN"

# 5. Word cloud komentar negatif
curl "http://localhost:8000/api/v1/youtube/wordcloud?keyword_id=$KEYWORD_ID&sentiment=negatif" \
  -H "Authorization: Bearer $TOKEN"
```

---

## Catatan Penting

| Kondisi | Yang Terjadi |
|---|---|
| Keyword belum ada di DB | POST smart-search → crawl otomatis → simpan → return |
| Keyword sudah ada di DB | POST smart-search → return dari cache (cepat) |
| `force_refresh: true` | Return data lama + background crawl baru |
| EnsembleData limit habis | Error 495, coba lagi esok hari |
| FIFA/video live | Komentar sering dinonaktifkan, 0 comment normal |
| `published_at` komentar | Dihitung dari `published_time` (misal "2 years ago") relatif terhadap waktu dikumpulkan |


1. Token Swagger — Tidak Hilang Saat Refresh
Ditambah persistAuthorization: True di main.py:57. Cara pakainya:

Buka http://localhost:8000/docs
Klik tombol Authorize (kanan atas)
Isi: Bearer eyJhbGci... (paste token dari login)
Klik Authorize → Close
Token tersimpan di localStorage browser — tidak hilang meski halaman di-refresh

2. Contoh API Per Topik — api-by-topic.md
Topik	Skenario
Isu Sosial/Politik	Demo DPRD, banjir → cari sentimen publik, filter komentar per tanggal kejadian
Brand/Artis	Tantri Kotak → sentimen penggemar, word cloud, perbandingan antar artis
Kuliner/Produk	Nasi goreng, tongseng → komentar terbaru, crawl ulang dengan force_refresh
Trending Real-time	Fetch Google Trends Indonesia → auto crawl → cek sentimen tiap topik
Dashboard & Laporan	Ringkasan semua data sekaligus
Filter Waktu	Komentar sebelum/sesudah event, komentar pukul prime time, video tahun tertentu
Bonus: Cara buat API Key permanen (tidak expired, cocok untuk integrasi sistem lain).