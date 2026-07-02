# Viral Update — Dokumentasi Perubahan Fitur Viral Tracking

**Tanggal:** 2026-07-01 s/d 2026-07-02  
**Scope:** `GET /videos/viral`, `POST /videos/viral`, Keyword Tracker 7 Hari, Dashboard Monitoring

---

## Ringkasan Perubahan

Fitur viral tracking mengalami peningkatan signifikan dalam tiga aspek:
1. **Nested comments + sentiment** per video (mirip smart-search)
2. **Auto-scrape komentar** otomatis saat video belum punya komentar
3. **Keyword Tracker 7 Hari** — tracking otomatis setelah POST /videos/viral dengan `q=`

---

## 1. Struktur Response Baru (GET & POST /videos/viral)

### Sebelum

```json
{
  "total": 5,
  "items": [
    {
      "rank": 1,
      "video_id": "abc123",
      "title": "...",
      "view_count": 1200000
    }
  ]
}
```

### Sesudah

```json
{
  "total": 5,
  "filter": {
    "keyword_id": null,
    "q": "FC Barcelona",
    "date_from": null,
    "date_to": null
  },
  "tracking": {
    "keyword_tracker_id": "0ee33a45-a3a4-4cd7-8420-a68aacefdff6",
    "tracking_days": 7,
    "note": "Tracking aktif untuk 'FC Barcelona' selama 7 hari"
  },
  "stats": {
    "total_videos": 5,
    "total_comments": 86,
    "total_analyzed": 86,
    "coverage_pct": 100.0
  },
  "sentiment": {
    "positif":  { "count": 11, "percentage": 12.8 },
    "negatif":  { "count": 3,  "percentage": 3.5  },
    "netral":   { "count": 72, "percentage": 83.7 },
    "dominant": "netral",
    "total_analyzed": 86
  },
  "items": [
    {
      "rank": 1,
      "video_id": "GqQiijlMbuI",
      "title": "BARÇA - REAL MADRID | LALIGA 2022/23",
      "channel": "FC Barcelona",
      "view_count": 4200000,
      "comment_count": 46,
      "sentiment_summary": {
        "positif": { "count": 4,  "percentage": 20.0 },
        "negatif": { "count": 0,  "percentage": 0.0  },
        "netral":  { "count": 16, "percentage": 80.0 }
      },
      "comments": [
        {
          "id": "uuid-komentar",
          "content": "This pass from Lewy is just... 😮‍💨",
          "author": "user123",
          "sentiment": "netral",
          "score": 0.0
        }
      ]
    }
  ]
}
```

---

## 2. Auto-Scrape Komentar

### Cara Kerja

Saat GET atau POST `/videos/viral` dipanggil dengan `limit_comments > 0`:

1. Sistem mengecek komentar yang sudah ada di DB untuk setiap video hasil query
2. Video yang belum punya komentar (**maks 3 video per request**) akan di-scrape otomatis
3. Komentar dianalisis dengan lexicon sentiment (11.569 kata kunci)
4. Hasil komentar dikembalikan nested di bawah video masing-masing

```
GET /videos/viral?q=FC Barcelona&limit=3&limit_comments=20
  → Cek DB: video A = 0 komentar, video B = 0 komentar
  → Auto-scrape video A & B (maks 3)
  → Setiap komentar disimpan dengan post_id = id video yang benar
  → Analisis lexicon sentiment per komentar
  → Return: comments nested per video + sentiment_summary
```

### Parameter Auto-Scrape Per Request

| Parameter | Nilai |
|---|---|
| `limit_comments` | 0–20 komentar per video (default: 20, max: 20) |
| Video yang di-scrape | Maks 3 per request (yang `comment_count = 0`) |
| `max_comments` per video | 20 komentar |
| `max_pages` komentar | 1 halaman |

### Jaminan Konsistensi Komentar

Komentar **selalu terikat ke post yang benar** via:
```sql
JOIN comments c ON c.post_id = p.id
WHERE c.post_id::text IN ('uuid-video-1', 'uuid-video-2', ...)
```
Tidak ada cross-contamination komentar antar video yang berbeda.

---

## 3. Keyword Tracker 7 Hari

### Tabel Baru: `viral_keyword_trackers`

```sql
CREATE TABLE viral_keyword_trackers (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    search_query      VARCHAR(500) NOT NULL,   -- kata kunci yang dilacak
    status            VARCHAR(20)  NOT NULL DEFAULT 'active',  -- active | completed
    started_at        TIMESTAMPTZ  NOT NULL,
    ends_at           TIMESTAMPTZ  NOT NULL,   -- started_at + 7 hari
    posts_collected   INTEGER      NOT NULL DEFAULT 0,
    last_scraped_date DATE,                    -- tanggal terakhir scraping
    day_logs          JSONB        NOT NULL DEFAULT '[]',  -- log per hari
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);
```

### Cara Kerja End-to-End

```
1. User → POST /videos/viral { "q": "timothy weah", "limit": 10 }

2. API → cari video di DB
   └─ Jika ditemukan (total > 0):
      └─ create_keyword_tracker("timothy weah")
         └─ Jika sudah ada tracker aktif → pakai yang lama (no duplicate)
         └─ Jika belum ada → buat baru, ends_at = now + 7 hari
      └─ Queue viral_keyword_daily_scrape_task(tracker_id)

3. Setiap hari jam 12:00 WIB (Celery Beat):
   └─ viral_tracking_daily_check_task()
      └─ resume_active_keyword_trackers()
         └─ Tandai yang ends_at < now → status = "completed"
         └─ Queue scrape untuk yang belum scraping hari ini

4. viral_keyword_daily_scrape_task(tracker_id):
   └─ search_by_keyword("timothy weah", depth=1)
      └─ Fallback ke YouTube Data API v3 jika EnsembleData 493/495
   └─ Ambil 5 video teratas
   └─ Simpan video baru ke DB (metadata: { keyword_tracker_id, source: "keyword_tracking" })
   └─ collect_comments_for_video() per video baru (50 komentar, 1 halaman)
   └─ Update tracker: last_scraped_date, posts_collected, day_logs
   └─ Jika hari ke-7 → status = "completed"

5. Setelah 7 hari → tracker.status = "completed" (tampil "done" di dashboard)
```

### Parameter Keyword Tracker 7 Hari

| Parameter | Nilai |
|---|---|
| `tracking_days` | 7 hari (tetap) |
| `posts_per_day` | 5 video per hari |
| `max_comments_per_video` | 50 komentar |
| `max_pages_komentar` | 1 halaman |
| Beat schedule | Setiap hari jam 12:00 WIB |
| Deduplication | Tracker baru tidak dibuat jika sudah ada active untuk query yang sama |

### Format `day_logs`

```json
[
  {
    "day": 1,
    "date": "2026-07-02",
    "posts_new": 3,
    "posts_skipped": 2
  },
  {
    "day": 2,
    "date": "2026-07-03",
    "posts_new": 1,
    "posts_skipped": 4
  },
  {
    "day": 3,
    "date": "2026-07-04",
    "posts_new": 0,
    "posts_skipped": 5,
    "error": "EnsembleData 493: subscription expired"
  }
]
```

---

## 4. Dashboard Monitoring — /scraping-status

URL: `http://187.77.125.10:8000/scraping-status`  
Auto-refresh: setiap 15 detik

### Section Baru: "Keyword Tracking (7 Hari)"

Menampilkan tabel semua keyword tracker dengan:

| Kolom | Keterangan |
|---|---|
| Keyword Pencarian | Query yang dilacak (mis: "FC Barcelona") |
| Status | `active` (hijau) / `done` (abu) |
| Hari ke- | N / 7 (hari scraping yang sudah dilakukan) |
| Progress | Progress bar visual persentase |
| Post | Total video yang berhasil dikumpulkan |
| Tgl Scrape | Tanggal terakhir scraping |
| Mulai | Tanggal tracker dibuat |
| Berakhir | Tanggal tracker selesai (7 hari dari mulai) |
| Log Terakhir | Hasil scraping terakhir (+N baru / error) |

### Section Existing: "Viral Tracking" (Channel-Based)

Tetap ada untuk channel tracker (post >= 1M views):

| Kolom | Keterangan |
|---|---|
| Channel | Nama channel yang dipantau |
| Tipe | `viral` (dipicu 1M views) / `flagged` (commenter aktif) |
| Status | `active` / `completed` |
| Hasil Scrape | Sukses / Error / Belum Scrape |
| Post | Total video terkumpul |

---

## 5. Perbedaan Dua Jenis Tracker

| Aspek | Channel Tracker (`viral_channel_trackers`) | Keyword Tracker (`viral_keyword_trackers`) |
|---|---|---|
| Trigger | Post ≥ 1 juta views terdeteksi | POST /videos/viral dengan `q=` |
| Objek dilacak | Channel YouTube (UCxxx) | Kata kunci pencarian |
| Cara scrape | `get_channel_videos(channel_id)` | `search_by_keyword(query)` |
| Tabel posts | `metadata['source'] = 'viral_tracking'` | `metadata['source'] = 'keyword_tracking'` |
| Flagged commenter | Ya (>10x komentar → buat tracker baru) | Tidak |
| Beat schedule | Setiap hari 12:00 WIB | Setiap hari 12:00 WIB (sama) |

---

## 6. DB Migration yang Dijalankan

```sql
-- Buat tabel keyword tracker
CREATE TABLE viral_keyword_trackers ( ... );
CREATE INDEX ix_viral_keyword_trackers_search_query ON viral_keyword_trackers(search_query);
CREATE INDEX ix_viral_keyword_trackers_status ON viral_keyword_trackers(status);

-- Sebelumnya (sesi sebelum): buat lexicon_analyses nullable untuk viral posts
ALTER TABLE lexicon_analyses ALTER COLUMN keyword_id DROP NOT NULL;
```

---

## 7. File yang Diubah

| File | Perubahan |
|---|---|
| `app/domain/viral_tracking/models.py` | Tambah model `ViralKeywordTracker` |
| `app/domain/youtube_analysis/models.py` | `keyword_id` → `nullable=True` di `LexiconAnalysis` |
| `app/services/viral_tracking/service.py` | Tambah `create_keyword_tracker`, `run_daily_keyword_scrape`, `resume_active_keyword_trackers`, `_append_keyword_log` |
| `app/services/youtube/pipeline_service.py` | `collect_comments_for_video` + `_analyze_comments_lexicon` → `keyword_id: uuid \| None` |
| `app/workers/viral_tracking_worker.py` | Tambah `viral_keyword_daily_scrape_task`; update `viral_tracking_daily_check_task` |
| `app/api/v1/youtube/router.py` | GET/POST viral: nested comments, auto-scrape, sentiment_summary, keyword tracker creation; monitor-public: tambah `keyword_tracking` |
| `app/main.py` | `/scraping-status`: tambah section Keyword Tracking 7 Hari dengan progress bar |

---

## 8. Contoh Penggunaan

### Mulai tracking keyword "timothy weah" selama 7 hari

```bash
curl -X POST http://187.77.125.10:8000/api/v1/youtube/videos/viral \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"q": "timothy weah", "limit": 20, "limit_comments": 20}'
```

**Response (key fields):**
```json
{
  "tracking": {
    "keyword_tracker_id": "uuid-tracker",
    "tracking_days": 7,
    "note": "Tracking aktif untuk 'timothy weah' selama 7 hari"
  },
  "stats": {
    "total_videos": 20,
    "total_comments": 180,
    "coverage_pct": 95.0
  },
  "items": [
    {
      "title": "Timothy Weah Goal vs...",
      "comment_count": 12,
      "sentiment_summary": {
        "positif": {"count": 8,  "percentage": 66.7},
        "negatif": {"count": 1,  "percentage": 8.3 },
        "netral":  {"count": 3,  "percentage": 25.0}
      },
      "comments": [ ... ]
    }
  ]
}
```

### Cek status tracking di dashboard

Buka browser: `http://187.77.125.10:8000/scraping-status`

Scroll ke section **"Keyword Tracking (7 Hari)"** — tampil semua tracker aktif dan yang sudah done.

### Cek via API

```bash
curl http://187.77.125.10:8000/api/v1/youtube/monitor-public \
  | jq '.data.keyword_tracking'
```

```json
{
  "active_trackers": 2,
  "completed_trackers": 0,
  "posts_collected": 15,
  "recent_activity": [
    {
      "tracker_id": "0ee33a45-...",
      "search_query": "FC Barcelona",
      "status": "active",
      "posts_collected": 5,
      "days_done": 1,
      "last_scraped_date": "2026-07-02",
      "started_at": "2026-07-02T12:30:25+00:00",
      "ends_at": "2026-07-09T12:30:25+00:00",
      "last_log": {
        "day": 1,
        "date": "2026-07-02",
        "posts_new": 5,
        "posts_skipped": 0
      }
    }
  ]
}
```

---

## 9. Batasan & Catatan Penting

- **ML/AI dinonaktifkan**: `worker-ai`, `ollama`, `sentence_transformers` tidak dipakai. Sentiment hanya dari lexicon (11.569 kata).
- **Keyword tracker tidak trigger jika DB kosong**: Jika `q=` tidak menemukan hasil di DB, tracker tidak dibuat (kecuali `auto_search=true` dan hasil disimpan dari YouTube API).
- **EnsembleData 493**: Subscription expired — fallback otomatis ke YouTube Data API v3 untuk semua operasi search dan comment.
- **Deduplication tracker**: Jika POST /videos/viral dipanggil berulang dengan `q=` yang sama, tracker lama dipakai (tidak membuat duplikat).
- **Viral posts `keyword_id = NULL`**: Post dari viral_tracking tidak terikat ke keyword manapun — ini disengaja dan sudah di-handle di `lexicon_analyses.keyword_id` (nullable).
