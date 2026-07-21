# API YouTube — Dokumentasi Lengkap untuk Frontend

Base URL: `https://api.dismi.xyz/api/v1`

Semua response sukses berbentuk `{ "success": true, "data": {...} }`.
Semua response gagal berbentuk `{ "success": false, "error": { "code": "...", "message": "..." } }`.

## Autentikasi

Sebagian besar endpoint butuh login. Beberapa (ditandai **TANPA login**) sengaja publik —
dirancang untuk dashboard yang di-share tanpa perlu token.

```
POST /auth/login
Body: { "email": "...", "password": "..." }
Response data: { "access_token": "...", "refresh_token": "...", "token_type": "bearer" }
```

Kirim di header tiap request yang butuh login:
```
Authorization: Bearer <access_token>
```

---

## Daftar isi

1. [Ambil semua postingan YouTube (dari DB)](#1-ambil-semua-postingan-youtube-dari-db)
2. [Cari berdasarkan keyword](#2-cari-berdasarkan-keyword)
3. [Cari berdasarkan rentang tanggal](#3-cari-berdasarkan-rentang-tanggal)
4. [Per topik (topic-search, lintas platform)](#4-per-topik-topic-search-lintas-platform)
5. [Detail 1 video + komentar](#5-detail-1-video--komentar)
6. [List komentar (filter bebas)](#6-list-komentar-filter-bebas)
7. [Views/likes/comments/subscriber + kapan diambil (fetched_at)](#7-viewslikescommentssubscriber--kapan-diambil-fetched_at)
8. [Ranking viral & trending](#8-ranking-viral--trending)
9. [Notifikasi topik viral](#9-notifikasi-topik-viral)
10. [Status agent otomatis (background)](#10-status-agent-otomatis-background)

---

## 1. Ambil semua postingan YouTube (dari DB)

```
GET /youtube/videos?limit=20&offset=0&sort_by=views
```
**Perlu login.**

Query params (semua opsional kecuali tidak ada yang wajib):
| Param | Arti |
|---|---|
| `keyword_id` | Filter per keyword (UUID) |
| `date_from`, `date_to` | Filter tanggal upload video (YYYY-MM-DD) |
| `hour` | Filter jam scrape (0-23, UTC) |
| `sort_by` | `views` (default, terviral dulu) / `newest` / `oldest` |
| `limit` (1-200), `offset` | Paginasi |

**Response:**
```json
{ "success": true, "data": {
  "total": 512,
  "offset": 0,
  "limit": 20,
  "note": "url berisi link YouTube. File video tidak disimpan di server.",
  "items": [
    {
      "id": "uuid-post-di-db",
      "video_id": "dQw4w9WgXcQ",
      "url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
      "title": "judul video",
      "channel": "nama channel",
      "thumbnail_url": "https://...",
      "view_count": 1500000,
      "like_count": 45000,
      "comment_count": 3200,
      "description": "...",
      "duration": "PT10M30S",
      "keyword": "kata kunci terkait",
      "keyword_id": "uuid-keyword",
      "collected_at": "2026-07-18T05:00:00+00:00",
      "published_at": "2026-07-17T10:00:00+00:00"
    }
  ]
}}
```

---

## 2. Cari berdasarkan keyword

| Kebutuhan | Endpoint | Simpan ke DB? |
|---|---|---|
| Cari LIVE langsung ke YouTube (cepat, tanpa nunggu scrape) | `GET /youtube/search?q=xxx&depth=1` | Tidak |
| Cari video ter-upload N jam terakhir (live, urut tanggal upload bukan relevansi) | `GET /youtube/search-recent?keyword=xxx&hours_back=24` | Ya (+ opsional sentimen) |
| Trigger scrape penuh + simpan + analisis komentar | `POST /youtube/collect` | Ya |
| Cek DB dulu, auto-crawl kalau belum ada | `POST /youtube/smart-search` | Ya (kalau belum ada) |
| Cari di data yang SUDAH tersimpan, per keyword_id | `GET /youtube/videos?keyword_id=xxx` | — (baca saja) |
| Daftar semua keyword yang pernah dipakai | `GET /youtube/keywords?q=xxx` | — |

Semua **perlu login**.

### `GET /youtube/search` — response
```json
{ "success": true, "data": {
  "query": "rupiah lemah",
  "depth": 1,
  "total": 20,
  "note": "Hasil tidak disimpan ke DB. Gunakan POST /youtube/collect untuk simpan & analisis.",
  "items": [
    {
      "video_id": "abc123",
      "url": "https://www.youtube.com/watch?v=abc123",
      "title": "judul video",
      "channel": "nama channel",
      "view_count": 150000,
      "published_at": "2026-07-17T08:00:00+00:00",
      "published_text": "1 hari yang lalu",
      "duration": "10:30",
      "thumbnail_url": "https://..."
    }
  ]
}}
```

### `GET /youtube/search-recent` — response (ringkas)
```json
{ "success": true, "data": {
  "status": "ok",
  "keyword": "rupiah lemah",
  "hours_back": 24,
  "videos_found": 12,
  "videos_saved": 12,
  "sentiment": { "positif": {...}, "negatif": {...}, "netral": {...} }
}}
```

### `GET /youtube/keywords` — response
```json
{ "success": true, "data": {
  "total": 30, "offset": 0, "limit": 50,
  "items": [
    { "id": "uuid", "keyword": "rupiah lemah", "is_active": true,
      "video_count": 45, "comment_count": 890, "created_at": "..." }
  ]
}}
```

---

## 3. Cari berdasarkan rentang tanggal

```
GET /youtube/videos/date-search?date_from=2026-07-01&date_to=2026-07-18&sort_by=newest
```
**Perlu login.** `date_from`/`date_to` **wajib**.

| Param opsional | Arti |
|---|---|
| `q` | Cari teks di judul/channel/keyword |
| `keyword_id` | Filter lebih presisi dari `q` |
| `date_field` | `published` (default, tanggal upload YouTube) atau `collected` (tanggal discrape) |
| `sort_by` | `newest` (default) / `oldest` / `views` |
| `limit_comments` | Sample komentar disertakan (default 50, 0 = tidak ambil) |
| `include_sentiment` | Sertakan distribusi sentimen (default true) |

**Tip:** pakai `date_field=collected` untuk cari video yang baru discrape hari ini meskipun video-nya sendiri sudah lama di-upload — berguna untuk audit "apa yang baru masuk sistem hari ini".

---

## 4. Per topik (topic-search, lintas platform)

Topik itu konsep lintas-platform (satu topik bisa mencakup YouTube + TikTok + Twitter dkk
sekaligus, ditentukan field `platforms` topik itu). Kalau topik mencakup YouTube, hasil di
bawah otomatis termasuk video YouTube.

```
GET /search/topics/list                       # daftar semua topik
GET /search/topics/{topic_id}                 # detail 1 topik: semua keyword + posts + sentimen
GET /search/topics/{topic_id}/trend-graph      # grafik tren N hari
GET /search/topics/keywords                    # semua keyword dari semua topik aktif, digabung 1 daftar
```
Semua **perlu login**.

### `GET /search/topics/{topic_id}` — response (ringkas)
```json
{ "success": true, "data": {
  "topic_id": "uuid",
  "name": "Demo Mahasiswa",
  "platforms": ["youtube", "twitter", "tiktok"],
  "total_keywords": 3,
  "total_posts": 245,
  "keyword_details": [
    {
      "keyword": "demo mahasiswa",
      "keyword_id": "uuid",
      "total_posts": 120,
      "posts": [ { "platform": "youtube", "title": "...", "url": "...", "..." } ],
      "sentiment": { "positif": {...}, "negatif": {...}, "netral": {...} }
    }
  ],
  "schedule_recurring": true,
  "last_ai_discovery_at": "..."
}}
```

---

## 5. Detail 1 video + komentar

```
GET /youtube/videos/{video_id}?limit_comments=50
```
**Perlu login.** `{video_id}` bisa UUID post di DB **atau** YouTube video_id asli (mis. `dQw4w9WgXcQ`).

```json
{ "success": true, "data": {
  "id": "uuid-post",
  "video_id": "dQw4w9WgXcQ",
  "url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
  "title": "judul video",
  "channel": "nama channel",
  "view_count": 1500000,
  "like_count": 45000,
  "description": "...",
  "thumbnail_url": "https://...",
  "duration": "PT10M30S",
  "keyword_id": "uuid",
  "published_at": "2026-07-17T10:00:00+00:00",
  "collected_at": "2026-07-18T05:00:00+00:00",
  "total_comments_in_db": 320,
  "comments": [
    {
      "id": "uuid-comment",
      "comment_id": "yt-comment-id",
      "content": "isi komentar asli",
      "author": "@namaakunkomentar",
      "sentiment": "positif",
      "sentiment_score": 0.82,
      "like_count": 15,
      "reply_count": 2,
      "author_channel_id": "UC...",
      "published_time": "2026-07-18T04:00:00Z",
      "scraped_at": "2026-07-18T05:00:00+00:00"
    }
  ]
}}
```

---

## 6. List komentar (filter bebas)

```
GET /youtube/comments?youtube_video_id=dQw4w9WgXcQ&sentiment=positif&limit=50
```
**Perlu login.** Filter yang tersedia: `keyword_id` / `q` (nama keyword), `video_id` (UUID post)
atau `youtube_video_id` (video_id YouTube asli), `sentiment` (`positif`/`negatif`/`netral`),
`date_from`/`date_to`, `hour`.

Bentuk item **identik** dengan array `comments` di endpoint #5, ditambah `video_id`, `video_url`,
`video_title`, `keyword` (karena endpoint ini bisa lintas video).

---

## 7. Views/likes/comments/subscriber + kapan diambil (`fetched_at`)

Ini sumber data **paling lengkap** kalau butuh angka statistik terbaru per video, termasuk
subscriber channel-nya, dan **kapan terakhir data itu di-refresh**.

```
GET /youtube/metadata-agent/history?page=1&limit=20
```
**TANPA login.**

```json
{ "success": true, "data": {
  "items": [
    {
      "video_id": "dQw4w9WgXcQ",
      "title": "judul video",
      "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
      "channel_name": "nama channel",
      "channel_subscriber_count": 500000,
      "views": 1500000,
      "likes": 45000,
      "comments": 3200,
      "keyword_matched": "rupiah lemah",
      "viral_context": "Video ini viral karena membahas ...",
      "fetched_at": "2026-07-18T00:23:32+00:00"
    }
  ],
  "pagination": { "page": 1, "limit": 20, "total": 80, "total_pages": 4 }
}}
```

> **Penting:** angka `views`/`likes`/`comments`/`channel_subscriber_count` di sini **otomatis
> ter-refresh berkala** (bukan snapshot sekali ambil lalu basi) — sistem di belakang layar
> (Metadata Agent) menyegarkan ulang video yang datanya sudah lebih tua dari ambang batas
> tertentu (default 6 jam). Field `fetched_at` selalu menunjukkan kapan angka itu terakhir
> disegarkan, jadi frontend bisa tampilkan "data per [fetched_at]" di UI kalau perlu.

```
GET /youtube/metadata-agent/status
```
**TANPA login** — ringkasan progres:
```json
{ "success": true, "data": {
  "is_running": false,
  "pending_enrichment": 8735,
  "total_enriched": 80,
  "pending_refresh": 0,
  "refresh_age_hours": 6,
  "last_run_at": "2026-07-18T00:13:27+00:00"
}}
```

---

## 8. Ranking viral & trending

| Kebutuhan | Endpoint | Auth |
|---|---|---|
| Video viral urut views (data kita sendiri) + sentimen | `GET /youtube/videos/viral?limit=20` | Ya |
| Sama seperti di atas + filter tanggal/keyword | `POST /youtube/videos/viral` | Ya |
| Topik trending YouTube 7 hari (Google Trends) — cocok utk link publik | `GET /youtube/trending-public?geo=ID` | **Tidak** |
| Chart resmi "Trending" YouTube (bukan dari data kita) | `GET /youtube/videos/popular?region_code=ID&limit=20` | Ya |

### `GET /youtube/videos/viral` — response
```json
{ "success": true, "data": {
  "total": 20,
  "stats": { "total_videos": 20, "total_comments": 450, "total_analyzed": 420, "coverage_pct": 93.3 },
  "sentiment": { "positif": {...}, "negatif": {...}, "netral": {...}, "dominant": "positif" },
  "items": [
    {
      "rank": 1, "video_id": "abc123", "url": "...", "title": "...", "channel": "...",
      "view_count": 1500000, "thumbnail_url": "...", "duration": "PT10M30S",
      "published_at": "...", "keyword": "...", "comment_count": 45,
      "sentiment_summary": { "positif": {"count": 20, "pct": 44.4}, "..." },
      "comments": [ { "content": "...", "author": "...", "sentiment": "positif" } ]
    }
  ]
}}
```

### `GET /youtube/trending-public` — response
```json
{ "success": true, "data": {
  "geo": "ID",
  "days": [
    { "date": "2026-07-18", "topics": [
      { "rank": 1, "title": "nama topik trending", "traffic": "500K+",
        "video_count": 3,
        "top_videos": [ { "title": "...", "url": "...", "channel": "...",
          "thumbnail": "...", "views": 500000, "likes": 12000 } ] }
    ]}
  ]
}}
```

### `GET /youtube/videos/popular` — response
```json
{ "success": true, "data": {
  "source": "youtube_data_api_v3", "region_code": "ID", "total": 20,
  "items": [
    { "rank": 1, "video_id": "...", "url": "...", "title": "...", "channel": "...",
      "channel_id": "...", "thumbnail_url": "...", "duration": "PT10M30S",
      "view_count": 2000000, "like_count": 50000, "comment_count": 3000 }
  ]
}}
```

---

## 9. Notifikasi topik viral

Dicek otomatis tiap jam untuk **semua topik** di daftar topic-search (bukan sebagian).

```
GET   /search/notifications?is_read=false&page=1&limit=20   # perlu login — daftar notifikasi
GET   /search/notifications/unread-count                    # perlu login — badge angka
POST  /search/notifications/{id}/read                        # perlu login — tandai terbaca
GET/PATCH /search/notifications/thresholds                   # perlu login — atur ambang batas viral
GET/PATCH /search/notifications/lookback-days                 # perlu login — atur jendela waktu (hari)
```

### `GET /search/notifications` — response
```json
{ "success": true, "data": {
  "items": [
    { "id": "uuid", "topic_id": "uuid", "platform": "youtube", "post_id": "uuid",
      "keyword_text": "demo mahasiswa", "metric_type": "views", "metric_value": 500000,
      "threshold": 100000, "title": "judul video", "author": "channel",
      "url": "https://...", "is_read": false, "created_at": "2026-07-18T06:00:00+00:00" }
  ],
  "pagination": { "page": 1, "limit": 20, "total": 12, "total_pages": 1 }
}}
```

---

## 10. Status agent otomatis (background)

Dua sistem yang jalan sendiri di belakang layar, tanpa perlu dipicu manual:

- **Discovery Agent** — cari video YouTube viral/trending baru (topic-guided + pencarian bebas),
  divalidasi AI sebelum disimpan.
- **Metadata Agent** — lengkapi info video+channel+komentar utk video yang sudah tersimpan,
  PLUS refresh berkala (lihat bagian #7).

```
GET /youtube/discovery-agent/status    # TANPA login
GET /youtube/discovery-agent/runs      # TANPA login — riwayat + rincian per-kandidat
GET /youtube/metadata-agent/status     # TANPA login
GET /youtube/metadata-agent/history    # TANPA login — lihat bagian #7
```

Endpoint ini **bukan** untuk menampilkan daftar video ke user — video yang ditemukan tetap
masuk lewat jalur `posts` biasa, jadi otomatis muncul di endpoint #1/#7/#8 begitu tersimpan.
Cocok untuk indikator kecil "agent sedang mencari..." / "terakhir jalan jam X, ketemu Y video baru".
