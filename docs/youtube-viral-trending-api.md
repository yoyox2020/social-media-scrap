# API Video Viral/Trending YouTube + Notifikasi

Dokumentasi untuk tim frontend — kumpulan endpoint utk menampilkan video YouTube
yang viral/trending, plus notifikasi otomatis saat topik tersimpan jadi viral.
Base URL: `https://api.dismi.xyz/api/v1`.

**Catatan penting (2026-07-18):** skema database `posts` baru saja ditambah kolom
`title`/`tags`/`media`/`metrics` (persiapan fitur agent AI ke depan) — **TAPI
endpoint di bawah ini BELUM disambungkan ke kolom baru itu**, semua masih pakai
struktur field yang SAMA seperti sebelumnya (`content`→judul, `metadata.thumbnail`,
`metadata.views`, dst). Jadi kalau integrasi frontend sudah ada dari sebelumnya,
**tidak perlu diubah** — field response tetap sama persis.

---

## Ringkasan: endpoint mana yang dipakai?

| Kebutuhan | Endpoint | Auth |
|---|---|---|
| Dashboard publik (share link, tanpa login) | `GET /youtube/trending-public` | Tidak |
| Daftar video viral (urut views) + sentimen komentar | `GET /youtube/videos/viral` | Ya |
| Sama seperti di atas + filter tanggal/keyword/sorting | `POST /youtube/videos/viral` | Ya |
| Chart resmi YouTube "Trending" (bukan dari data kita) | `GET /youtube/videos/popular` | Ya |
| Notifikasi "topik ini baru viral" | `GET/POST /search/notifications*` | Campuran — lihat `docs/topic-notifications-api.md` |
| Status agent pencari viral otomatis (baru) | `GET /youtube/discovery-agent/status` | Tidak |

---

## 1. `GET /youtube/trending-public` — dashboard publik, TANPA login

Cocok untuk halaman yang di-share ke siapa saja (link publik, bukan dashboard internal).
Data GLOBAL (sama untuk semua orang), di-cache 10 menit.

```
GET /youtube/trending-public?geo=ID
```

**Response:**
```json
{ "success": true, "data": {
  "geo": "ID",
  "days": [
    {
      "date": "2026-07-18",
      "topics": [
        {
          "rank": 1,
          "title": "nama topik trending",
          "traffic": "500K+",
          "description": "...",
          "fetched_at": "2026-07-18T06:00:00+00:00",
          "video_count": 3,
          "top_videos": [
            {
              "title": "judul video",
              "url": "https://youtube.com/watch?v=...",
              "channel": "nama channel",
              "thumbnail": "https://...",
              "views": 500000,
              "likes": 12000,
              "published_at": "2026-07-18T05:00:00+00:00"
            }
          ]
        }
      ]
    }
  ]
}}
```

---

## 2. `GET /youtube/videos/viral` — video viral urut views (perlu login)

Cocok untuk dashboard internal, sudah termasuk ringkasan sentimen komentar.

```
GET /youtube/videos/viral?limit=20
```

**Response:**
```json
{ "success": true, "data": {
  "total": 20,
  "stats": { "total_videos": 20, "total_comments": 450, "total_analyzed": 420, "coverage_pct": 93.3 },
  "sentiment": { "positif": {...}, "negatif": {...}, "netral": {...}, "dominant": "positif", "total_analyzed": 420 },
  "items": [
    {
      "rank": 1,
      "video_id": "abc123",
      "url": "https://youtube.com/watch?v=abc123",
      "title": "judul video",
      "channel": "nama channel",
      "view_count": 1500000,
      "thumbnail_url": "https://...",
      "duration": "PT10M30S",
      "published_at": "2026-07-18T05:00:00+00:00",
      "keyword": "kata kunci terkait",
      "comment_count": 45,
      "sentiment_summary": { "positif": {"count": 20, "pct": 44.4}, "negatif": {...}, "netral": {...} },
      "comments": [
        { "id": "...", "content": "...", "author": "...", "sentiment": "positif", "score": 0.82 }
      ]
    }
  ]
}}
```

## 3. `POST /youtube/videos/viral` — sama seperti di atas, dengan filter

Tambahan body opsional: `date_from`, `date_to`, `q` (keyword — kalau DB kosong, otomatis
live-fetch dari YouTube + mulai tracking 7 hari untuk keyword itu), `sort_by`, `offset`, `limit`.
Bentuk response item **identik** dengan `GET /videos/viral` di atas, envelope tambah
`filter`, `offset`, `limit`, dan `tracking` (kalau `q` dikirim dan keyword baru).

---

## 4. `GET /youtube/videos/popular` — chart resmi YouTube (bukan dari data kita)

**Beda konsep** dari 2 endpoint di atas — ini murni live dari `mostPopular` chart
YouTube Data API v3 (video terpopuler di region tsb SAAT INI), bukan hasil scraping/
keyword kita. Cocok kalau butuh "apa yang lagi trending di YouTube Indonesia hari ini"
secara umum, bukan terkait topik yang kita track.

```
GET /youtube/videos/popular?region_code=ID&limit=20
```

**Response:**
```json
{ "success": true, "data": {
  "source": "youtube_data_api_v3", "region_code": "ID", "total": 20,
  "items": [
    {
      "rank": 1, "video_id": "...", "url": "...", "title": "...", "channel": "...",
      "channel_id": "...", "description": "...", "thumbnail_url": "...",
      "published_at": "...", "duration": "PT10M30S",
      "view_count": 2000000, "like_count": 50000, "comment_count": 3000
    }
  ]
}}
```

---

## 5. Notifikasi viral — sudah ada dokumentasi terpisah lengkap

**Lihat `docs/topic-notifications-api.md`** — 7 endpoint (unread-count, list, mark-read,
thresholds, lookback-days), semua sudah live-verified. Ringkasan: sistem cek **tiap jam**
apakah ada post (termasuk YouTube) dari topik tersimpan yang lewat ambang batas viral DAN
masih dalam jendela waktu tertentu (default 30 hari) — kalau iya, satu notifikasi baru dibuat.

---

## 6. BARU (2026-07-18): status agent pencari viral otomatis

Fitur baru — agent yang otomatis mencari video YouTube viral/trending (baik dari topik
tersimpan maupun pencarian bebas), divalidasi AI sebelum disimpan. **Ini API status/riwayat
si agent, BUKAN API buat nampilin video** — video yang dia temukan tetap masuk ke `posts`
lewat jalur yang sama, jadi otomatis akan muncul di endpoint 1-3 di atas begitu tersimpan.

```
GET /youtube/discovery-agent/status   # tanpa login — status sekarang (idle/jalan) + ringkasan run terakhir
GET /youtube/discovery-agent/runs     # tanpa login — riwayat lengkap + rincian per-kandidat
```

**Response `/status`:**
```json
{ "success": true, "data": {
  "is_running": false,
  "last_run": {
    "status": "success", "started_at": "...", "finished_at": "...",
    "topics_checked": 5, "candidates_found": 184,
    "candidates_validated": 2, "candidates_rejected": 182,
    "posts_saved": 2, "model_used": "meta-llama/llama-3.3-70b-instruct:free",
    "error_message": null
  }
}}
```

Cocok kalau frontend mau tampilkan indikator kecil "agent lagi cari video baru..." /
"terakhir jalan jam X, ketemu Y video baru" — bukan buat nampilkan daftar videonya
(pakai endpoint 1-3 utk itu).
