# API Reference — Social Intelligence Platform

Base URL: `http://localhost:8000`  
Versi: `v1`  
Format response: `application/json`  
Swagger UI: `http://localhost:8000/docs`

---

## Autentikasi

Semua endpoint (kecuali `/health`, `/auth/register`, `/auth/login`) memerlukan header:

```
Authorization: Bearer <access_token>
```

Token didapat dari `POST /api/v1/auth/login`.

---

## Daftar Isi

- [Health](#health)
- [Auth](#auth)
- [Keywords](#keywords)
- [YouTube Intelligence](#youtube-intelligence)
- [Collectors](#collectors)
- [Processing](#processing)
- [Sentiment](#sentiment)
- [Topics](#topics)
- [Entities](#entities)
- [Trends](#trends)
- [Search](#search)
- [Agents](#agents)
- [Reports](#reports)

---

## Health

### GET /health

Cek status semua service infrastruktur.

**Response 200:**
```json
{
  "success": true,
  "data": {
    "status": "ok",
    "version": "1.0.0",
    "checks": {
      "database": { "status": "ok" },
      "redis": { "status": "ok" },
      "ollama": { "status": "error", "detail": "connection refused" },
      "elasticsearch": { "status": "ok" }
    }
  }
}
```

`status` bisa `"ok"` atau `"degraded"` (jika ada service yang error).

---

## Auth

### POST /api/v1/auth/register

Daftarkan user baru.

**Request body:**
```json
{
  "email": "user@example.com",
  "password": "MinLength8!",
  "full_name": "Nama Lengkap"
}
```

**Response 201:**
```json
{
  "success": true,
  "data": {
    "id": "uuid",
    "email": "user@example.com",
    "full_name": "Nama Lengkap"
  }
}
```

---

### POST /api/v1/auth/login

Login dan dapatkan JWT token.

**Request body:**
```json
{
  "email": "user@example.com",
  "password": "MinLength8!"
}
```

**Response 200:**
```json
{
  "success": true,
  "data": {
    "access_token": "eyJ...",
    "refresh_token": "eyJ...",
    "token_type": "bearer"
  }
}
```

---

### POST /api/v1/auth/refresh

Perbarui access token menggunakan refresh token.

**Request body:**
```json
{ "refresh_token": "eyJ..." }
```

---

### POST /api/v1/auth/logout

Invalidasi token aktif.

---

### GET /api/v1/auth/me

Info user yang sedang login.

**Response 200:**
```json
{
  "success": true,
  "data": {
    "id": "uuid",
    "email": "user@example.com",
    "full_name": "Nama Lengkap",
    "is_active": true
  }
}
```

---

### GET /api/v1/auth/api-keys

List semua API key milik user.

---

### POST /api/v1/auth/api-keys

Buat API key baru.

**Request body:**
```json
{ "name": "Key untuk frontend" }
```

---

### DELETE /api/v1/auth/api-keys/{key_id}

Hapus API key.

---

## Keywords

### GET /api/v1/keywords/

List semua keyword yang dipantau.

**Query params:**
| Param | Tipe | Default | Keterangan |
|---|---|---|---|
| `limit` | int | 20 | Jumlah per halaman |
| `offset` | int | 0 | Skip N item |
| `is_active` | bool | — | Filter aktif/nonaktif |

**Response 200:**
```json
{
  "success": true,
  "data": {
    "total": 16,
    "items": [
      {
        "id": "uuid",
        "keyword": "tantri kotak",
        "is_active": true,
        "project_id": "uuid",
        "created_at": "2026-06-25T21:03:55Z"
      }
    ]
  }
}
```

---

### POST /api/v1/keywords/

Buat keyword baru untuk dipantau.

**Request body:**
```json
{
  "keyword": "nama keyword",
  "project_id": "uuid",
  "is_active": true
}
```

---

### GET /api/v1/keywords/{keyword_id}

Detail satu keyword.

---

### PUT /api/v1/keywords/{keyword_id}

Update keyword (nama atau status aktif).

---

### DELETE /api/v1/keywords/{keyword_id}

Hapus keyword.

---

## YouTube Intelligence

Pipeline otomatis: **Google Trends → Keyword → Video (URL) → Komentar → Lexicon Sentiment**

> Video yang tersimpan adalah **URL YouTube** (`https://youtube.com/watch?v=VIDEO_ID`) beserta metadata. File video tidak disimpan.

> **Data tidak pernah dihapus.** Semua endpoint READ mendukung filter tanggal dan jam.

---

### POST /api/v1/youtube/trending/fetch

Fetch trending Google Trends → simpan ke DB → buat keyword → queue pipeline.

**Request body:**
```json
{
  "geo": "ID",
  "period": "24h",
  "limit": 10,
  "project_id": "uuid",
  "auto_collect": true,
  "max_pages_per_keyword": 2
}
```

| Field | Nilai | Keterangan |
|---|---|---|
| `geo` | `ID`, `US`, `GB`, `JP`, `SG` | Kode negara |
| `period` | `4h`, `24h`, `48h`, `7d` | Jendela waktu trending |
| `auto_collect` | `true` | Otomatis queue pipeline per keyword |

**Response 202:**
```json
{
  "success": true,
  "data": {
    "geo": "ID",
    "period": "24h",
    "fetched_at": "2026-06-26T00:00:00Z",
    "items": [
      { "rank": 1, "title": "fifa world cup games", "traffic": "1000+", "published_at": "..." }
    ],
    "keywords_created": 5,
    "jobs_queued": 10
  }
}
```

---

### GET /api/v1/youtube/trending

List trending topics yang tersimpan di database.

**Query params:**
| Param | Tipe | Default | Keterangan |
|---|---|---|---|
| `geo` | string | `ID` | Filter negara |
| `period` | string | `24h` | Filter periode |
| `date_from` | date | — | Filter dari tanggal (`YYYY-MM-DD`) |
| `date_to` | date | — | Filter sampai tanggal (inklusif) |
| `hour` | int 0-23 | — | Filter jam tertentu (UTC) |
| `limit` | int | 50 | Maks item |
| `offset` | int | 0 | Skip N item |

**Response 200:**
```json
{
  "success": true,
  "data": {
    "geo": "ID",
    "period": "24h",
    "filter": { "date_from": "2026-06-26", "date_to": null, "hour": null },
    "total": 26,
    "items": [
      {
        "id": "uuid",
        "rank": 1,
        "title": "fifa world cup games",
        "traffic": "1000+",
        "description": "",
        "geo": "ID",
        "period": "24h",
        "published_at": "2026-06-25T15:50:00Z",
        "fetched_at": "2026-06-25T23:09:06Z"
      }
    ]
  }
}
```

---

### GET /api/v1/youtube/videos

List video YouTube yang sudah di-scrape. Menyimpan URL dan metadata, **bukan file video**.

**Query params:**
| Param | Tipe | Keterangan |
|---|---|---|
| `keyword_id` | uuid | Filter per keyword |
| `date_from` | date | Filter dari tanggal collect |
| `date_to` | date | Filter sampai tanggal collect |
| `hour` | int 0-23 | Filter jam collect (UTC) |
| `limit` | int | Default 20, maks 200 |
| `offset` | int | Default 0 |

**Response 200:**
```json
{
  "success": true,
  "data": {
    "filter": { "keyword_id": null, "date_from": null, "date_to": null, "hour": null },
    "total": 3,
    "note": "url berisi link YouTube. File video tidak disimpan di server.",
    "items": [
      {
        "id": "uuid",
        "video_id": "cccW6vIPGzQ",
        "url": "https://www.youtube.com/watch?v=cccW6vIPGzQ",
        "title": "KOTAK - Kecuali Kamu (Official Video)",
        "channel": "KOTAKBandOFFICIAL",
        "thumbnail_url": "https://i.ytimg.com/vi/cccW6vIPGzQ/hq720.jpg",
        "view_count": 14511895,
        "description": "...",
        "duration": "4:44",
        "keyword": "tantri kotak",
        "keyword_id": "uuid",
        "collected_at": "2026-06-25T23:13:40Z",
        "published_at": null
      }
    ]
  }
}
```

---

### GET /api/v1/youtube/comments

List komentar yang sudah di-scrape dari YouTube.

**Query params:**
| Param | Tipe | Keterangan |
|---|---|---|
| `keyword_id` | uuid | Filter per keyword |
| `video_id` | uuid | Filter per video (UUID post, bukan video_id YouTube) |
| `date_from` | date | Filter dari tanggal |
| `date_to` | date | Filter sampai tanggal |
| `hour` | int 0-23 | Filter jam (UTC) |
| `limit` | int | Default 50, maks 500 |
| `offset` | int | Default 0 |

**Response 200:**
```json
{
  "success": true,
  "data": {
    "filter": { "keyword_id": null, "video_id": null, "date_from": null, "date_to": null, "hour": null },
    "total": 42,
    "items": [
      {
        "id": "uuid",
        "comment_id": "UgxABC123",
        "content": "lagunya bagus banget!",
        "author": "user123",
        "like_count": 5,
        "reply_count": 1,
        "published_time": "2 days ago",
        "video_url": "https://www.youtube.com/watch?v=cccW6vIPGzQ",
        "video_title": "KOTAK - Kecuali Kamu (Official Video)",
        "scraped_at": "2026-06-26T01:00:00Z"
      }
    ]
  }
}
```

---

### GET /api/v1/youtube/dashboard

Ringkasan statistik keseluruhan pipeline YouTube.

**Query params:**
| Param | Tipe | Keterangan |
|---|---|---|
| `project_id` | uuid | Filter per project |
| `date_from` | date | Hitung stats dari tanggal ini |
| `date_to` | date | Hitung stats sampai tanggal ini |

**Response 200:**
```json
{
  "success": true,
  "data": {
    "summary": {
      "total_trending_today": 26,
      "total_keywords": 16,
      "total_videos": 96,
      "total_comments": 0,
      "total_analyzed": 0,
      "last_updated": "2026-06-25T23:22:42Z"
    },
    "sentiment_overview": {
      "positif": 0,
      "negatif": 0,
      "netral": 0
    },
    "keyword_summaries": [],
    "recent_trending": [
      { "rank": 1, "title": "fifa world cup games", "traffic": "1000+" }
    ]
  }
}
```

---

### GET /api/v1/youtube/status

Progress pipeline untuk satu keyword: berapa video, komentar, dan yang sudah dianalisis.

**Query params:**
| Param | Tipe | Wajib | Keterangan |
|---|---|---|---|
| `keyword_id` | uuid | Ya | UUID keyword |

**Response 200:**
```json
{
  "success": true,
  "data": {
    "keyword_id": "uuid",
    "keyword_text": "tantri kotak",
    "is_active": true,
    "total_videos": 32,
    "total_comments": 150,
    "total_analyzed": 148,
    "coverage_pct": 98.7,
    "positif": 90,
    "negatif": 20,
    "netral": 38
  }
}
```

---

### GET /api/v1/youtube/sentiment/distribution

Distribusi sentimen positif/negatif/netral untuk satu keyword.

**Query params:**
| Param | Tipe | Wajib | Keterangan |
|---|---|---|---|
| `keyword_id` | uuid | Ya | |
| `date_from` | date | — | Filter dari tanggal analisis |
| `date_to` | date | — | Filter sampai tanggal analisis |

**Response 200:**
```json
{
  "success": true,
  "data": {
    "keyword_id": "uuid",
    "keyword_text": "tantri kotak",
    "total_comments": 148,
    "distribution": [
      { "label": "positif", "count": 90, "percentage": 60.81 },
      { "label": "negatif", "count": 20, "percentage": 13.51 },
      { "label": "netral",  "count": 38, "percentage": 25.68 }
    ]
  }
}
```

---

### GET /api/v1/youtube/sentiment/table

Tabel detail sentimen per komentar — termasuk kata yang cocok dengan leksikon.

**Query params:**
| Param | Tipe | Keterangan |
|---|---|---|
| `keyword_id` | uuid | Wajib |
| `label` | string | Filter: `positif`, `negatif`, `netral` |
| `date_from` | date | Filter dari tanggal |
| `date_to` | date | Filter sampai tanggal |
| `hour` | int 0-23 | Filter jam (UTC) |
| `limit` | int | Default 50, maks 500 |
| `offset` | int | Default 0 |

**Response 200:**
```json
{
  "success": true,
  "data": {
    "keyword_id": "uuid",
    "keyword_text": "tantri kotak",
    "total": 90,
    "rows": [
      {
        "comment_id": "uuid",
        "comment_text": "lagunya bagus banget keren",
        "author": "user123",
        "video_url": "https://www.youtube.com/watch?v=cccW6vIPGzQ",
        "matched_positive": ["bagus", "keren"],
        "matched_negative": [],
        "removed_stopwords": ["banget"],
        "score": 2.0,
        "label": "positif",
        "analyzed_at": "2026-06-26T01:05:00Z"
      }
    ]
  }
}
```

---

### GET /api/v1/youtube/wordcloud

Data frekuensi kata untuk ditampilkan sebagai word cloud.

**Query params:**
| Param | Tipe | Keterangan |
|---|---|---|
| `keyword_id` | uuid | Wajib |
| `sentiment` | string | Filter: `positif`, `negatif`, `netral` |
| `date_from` | date | Filter dari tanggal |
| `date_to` | date | Filter sampai tanggal |
| `top_n` | int | Default 100, maks 500 |

**Response 200:**
```json
{
  "success": true,
  "data": {
    "keyword_id": "uuid",
    "sentiment_filter": "positif",
    "words": [
      { "word": "bagus", "count": 45 },
      { "word": "keren", "count": 38 },
      { "word": "mantap", "count": 22 }
    ]
  }
}
```

---

### POST /api/v1/youtube/collect

Trigger pipeline YouTube secara manual untuk satu keyword (async via Celery).

**Request body:**
```json
{
  "keyword_id": "uuid",
  "max_pages": 2,
  "max_comments_per_video": 100,
  "max_comment_pages": 3
}
```

**Response 202:**
```json
{
  "success": true,
  "data": {
    "job_id": "celery-task-uuid",
    "keyword_id": "uuid",
    "status": "queued",
    "message": "Pipeline sedang berjalan di background."
  }
}
```

---

## Collectors

### POST /api/v1/collectors/collect

Trigger koleksi data dari platform (TikTok, Instagram, Reddit, Threads).

**Request body:**
```json
{
  "keyword_id": "uuid",
  "platforms": ["tiktok", "instagram"]
}
```

**Response 202:**
```json
{
  "success": true,
  "data": {
    "keyword_id": "uuid",
    "keyword_text": "nama keyword",
    "jobs": [
      { "platform": "tiktok", "job_id": "uuid", "status": "queued" },
      { "platform": "instagram", "job_id": "uuid", "status": "queued" }
    ]
  }
}
```

---

### GET /api/v1/collectors/jobs/{job_id}

Cek status Celery task.

**Response 200:**
```json
{
  "success": true,
  "data": {
    "job_id": "uuid",
    "status": "SUCCESS",
    "result": { "new_posts": 15, "total_fetched": 20 }
  }
}
```

---

### GET /api/v1/collectors/platforms

List platform yang didukung.

**Response 200:**
```json
{
  "success": true,
  "data": ["tiktok", "youtube", "instagram", "reddit", "threads"]
}
```

---

## Processing

### POST /api/v1/processing/trigger

Trigger text processing async (cleaning, deduplication, normalisasi).

**Request body:**
```json
{ "keyword_id": "uuid", "force": false }
```

---

### POST /api/v1/processing/trigger-sync

Processing sinkron (tunggu selesai di request yang sama).

---

### GET /api/v1/processing/stats/{keyword_id}

Statistik processing untuk satu keyword.

**Response 200:**
```json
{
  "success": true,
  "data": {
    "total_posts": 96,
    "processed": 80,
    "pending": 16,
    "duplicates_removed": 5
  }
}
```

---

## Sentiment

Analisis sentimen menggunakan model **IndoBERT** (`mdhugol/indonesia-bert-sentiment-classification`). Berbeda dengan lexicon — ini AI model, dijalankan oleh `worker-ai`.

### POST /api/v1/sentiment/analyze

Trigger analisis sentimen async.

**Request body:**
```json
{ "keyword_id": "uuid", "force": false }
```

---

### POST /api/v1/sentiment/analyze-sync

Analisis sinkron (tunggu selesai).

---

### GET /api/v1/sentiment/results/{post_id}

Hasil sentimen untuk satu post.

---

### GET /api/v1/sentiment/summary/{keyword_id}

Distribusi sentimen IndoBERT untuk keyword.

**Response 200:**
```json
{
  "success": true,
  "data": {
    "keyword_id": "uuid",
    "total": 80,
    "positive": 45,
    "negative": 15,
    "neutral": 20,
    "positive_pct": 56.25,
    "negative_pct": 18.75,
    "neutral_pct": 25.0
  }
}
```

---

## Topics

### POST /api/v1/topics/detect

Deteksi topik dari kumpulan post (async).

**Request body:**
```json
{ "keyword_id": "uuid", "n_topics": 5 }
```

---

### POST /api/v1/topics/detect-sync

Deteksi topik sinkron.

---

### GET /api/v1/topics/keyword/{keyword_id}

List topik yang sudah terdeteksi.

---

## Entities

Named Entity Recognition menggunakan **GLiNER**.

### GET /api/v1/entities/post/{post_id}

Entitas dari satu post (Person, Org, Location, Product).

### GET /api/v1/entities/keyword/{keyword_id}

Semua entitas untuk keyword.

### GET /api/v1/entities/top/{keyword_id}

Entitas paling sering muncul.

**Response 200:**
```json
{
  "success": true,
  "data": [
    { "text": "Tantri Kotak", "type": "PERSON", "count": 45 },
    { "text": "YouTube", "type": "ORG", "count": 32 }
  ]
}
```

### GET /api/v1/entities/{entity_id}

Detail satu entitas.

---

## Trends

### GET /api/v1/trends/keyword/{keyword_id}

Volume post per hari untuk keyword.

**Response 200:**
```json
{
  "success": true,
  "data": {
    "keyword_id": "uuid",
    "data_points": [
      { "date": "2026-06-25", "count": 32 },
      { "date": "2026-06-26", "count": 64 }
    ]
  }
}
```

---

### GET /api/v1/trends/sentiment/{keyword_id}

Tren sentimen over time.

**Response 200:**
```json
{
  "success": true,
  "data": {
    "keyword_id": "uuid",
    "data_points": [
      { "date": "2026-06-25", "positive": 20, "negative": 5, "neutral": 7 }
    ]
  }
}
```

---

### GET /api/v1/trends/platforms/{keyword_id}

Distribusi post per platform.

---

## Search

### POST /api/v1/search/semantic

Cari post/komentar menggunakan embedding similarity (BGE-M3 + pgvector).

**Request body:**
```json
{
  "query": "komentar tentang suara penyanyi",
  "keyword_id": "uuid",
  "limit": 10,
  "ef_search": 40
}
```

**Response 200:**
```json
{
  "success": true,
  "data": {
    "query": "komentar tentang suara penyanyi",
    "results": [
      {
        "id": "uuid",
        "content": "suaranya merdu banget",
        "score": 0.92,
        "platform": "youtube"
      }
    ]
  }
}
```

---

### POST /api/v1/search/fulltext

Full-text search via Elasticsearch.

**Request body:**
```json
{
  "query": "tantri kotak konser",
  "keyword_id": "uuid",
  "limit": 20
}
```

---

## Agents

Multi-agent pipeline menggunakan **Qwen3 8B** via Ollama. Rate limited: 10 request/menit.

### POST /api/v1/agents/ask

Tanya ke AI agent (async — dapat job_id, poll untuk hasil).

**Request body:**
```json
{
  "question": "Bagaimana sentimen publik terhadap tantri kotak bulan ini?",
  "keyword_id": "uuid",
  "context_limit": 20
}
```

**Response 202:**
```json
{
  "success": true,
  "data": {
    "job_id": "uuid",
    "status": "queued"
  }
}
```

---

### POST /api/v1/agents/ask-sync

Tanya ke AI agent (sinkron — tunggu respons, timeout 120 detik).

**Response 200:**
```json
{
  "success": true,
  "data": {
    "question": "Bagaimana sentimen publik...",
    "answer": "Berdasarkan analisis 148 komentar...",
    "sources": ["post-uuid-1", "post-uuid-2"],
    "sentiment_summary": { "positif": 90, "negatif": 20, "netral": 38 }
  }
}
```

---

## Reports

### POST /api/v1/reports/generate

Generate laporan async (PDF / DOCX / JSON).

**Request body:**
```json
{
  "keyword_id": "uuid",
  "format": "pdf",
  "period": "week",
  "title": "Laporan Sentimen Tantri Kotak"
}
```

| `format` | Keterangan |
|---|---|
| `pdf` | PDF dengan grafik (ReportLab) |
| `docx` | Word document (python-docx) |
| `json` | Data mentah JSON |

**Response 202:**
```json
{
  "success": true,
  "data": {
    "report_id": "uuid",
    "status": "generating"
  }
}
```

---

### POST /api/v1/reports/generate-sync

Generate laporan sinkron (tunggu selesai).

---

### GET /api/v1/reports/

List semua laporan yang sudah digenerate.

---

### GET /api/v1/reports/{report_id}

Status dan metadata satu laporan.

---

### GET /api/v1/reports/{report_id}/download

Download file laporan (PDF / DOCX / JSON).

**Response:** File binary dengan header `Content-Disposition: attachment`.

---

### DELETE /api/v1/reports/{report_id}

Hapus laporan.

---

## Format Response

Semua response menggunakan format standar:

**Sukses:**
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
    "code": "NOT_FOUND",
    "message": "Keyword tidak ditemukan"
  }
}
```

**Kode error umum:**

| Code | HTTP | Keterangan |
|---|---|---|
| `UNAUTHORIZED` | 401 | Token tidak valid atau tidak ada |
| `FORBIDDEN` | 403 | Tidak punya akses ke resource ini |
| `NOT_FOUND` | 404 | Resource tidak ditemukan |
| `VALIDATION_ERROR` | 422 | Input tidak valid |
| `RATE_LIMITED` | 429 | Terlalu banyak request |
| `INTERNAL_ERROR` | 500 | Error server internal |
