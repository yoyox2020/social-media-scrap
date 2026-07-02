# Flow Viral Tracking — Input, Proses, Output (Case Nyata)

---

## Gambaran Singkat

Sistem ini bekerja seperti **agen pengintai otomatis**:

1. Dari ribuan video yang sudah di-scraping, dia cari yang views-nya **≥ 1 juta**
2. Channel pemilik video itu langsung **diawasi selama 7 hari** (5 video/hari)
3. Setelah video terkumpul, dia analisis komentar — siapa yang **komentar >10x** di video-video tersebut
4. Akun tersebut di-**flag** dan channelnya juga diawasi 7 hari lagi

---

## INPUT — Dari mana data masuk?

### Sumber utama input: Tabel `posts`

Input sistem ini bukan dari user. Input datang dari scraping keyword biasa yang sudah berjalan.

Contoh nyata: sistem scraping keyword "mr beast" → mendapatkan video ini dan simpan ke tabel `posts`:

```
post_id:     54f8b4af-285c-45a8-8866-7294574ef9d8
external_id: iogcY_4xGjo                          ← YouTube video ID
judul:       $1 vs $1,000,000 Hotel Room!
keyword:     mr beast
platform:    youtube
metadata: {
  "views": 452393214,                             ← 452 JUTA views — ini yang jadi trigger
  "channel_id": "UCX6OQ3DkcsbYNE6H8uQQuVA",
  "channel_name": "MrBeast",
  ...
}
```

Begitu juga dengan video lain — semua video yang pernah di-scraping tersimpan di sini. Saat ini ada **162 post** di DB yang melewati threshold 1 juta views.

---

## PROSES 1 — Deteksi (setiap 6 jam)

### Service: `detect_and_create_trackers()`
### Worker: `detect_viral_posts_task`

Setiap 6 jam, sistem scan seluruh tabel `posts`:

```sql
SELECT * FROM posts
WHERE platform = 'youtube'
  AND (metadata->>'views')::bigint >= 1000000
ORDER BY collected_at ASC;  -- post terlama diproses duluan
```

Untuk setiap post yang ditemukan, sistem cek:
- Apakah post ini sudah jadi trigger tracker? → skip
- Apakah channel-nya sudah punya tracker aktif? → skip
- Jika belum → **buat tracker baru**

### Contoh nyata: 5 tracker yang dibuat dari post viral

| Trigger Video | Views | Channel → Tracker Dibuat |
|---|---|---|
| Tabola Bale - Silet Open Up | **540 juta** | SILET OPEN UP → tracker `137512d6-...` |
| Last to Leave Their Circle | **208 juta** | MrBeast → tracker `a15c7453-...` |
| KAROL G - OKI DOKI | **127 juta** | KAROL G → tracker `afb6200b-...` |
| Some people should just watch | **113 juta** | Anwar Jibawi → tracker `89714db0-...` |
| KOTAK - Pelan-Pelan Saja | **76 juta** | KOTAKBandOFFICIAL → tracker `192c1d5c-...` |

Setiap tracker tersimpan di tabel `viral_channel_trackers`:

```
tracker_id:        a15c7453-62c4-47ed-872b-22c37588d50a
channel_id:        UCX6OQ3DkcsbYNE6H8uQQuVA
channel_name:      MrBeast
tracker_type:      viral
trigger_post_id:   (id post "Last to Leave Their Circle")
status:            active
started_at:        2026-07-01
ends_at:           2026-07-08          ← otomatis 7 hari dari mulai
posts_collected:   0
last_scraped_date: (null)
scrape_logs:       []
```

---

## PROSES 2 — Scraping Harian Channel (setiap hari jam 03:00 + setelah deteksi)

### Service: `run_daily_channel_scrape()`
### Worker: `viral_channel_daily_scrape_task`

Untuk setiap tracker aktif yang belum scraping hari ini:

```
1. Ambil 5 video terbaru dari channel MrBeast
   → EnsembleData: GET /youtube/channel/videos?browseId=UCX6OQ3DkcsbYNE6H8uQQuVA
   → Jika 493/495 (expired/quota) → fallback ke YouTube Data API v3:
      GET search?channelId=UCX6OQ3DkcsbYNE6H8uQQuVA&order=date&maxResults=10

2. Cek apakah video sudah ada di DB → skip duplikat

3. Simpan video baru ke tabel posts dengan tag:
   metadata: { "tracker_id": "a15c7453-...", "source": "viral_tracking" }

4. Update tracker:
   posts_collected += (jumlah video baru)
   last_scraped_date = hari ini
   scrape_logs += { "day": 1, "date": "2026-07-02", "posts_new": 3, "posts_skipped": 2 }

5. Langsung queue: collect_youtube_comments_task per video baru
```

### Contoh log scrape harian yang tersimpan (scrape_logs JSONB):

Case FOX Sports — tracker yang berhasil collect 5 post di hari pertama:
```json
[
  { "day": 1, "date": "2026-07-01", "posts_new": 5, "posts_skipped": 0 },
  { "day": 2, "date": "2026-07-02", "posts_new": 0, "posts_skipped": 0,
    "error": "EnsembleData error: HTTP 493: Subscription expired" }
]
```

Artinya: hari pertama dapat 5 video baru, hari kedua EnsembleData expired — setelah fix fallback, seharusnya switch ke YouTube Data API v3.

### Video yang sudah terkumpul via tracker (output di tabel `posts`):

```
video_id    | judul                                          | tracker_id        | collected_at
ZgiLkk8BtSY | Bosnia And Herzegovina Train Before USA       | 4f932aba-... | 2026-07-01
m7UZVhjP9FQ | Senegal Train Before Belgium                  | 4f932aba-... | 2026-07-01
3vqHHgMWk6k | Post-Match Press Conference: France Deschamps | 4f932aba-... | 2026-07-01
SoQCKUDfpvo | Kylian Mbappe Goal - France 3-0 Sweden        | 4f932aba-... | 2026-07-01
otJkmF7hC-Q | Kylian Mbappe Scores Again! France 3-0 Sweden | 4f932aba-... | 2026-07-01
```

Ini milik tracker FOX Sports — 5 video FIFA World Cup yang dikumpulkan pada hari pertama tracker berjalan.

---

## PROSES 3 — Analisis Commenter (setelah setiap scrape)

### Service: `check_and_flag_commenters()`
### Worker: `check_flagged_commenters_task`

Setelah scraping selesai, sistem cek komentar pada semua post yang dikumpulkan via tracker ini:

```sql
SELECT c.author, c.metadata->>'author_channel_id' AS channel_id, COUNT(*) AS total
FROM comments c
JOIN posts p ON p.id = c.post_id
WHERE p.metadata->>'tracker_id' = 'a15c7453-...'   -- tracker MrBeast
GROUP BY c.author, c.metadata->>'author_channel_id'
HAVING COUNT(*) > 10;                               -- komentar lebih dari 10x
```

Jika ada — contoh fiktif untuk ilustrasi:
```
@superfan_mrbeast  | UCaaa... | 23 komentar di 4 video MrBeast  → DIFLAG
@spammer_channel   | UCbbb... | 18 komentar di 3 video MrBeast  → DIFLAG
```

Akun ini masuk ke tabel `flagged_accounts`:
```
id:                  (uuid baru)
channel_id:          UCaaa...
channel_name:        @superfan_mrbeast
comment_count:       23
tracker_id:          a15c7453-...     ← dari tracker MrBeast
trigger_post_id:     (post MrBeast yang viral)
analysis_tracker_id: (uuid tracker baru)  ← tracker baru untuk menganalisis channel @superfan
flagged_at:          2026-07-02
```

Sistem langsung buat **tracker baru** untuk channel @superfan_mrbeast:
```
tracker_type: flagged_commenter
channel_id:   UCaaa...
ended_at:     7 hari dari sekarang
```

Dan channel ini juga diawasi 7 hari — untuk tahu: apakah @superfan_mrbeast ini memang fan biasa, atau channel yang sedang menjalankan kampanye engagement berbayar?

---

## OUTPUT — Data tersimpan dimana dan bisa dilihat dimana?

### 1. Monitor Web (tanpa login)
```
http://187.77.125.10:8000/scraping-status
```
Menampilkan:
- Jumlah tracker aktif (76), posts via tracker, akun diflag
- Tabel 10 tracker terakhir dengan status per hari
- Jika ada error (seperti EnsembleData expired) → terlihat di kolom "Log Terakhir"

---

### 2. List Semua Tracker (butuh token)
```
GET /api/v1/youtube/viral-tracking?status=active
```
Output:
```json
{
  "data": {
    "total": 76,
    "items": [
      {
        "id": "a15c7453-62c4-47ed-872b-22c37588d50a",
        "channel_name": "MrBeast",
        "channel_id": "UCX6OQ3DkcsbYNE6H8uQQuVA",
        "tracker_type": "viral",
        "status": "active",
        "posts_collected": 0,
        "started_at": "2026-07-01T...",
        "ends_at": "2026-07-08T...",
        "trigger_post_id": "54f8b4af-..."
      }
    ]
  }
}
```

---

### 3. Detail Tracker MrBeast + Timeline + Posts + Flagged (butuh token)
```
GET /api/v1/youtube/viral-tracking/a15c7453-62c4-47ed-872b-22c37588d50a
```
Output:
```json
{
  "data": {
    "tracker": {
      "channel_name": "MrBeast",
      "status": "active",
      "posts_collected": 0,
      "trigger_post_id": "54f8b4af-..."
    },
    "progress": {
      "total_days": 7,
      "days_elapsed": 2,
      "days_remaining": 5,
      "percent": 28.6
    },
    "scrape_timeline": [
      { "day": 1, "date": "2026-07-01", "status": "skipped",  "posts_new": 0 },
      { "day": 2, "date": "2026-07-02", "status": "error",    "posts_new": 0,
        "error": "EnsembleData expired" },
      { "day": 3, "date": "2026-07-03", "status": "pending",  "posts_new": null },
      { "day": 4–7: pending... }
    ],
    "posts": [
      { "video_id": "...", "title": "...", "views": 12345 }
    ],
    "flagged_accounts": []
  }
}
```

---

### 4. Akun yang Diflag (butuh token)
```
GET /api/v1/youtube/flagged-accounts
```
Output:
```json
{
  "data": {
    "total": 0,
    "items": [
      {
        "channel_name": "@superfan_mrbeast",
        "comment_count": 23,
        "tracker_id": "a15c7453-...",
        "analysis_tracker_id": "uuid-tracker-baru-untuk-channel-ini",
        "flagged_at": "2026-07-02T..."
      }
    ]
  }
}
```

---

### 5. Trigger Manual (jalankan sekarang tanpa menunggu jadwal)
```
POST /api/v1/youtube/viral-tracking/detect
→ Cek DB sekarang, buat tracker baru jika ada post baru ≥1M views

POST /api/v1/youtube/viral-tracking/a15c7453-.../scrape
→ Paksa scrape channel MrBeast sekarang (reset last_scraped_date → null)
```

---

## Kondisi Nyata Sekarang

| Kondisi | Nilai |
|---|---|
| Post di DB dengan views ≥1 juta | 162 post |
| Tracker aktif | 76 channel dipantau |
| Post terkumpul via tracker | 5 post (FOX Sports, hari pertama) |
| Akun diflag | 0 (belum ada komentar >10x terkumpul) |
| Status EnsembleData | **EXPIRED (493)** — tracker scraping gagal |
| Fallback setelah fix | YouTube Data API v3 via `search?channelId=UCxxx&order=date` |

### Kenapa `posts_collected = 0` di hampir semua tracker?

Tracker dibuat tanggal 2026-07-01. Saat scrape pertama dijalankan, EnsembleData mengembalikan HTTP 493 (subscription expired). Error ini dicatat di `scrape_logs`:

```json
{ "day": 1, "date": "2026-07-02", "posts_new": 0,
  "error": "EnsembleData error: HTTP 493: Subscription expired" }
```

Setelah fix yang baru di-deploy: scrape berikutnya akan otomatis switch ke YouTube Data API v3 dan mengambil video terbaru dari channel via `search?channelId=`.

---

## Diagram Alur Lengkap

```
[Scraping Keyword Biasa]          [Trigger Manual]
         |                               |
         v                               v
    tabel: posts                    POST /viral-tracking/detect
    (750 post, 162 di atas 1M)             |
         |                               |
         +─────────────────┬─────────────+
                           |
                    [Setiap 6 Jam]
               detect_viral_posts_task
                           |
                  Scan: posts WHERE views >= 1M
                  Cek: channel belum ada tracker aktif
                           |
                    INSERT viral_channel_trackers
                    tracker_type = 'viral'
                    ends_at = starts_at + 7 hari
                           |
                    [Setiap Hari / Manual]
               viral_channel_daily_scrape_task
                           |
               GET channel videos (EnsembleData)
               → jika 493/495: fallback YouTube Data API v3
                           |
                INSERT posts WHERE tracker_id = tracker.id
                UPDATE viral_channel_trackers.scrape_logs
                           |
               collect_youtube_comments_task (per video baru)
                           |
                INSERT comments
                           |
               check_flagged_commenters_task
                           |
               SELECT komentar WHERE COUNT > 10
                           |
                INSERT flagged_accounts
                INSERT viral_channel_trackers (tracker_type='flagged_commenter')
                           |
               [Siklus ulang untuk tracker commenter]

OUTPUT (bisa dilihat):
├── /scraping-status         → monitor web real-time
├── GET /viral-tracking      → list tracker aktif/selesai
├── GET /viral-tracking/{id} → detail + timeline 7 hari + posts + flagged
└── GET /flagged-accounts    → daftar akun mencurigakan
```
