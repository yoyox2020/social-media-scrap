# Smart Search (Topic-Based Search) API

Fitur pencarian berdasarkan topik + kata kunci, lintas **semua platform**
sekaligus (Instagram, Facebook, TikTok, Twitter, YouTube, News), digabung
dalam satu topik yang tersimpan di database untuk dashboard.

Base URL: `https://api.dismi.xyz/api/v1`. Header wajib di semua request:
`Authorization: Bearer <token>` (dapat dari `POST /auth/login`).

Semua contoh di bawah **hasil pengujian langsung** ke API produksi (bukan
contoh dikarang) — request dan response persis apa adanya (beberapa field
teks panjang dipotong `[...]` biar ringkas dibaca).

---

## Cara kerja: pencarian 3 tingkat (tier)

```
User cari/simpan topik dengan keyword
        │
        ▼
Tier-1: cek DATABASE dulu (posts/comments, murah & cepat)
        │
   ┌────┴────┐
   │         │
 ADA       KOSONG
   │         │
   ▼         ▼
Langsung   OTOMATIS didaftarkan ke antrian tier-3 di background
tampilkan  (status "queued") -- TIDAK ADA lagi langkah konfirmasi
hasil      manual, langsung jalan begitu tier-1 kosong
           │
           ▼
   Diproses SATU PER SATU berurutan di background (Apify utk Facebook/
   Instagram/TikTok/Twitter, Firecrawl utk News, YouTube Data API/
   EnsembleData utk YouTube), dibatasi `limit_per_keyword`
           │
           ▼
   Hasil otomatis tersimpan ke database -- cek lagi lewat
   GET /search/topics/{topic_id}
```

**Kenapa background, bukan langsung ditunggu?** Satu panggilan ke Apify/
Firecrawl bisa makan waktu 15–60+ detik. Kalau ditunggu di request yang
sama, gampang timeout (apalagi kalau beberapa keyword sekaligus). Jadi
begitu status jadi `"queued"`, request langsung selesai (balasan instan),
sementara prosesnya jalan sendiri di server.

**Satu-satunya batas yang tersisa** (pengganti konfirmasi manual yang sudah
dihapus) adalah `limit_per_keyword` — parameter yang kamu tentukan sendiri
di request (default `10`, maks `100`), membatasi berapa banyak hasil dicari
per keyword.

**Platform kosong = SEMUA platform terdaftar** (`instagram`, `facebook`,
`tiktok`, `twitter`, `youtube`, `news`) kalau field `platforms` tidak
dikirim/kosong.

---

## 1. Cari + simpan topik baru — `POST /search/topics`

**Request:**
```json
{
  "topics": [
    { "name": "Dok Test Smart Search", "keywords": ["frasa unik dokumentasi xyz789"] }
  ],
  "platforms": ["news"],
  "limit_per_keyword": 5
}
```
Field lain yang boleh dikirim (semua opsional):
- `save_topic` (default `true`) — simpan topik ke DB untuk dashboard
- `auto_crawl` (default `true`) — izinkan tier-3 kalau tier-1 kosong. Set
  `false` kalau cuma mau cek DB / simpan definisi topik saja.
- `include_sentiment` (default `true`)
- `include_comments` (default `false`)
- `enable_recurring` + `schedule_duration_days` — lihat bagian jadwal berkala

**Response — data belum ada di DB (`200 OK`, status `"queued"`):**
```json
{
  "success": true,
  "data": {
    "status": "queued",
    "platforms": ["news"],
    "total_topics": 1,
    "queued_keywords": ["frasa unik dokumentasi xyz789"],
    "note": "Keyword dengan status 'queued' sedang dicari ke third-party SATU PER SATU di background (Apify/Firecrawl/YouTube API). Cek lagi lewat GET /search/topics/{topic_id} setelah beberapa saat.",
    "topics": [
      {
        "topic_id": "c2b1e6b7-da54-4c78-ac95-1fc94f0cf85f",
        "topic": "Dok Test Smart Search",
        "keywords": ["frasa unik dokumentasi xyz789"],
        "total_posts": 0,
        "status_per_keyword": { "frasa unik dokumentasi xyz789": "queued" },
        "sentiment_per_keyword": {},
        "results": [],
        "queued": ["frasa unik dokumentasi xyz789"]
      }
    ]
  }
}
```

**Response — data SUDAH ada di DB (status `"ready"`):** `queued_keywords`
kosong, `topics[].results` langsung berisi post yang cocok + `sentiment`.

`status` yang mungkin muncul: `"ready"` (semua ketemu di DB atau tidak ada
yang perlu tier-3), `"queued"` (ada yang lagi diantrekan), `"partial"`
(sebagian ketemu di DB, sebagian diantrekan).

---

## 2. Daftar semua topik tersimpan — `GET /search/topics/list`

Query opsional: `is_active` (default `true`), `limit` (default 50, maks
200), `offset`.

**Request:** `GET /search/topics/list?limit=2`

**Response (`200 OK`):**
```json
{
  "success": true,
  "data": {
    "total": 11,
    "offset": 0,
    "items": [
      {
        "topic_id": "eac6fbbf-2358-4c0f-b7a8-5e2d668538b8",
        "name": "Politik",
        "description": null,
        "platforms": ["facebook", "instagram", "news", "tiktok", "twitter", "youtube"],
        "keywords": ["rupiah lemah", "blokade as iran"],
        "total_keywords": 2,
        "total_posts": 38,
        "total_comments": 37,
        "auto_crawl": true,
        "is_active": true,
        "schedule_recurring": false,
        "schedule_duration_days": null,
        "schedule_expires_at": null,
        "created_at": "2026-07-14T03:56:23.954055+00:00",
        "updated_at": "2026-07-14T03:56:23.954061+00:00"
      },
      {
        "topic_id": "13d5b54d-6988-47f2-9677-5ab575931ab7",
        "name": "Kemacetan Jakarta",
        "keywords": ["kemacetan akibat truk tersangkut di JPO Tendean"],
        "total_keywords": 1,
        "total_posts": 4,
        "total_comments": 0,
        "auto_crawl": true,
        "is_active": true,
        "schedule_recurring": false
      }
    ]
  }
}
```

---

## 3. Semua keyword lintas topik+platform — `GET /search/topics/keywords`

Gabungan SEMUA keyword dari topik aktif manapun jadi satu daftar rata,
dedup (case-insensitive) — kalau satu keyword dipakai di >1 topik, field
`topics` menunjukkan semuanya. Query opsional: `limit`, `offset`,
`limit_per_keyword`, `include_sentiment`.

**Request:** `GET /search/topics/keywords?limit=1&limit_per_keyword=2`

**Response (`200 OK`):**
```json
{
  "success": true,
  "data": {
    "total_keywords": 22,
    "offset": 0,
    "limit": 1,
    "keywords": [
      {
        "keyword": "blokade as iran",
        "topics": ["Politik"],
        "total_posts": 37,
        "total_comments": 37,
        "platforms_found": ["youtube"],
        "results": [
          {
            "id": "c3f70590-dfed-4e9c-b5db-2bfeb83903e6",
            "platform": "youtube",
            "title": "Blokade AS Mulai Berhasil! Pelabuhan Iran Lumpuh, Dunia Tunggu Negosiasi Baru",
            "author": "KONTAN TV",
            "url": "https://www.youtube.com/watch?v=01V_9uVAW-s",
            "view_count": 0,
            "likes": 0,
            "published_at": "2026-04-16T00:42:08+00:00",
            "collected_at": "2026-07-14T03:56:58.065805+00:00",
            "thumbnail_url": "https://i.ytimg.com/vi/01V_9uVAW-s/hqdefault.jpg"
          }
        ],
        "last_rescanned_at": "2026-07-14T03:56:53.382898+00:00",
        "sentiment": {
          "total_analyzed": 37,
          "positif": { "count": 5, "pct": 13.5 },
          "negatif": { "count": 10, "pct": 27.0 },
          "netral": { "count": 22, "pct": 59.5 },
          "dominant": "netral"
        }
      }
    ]
  }
}
```

---

## 4. Detail satu topik — `GET /search/topics/{topic_id}`

Query opsional: `limit_per_keyword`, `include_sentiment`.

**Request:** `GET /search/topics/13d5b54d-6988-47f2-9677-5ab575931ab7?limit_per_keyword=2`

**Response (`200 OK`, dipersingkat):**
```json
{
  "success": true,
  "data": {
    "topic_id": "13d5b54d-6988-47f2-9677-5ab575931ab7",
    "name": "Kemacetan Jakarta",
    "platforms": ["facebook", "instagram", "news", "tiktok", "twitter", "youtube"],
    "total_keywords": 1,
    "total_posts": 2,
    "keyword_details": [
      {
        "keyword": "kemacetan akibat truk tersangkut di JPO Tendean",
        "keyword_id": "dbfff414-2e0f-440b-a4f4-621156ca1767",
        "total_posts": 2,
        "posts": [
          {
            "id": "a44a17e0-7aa9-4cdd-b1c4-6582a11a7863",
            "platform": "news",
            "title": "Polisi Rekayasa Lalu Lintas Imbas Truk Tersangkut di JPO Tendean Jaksel [...]",
            "author": "Metro TV",
            "url": "https://www.metrotvnews.com/read/NgxCaw6D-polisi-rekayasa-lalu-lintas-imbas-truk-tersangkut-di-jpo-tendean-jaksel",
            "published_at": null,
            "collected_at": "2026-07-14T03:44:46.181413+00:00",
            "thumbnail_url": "https://cdn25.metrotvnews.com/dynamic/content/2026/07/14/NgxCaw6D/a_6a5592c005012.jpeg?w=1024"
          }
        ],
        "last_rescanned_at": "2026-07-14T03:45:01.187595+00:00",
        "sentiment": { "total_analyzed": 0 }
      }
    ],
    "auto_crawl": true,
    "schedule_recurring": false,
    "schedule_expires_at": null,
    "created_at": "2026-07-14T03:42:57.183212+00:00",
    "updated_at": "2026-07-14T03:42:57.183218+00:00"
  }
}
```
Catatan: untuk platform News/artikel, field `title` berisi konten artikel
lengkap hasil scrape (bisa sangat panjang), bukan cuma judul singkat.

**Response — ID tidak ditemukan (`404 Not Found`):**
```json
{ "success": false, "error": { "code": "NOT_FOUND", "message": "Topik {id} tidak ditemukan" } }
```

---

## 5. Cari ulang topik tersimpan — `POST /search/topics/{topic_id}/search`

Untuk UI "pilih topik dari dropdown, klik Search" — cukup kirim `topic_id`,
**tidak perlu** kirim ulang `name`/`keywords`/`platforms` (beda dengan
`POST /search/topics`). Pakai keyword+platform yang sudah tersimpan di
topik itu.

**Request:** `POST /search/topics/c2b1e6b7-da54-4c78-ac95-1fc94f0cf85f/search`
```json
{ "limit_per_keyword": 10, "include_sentiment": true }
```
(body boleh dikirim kosong `{}`, semua field opsional dengan default)

**Response (`200 OK`):**
```json
{
  "success": true,
  "data": {
    "topic_id": "c2b1e6b7-da54-4c78-ac95-1fc94f0cf85f",
    "topic": "Dok Test Smart Search",
    "platforms": ["news"],
    "status": "queued",
    "total_posts": 0,
    "status_per_keyword": { "frasa unik dokumentasi xyz789": "queued" },
    "sentiment_per_keyword": {},
    "results": [],
    "queued_keywords": ["frasa unik dokumentasi xyz789"],
    "note": "Keyword dengan status 'queued' sedang dicari ke third-party SATU PER SATU di background (Apify/Firecrawl/YouTube API). Cek lagi lewat GET /search/topics/{topic_id} setelah beberapa saat."
  }
}
```

**Response — topik tidak ditemukan/nonaktif (`404 Not Found`):**
```json
{ "success": false, "error": { "code": "NOT_FOUND", "message": "Topik {id} tidak ditemukan atau sudah dinonaktifkan" } }
```

---

## 6. Atur jadwal pencarian berkala — `POST /search/topics/{topic_id}/schedule`

Aktifkan/nonaktifkan atau ubah durasi pemindaian harian otomatis (Celery
Beat) **tanpa perlu search ulang dari awal**. Durasi selalu dihitung dari
SEKARANG (saat endpoint dipanggil), bukan dari kapan topik pertama kali
dibuat.

**Request:**
```json
{ "enabled": true, "duration_days": 3 }
```

**Response (`200 OK`):**
```json
{
  "success": true,
  "data": {
    "topic_id": "c2b1e6b7-da54-4c78-ac95-1fc94f0cf85f",
    "name": "Dok Test Smart Search",
    "schedule_recurring": true,
    "schedule_duration_days": 3,
    "schedule_started_at": "2026-07-14T04:05:22.030543+00:00",
    "schedule_expires_at": "2026-07-17T04:05:22.030543+00:00"
  }
}
```
Kirim `{"enabled": false}` untuk menonaktifkan jadwal berkala kapan saja.

Selama jadwal aktif (`schedule_expires_at` belum lewat), topik ini otomatis
di-scan ulang tiap hari — cek DB dulu (skip kalau ada post baru dalam
cooldown), baru tier-3 kalau genuinely basi.

---

## 7. Hapus / nonaktifkan topik — `DELETE /search/topics/{topic_id}`

**Bukan hapus permanen** — cuma menonaktifkan (`is_active=false`). Keyword
dan post/comment yang sudah ditemukan tetap tersimpan permanen, dan topik
otomatis berhenti diambil jadwal berkala.

**Response (`200 OK`):**
```json
{ "success": true, "data": { "message": "Topik 'Dok Test Smart Search' dinonaktifkan" } }
```

---

## Error umum yang bisa muncul di endpoint manapun di atas

| Situasi | Status | Body |
|---|---|---|
| Tidak kirim token sama sekali | `401` | `{"success":false,"error":{"code":"UNAUTHORIZED","message":"Authentication required"}}` |
| Token kadaluarsa/rusak | `401` | `{"success":false,"error":{"code":"UNAUTHORIZED","message":"..."}}` |
| `topic_id` tidak ditemukan | `404` | `{"success":false,"error":{"code":"NOT_FOUND","message":"Topik {id} tidak ditemukan"}}` |

---

## Ringkasan cepat

| Method | Path | Body | Catatan |
|---|---|---|---|
| POST | `/search/topics` | `{topics, platforms?, limit_per_keyword?, save_topic?, auto_crawl?, ...}` | Cari/simpan topik baru by nama |
| GET | `/search/topics/list` | — | Dashboard: semua topik tersimpan |
| GET | `/search/topics/keywords` | — | Semua keyword lintas topik+platform, dedup |
| GET | `/search/topics/{id}` | — | Detail 1 topik + semua post per keyword |
| POST | `/search/topics/{id}/search` | `{limit_per_keyword?, include_sentiment?}` | Cari ulang topik tersimpan by ID |
| POST | `/search/topics/{id}/schedule` | `{enabled, duration_days?}` | Atur pemindaian berkala harian |
| DELETE | `/search/topics/{id}` | — | Soft-delete (nonaktifkan) |

**Riwayat perubahan penting:** per 2026-07-14, mekanisme konfirmasi manual
(`confirm_third_party`, status `needs_confirmation`) **sudah dihapus total**
dari seluruh endpoint di atas. Sebelumnya, kalau tier-1 (DB) kosong,
endpoint berhenti dulu dan minta konfirmasi eksplisit sebelum memanggil
tier-3 (Apify/Firecrawl/YouTube API). Sekarang begitu tier-1 kosong dan
`auto_crawl=true` (default), keyword **langsung** diantrekan ke tier-3 di
background — tanpa request kedua. Kalau ada dokumentasi/kode frontend lain
yang masih mengacu ke `confirm_third_party`/`needs_confirmation_keywords`,
itu sudah usang dan perlu disesuaikan (lihat contoh yang sudah diperbaiki
di `frontend-reference/nextjs-smart-search-confirm/demo-scaffold/`).
