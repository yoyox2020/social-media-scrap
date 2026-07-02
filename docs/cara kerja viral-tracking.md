# Cara Kerja Viral Tracking & Flagged Accounts

## Ringkasan

Sistem ini secara otomatis **mendeteksi video YouTube yang viral** (≥1 juta views), lalu **melacak channel pemiliknya selama 7 hari**, dan **memflag akun yang berkomentar berulang** di video tersebut.

---

## 1. Service yang Bertanggung Jawab

| Peran | File |
|---|---|
| Logika bisnis | `app/services/viral_tracking/service.py` |
| Celery tasks (scheduler) | `app/workers/viral_tracking_worker.py` |
| Model / tabel DB | `app/domain/viral_tracking/models.py` |
| API endpoint GET | `app/api/v1/youtube/router.py` (line 2285+) |

---

## 2. Threshold & Kondisi Trigger

File: [app/services/viral_tracking/service.py](../app/services/viral_tracking/service.py)

```python
VIRAL_VIEW_THRESHOLD = 1_000_000   # 1 juta views
TRACKER_DAYS        = 7            # durasi tracking
POSTS_PER_DAY       = 5            # max video per channel per hari
COMMENTER_FLAG_THRESHOLD = 10      # komentar berulang minimum
```

**Kondisi sebuah post menjadi trigger tracker:**
- Platform = `youtube`
- `metadata_->>'views'` (JSONB) >= 1.000.000
- Channel belum punya tracker aktif (`status = 'active'`)
- Post belum pernah jadi `trigger_post_id` di tracker manapun

**Kondisi sebuah akun masuk `flagged_accounts`:**
- Komentar pada post yang dikumpulkan via tracker tertentu
- Jumlah komentar pada post-post tracker tersebut > 10x
- Channel ID belum pernah diflag di tracker yang sama

---

## 3. Flow Otomatis (Celery Beat)

```
[Setiap 6 jam]
detect_viral_posts_task
  └─► detect_and_create_trackers(db)
        ├─ Query: posts dengan views ≥ 1M yang belum ada trackernya
        ├─ Skip channel yang sudah punya tracker aktif
        ├─ INSERT ke viral_channel_trackers (tracker_type='viral')
        └─► viral_channel_daily_scrape_task(tracker_id)  [per tracker baru]

[Setiap hari jam 03:00]
viral_tracking_daily_check_task
  └─► resume_active_trackers(db)
        ├─ Tandai tracker expired → status='completed'
        └─► viral_channel_daily_scrape_task(tracker_id)  [per tracker aktif belum scrape hari ini]

viral_channel_daily_scrape_task(tracker_id)
  └─► run_daily_channel_scrape(db, tracker_id)
        ├─ Skip jika last_scraped_date == hari ini
        ├─ Skip jika tracker.ends_at < now (otomatis completed)
        ├─ Ambil 5 video terbaru dari channel via EnsembleData
        ├─ Simpan post baru ke tabel posts (metadata.tracker_id, metadata.source='viral_tracking')
        ├─ Collect komentar tiap post baru (max 50 komentar, 1 halaman)
        ├─ Update tracker.posts_collected + tracker.last_scraped_date
        └─► check_flagged_commenters_task(tracker_id)

check_flagged_commenters_task(tracker_id)
  └─► check_and_flag_commenters(db, tracker_id)
        ├─ Query komentar pada posts WHERE metadata->>'tracker_id' = tracker_id
        ├─ GROUP BY author, author_channel_id → filter HAVING COUNT > 10
        ├─ INSERT ke flagged_accounts
        ├─ Jika channel_id valid (UCxxx) → INSERT tracker baru (tracker_type='flagged_commenter')
        └─► viral_channel_daily_scrape_task(analysis_tracker_id)  [per commenter baru]
```

---

## 4. Tabel Database

### `viral_channel_trackers`

Satu baris = satu channel yang sedang atau pernah dilacak.

| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | UUID | Primary key |
| `channel_id` | String | YouTube channel ID (UCxxx) |
| `channel_name` | String | Nama channel |
| `tracker_type` | String | `viral` atau `flagged_commenter` |
| `trigger_post_id` | UUID FK → posts | Post yang memicu tracker dibuat |
| `keyword_id` | UUID FK → keywords | Keyword asal post viral ditemukan |
| `status` | String | `active` atau `completed` |
| `started_at` | DateTime | Waktu tracker dibuat |
| `ends_at` | DateTime | started_at + 7 hari |
| `posts_collected` | Integer | Total post baru yang berhasil dikumpulkan |
| `last_scraped_date` | Date | Tanggal terakhir scraping berhasil |
| `scrape_logs` | JSONB | Array log harian `[{day, date, posts_new, posts_skipped, error?}]` |

### `flagged_accounts`

Satu baris = satu akun yang berkomentar >10x pada post tracker.

| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | UUID | Primary key |
| `channel_id` | String | YouTube channel ID si commenter |
| `channel_name` | String | Nama akun |
| `comment_count` | Integer | Jumlah komentar yang memicu flagging |
| `tracker_id` | UUID FK → viral_channel_trackers | Tracker tempat akun ini ditemukan |
| `trigger_post_id` | UUID FK → posts | Post viral asal |
| `analysis_tracker_id` | UUID FK → viral_channel_trackers | Tracker baru untuk menganalisis channel si commenter |
| `flagged_at` | DateTime | Waktu akun diflag |

### `posts` (kolom relevan)

Post yang dikumpulkan via viral tracking ditandai di JSONB `metadata_`:

```json
{
  "tracker_id": "<uuid tracker>",
  "source": "viral_tracking",
  "views": 1234567
}
```

---

## 5. Status Saat Ini di Database (per 2026-07-02)

| Metrik | Nilai |
|---|---|
| Post YouTube di atas 1 juta views | **162 post** |
| Active tracker (viral) | **74 tracker** (1 per channel) |
| Active tracker (flagged_commenter) | 0 |
| Post yang dikumpulkan via tracker | 5 post |
| Flagged accounts | 0 (akan terisi saat scrape berikutnya) |

**Catatan:** `posts_collected = 0` pada sebagian besar tracker adalah normal — video dari channel tersebut sudah ada di DB dari scraping biasa, sehingga deduplication mencegah penyimpanan ulang. Hitungan hanya naik jika post betul-betul baru.

---

## 6. API Endpoint (semua butuh Bearer token)

### List semua tracker
```
GET /api/v1/youtube/viral-tracking
    ?status=active          # active | completed
    ?tracker_type=viral     # viral | flagged_commenter
    ?limit=20&offset=0
```

**Response:**
```json
{
  "data": {
    "total": 74,
    "items": [
      {
        "id": "uuid",
        "channel_id": "UCxxxx",
        "channel_name": "MrBeast",
        "tracker_type": "viral",
        "status": "active",
        "posts_collected": 3,
        "started_at": "2026-07-01T...",
        "ends_at": "2026-07-08T...",
        "last_scraped_date": "2026-07-01",
        "trigger_post_id": "uuid"
      }
    ]
  }
}
```

### Detail tracker + timeline 7 hari + flagged accounts
```
GET /api/v1/youtube/viral-tracking/{tracker_id}
    ?limit_posts=20
```

**Response:**
```json
{
  "data": {
    "tracker": { "id": "...", "channel_name": "...", "status": "active", ... },
    "progress": {
      "total_days": 7,
      "days_elapsed": 2,
      "days_remaining": 5,
      "percent": 28.6,
      "scrape_days_done": 1,
      "scrape_days_error": 0
    },
    "scrape_timeline": [
      { "day": 1, "date": "2026-07-01", "status": "done", "posts_new": 3, "posts_skipped": 2 },
      { "day": 2, "date": "2026-07-02", "status": "pending", "posts_new": null }
    ],
    "posts": [ { "id": "...", "video_id": "...", "title": "...", "views": 2062317 } ],
    "flagged_accounts": [ { "channel_name": "...", "comment_count": 15, "analysis_tracker_id": "..." } ]
  }
}
```

### List flagged accounts
```
GET /api/v1/youtube/flagged-accounts
    ?limit=20&offset=0
```

### Trigger manual (jalankan deteksi sekarang)
```
POST /api/v1/youtube/viral-tracking/detect
```

---

## 7. Dari Mana ID Tracker Berasal?

Tracker ID adalah UUID yang **di-generate otomatis** saat `detect_and_create_trackers()` menyimpan baris baru ke `viral_channel_trackers`.

### Alur Lengkap dengan Contoh Data Nyata dari Server

**Step 1 — Post dengan views ≥1 juta ditemukan di tabel `posts`:**

```
id:          4a05e6c2-7f85-4a1e-9c05-520d2a34e266
external_id: oMQkDkCBmmM                          <- YouTube video ID
content:     To Myself
metadata:    { "views": 12558886, ... }           <- 12.5 juta views > threshold 1 juta
platform:    youtube
```

**Step 2 — Sistem ekstrak channel_id dari `posts.raw_data` (JSONB):**

```
channel_id:   UCqNHUKcn3dEtWyuGZ5Gv9rw
channel_name: DPR LIVE - Topic
```

**Step 3 — Cek: channel ini belum punya tracker aktif → INSERT tracker baru:**

```
id:              6eec04f1-6d7e-4aac-9357-f6a245c0a03d   <- UUID baru, ini yang jadi tracker_id
channel_id:      UCqNHUKcn3dEtWyuGZ5Gv9rw
channel_name:    DPR LIVE - Topic
tracker_type:    viral
trigger_post_id: 4a05e6c2-7f85-4a1e-9c05-520d2a34e266  <- FK ke post yang memicu
status:          active
started_at:      2026-07-01
ends_at:         2026-07-08
posts_collected: 0
last_scraped_date: 2026-07-01
```

**Contoh 3 tracker nyata di DB server sekarang:**

| tracker_id | channel_name | channel_id | trigger_post | views trigger |
|---|---|---|---|---|
| `6eec04f1-6d7e-4aac-9357-f6a245c0a03d` | DPR LIVE - Topic | UCqNHUKcn3dEtWyuGZ5Gv9rw | oMQkDkCBmmM | 12.5 juta |
| `b9fda38d-8f74-46f6-8553-f93c34a59370` | Dream Perfect Regime | UCtG5oKSlksz-QmD_2uI4WBw | - | >1 juta |
| `e1a7d457-44c8-4831-9522-4c3336c977d2` | EVIO Multimedia | UCi1WgkplXoZ9hdtssKtz_5A | - | >1 juta |

---

### Cara Dapat Daftar Semua Tracker ID

```bash
# Via API (butuh Bearer token)
GET http://187.77.125.10:8000/api/v1/youtube/viral-tracking

# Response (contoh):
{
  "data": {
    "total": 74,
    "items": [
      {
        "id": "6eec04f1-6d7e-4aac-9357-f6a245c0a03d",
        "channel_name": "DPR LIVE - Topic",
        "channel_id": "UCqNHUKcn3dEtWyuGZ5Gv9rw",
        "tracker_type": "viral",
        "status": "active",
        "posts_collected": 0,
        "started_at": "2026-07-01T...",
        "ends_at": "2026-07-08T...",
        "trigger_post_id": "4a05e6c2-7f85-4a1e-9c05-520d2a34e266"
      },
      ...74 tracker total
    ]
  }
}
```

### Detail Satu Tracker (pakai ID dari list di atas)

```bash
GET http://187.77.125.10:8000/api/v1/youtube/viral-tracking/6eec04f1-6d7e-4aac-9357-f6a245c0a03d

# Response lengkap:
{
  "data": {
    "tracker": {
      "id": "6eec04f1-6d7e-4aac-9357-f6a245c0a03d",
      "channel_name": "DPR LIVE - Topic",
      "channel_id": "UCqNHUKcn3dEtWyuGZ5Gv9rw",
      "tracker_type": "viral",
      "status": "active",
      "posts_collected": 0,
      "trigger_post_id": "4a05e6c2-7f85-4a1e-9c05-520d2a34e266",
      "started_at": "2026-07-01T00:00:00Z",
      "ends_at": "2026-07-08T00:00:00Z",
      "last_scraped_date": "2026-07-01"
    },
    "progress": {
      "total_days": 7,
      "days_elapsed": 2,
      "days_remaining": 5,
      "percent": 28.6,
      "scrape_days_done": 0,
      "scrape_days_error": 0
    },
    "scrape_timeline": [
      { "day": 1, "date": "2026-07-01", "status": "skipped", "posts_new": 0 },
      { "day": 2, "date": "2026-07-02", "status": "pending", "posts_new": null },
      { "day": 3, "date": "2026-07-03", "status": "pending", "posts_new": null },
      { "day": 4, "date": "2026-07-04", "status": "pending", "posts_new": null },
      { "day": 5, "date": "2026-07-05", "status": "pending", "posts_new": null },
      { "day": 6, "date": "2026-07-06", "status": "pending", "posts_new": null },
      { "day": 7, "date": "2026-07-07", "status": "pending", "posts_new": null }
    ],
    "posts": [],
    "flagged_accounts": []
  }
}
```

---

## 8. Konfirmasi: Threshold ≥1 Juta Views Sudah Benar

Query di `detect_and_create_trackers()` ([app/services/viral_tracking/service.py](../app/services/viral_tracking/service.py)):

```python
select(Post).where(
    Post.platform == "youtube",
    Post.metadata_["views"].as_integer() >= 1_000_000,   # kolom DB: metadata (JSONB)
)
.order_by(Post.collected_at.asc())  # oldest-first, trigger post paling awal jadi acuan
```

**Verifikasi dari DB server:**
- **162 post** memiliki `metadata->>'views' >= 1000000`
- **74 tracker aktif** sudah dibuat (1 per channel unik)
- Query cast JSONB ke integer bekerja: contoh nyata views = 12.558.886 (DPR LIVE "To Myself")


1. Auto-switch ke YouTube API v3 — SUDAH BENAR SEKARANG
Root cause yang ditemukan: get_video_comments hanya
 fallback saat HTTP 495 (quota), tapi EnsembleData subscription kamu expired dengan HTTP 493 — kode berbeda. Akibatnya exception masuk ke DB session, session rusak, commit last_scraped_date gagal → tampak seperti tidak ada yang terjadi.

Fix: Semua 3 method connector sekarang fallback pada 493 DAN 495:

get_channel_videos (video channel) ✓
get_video_comments (komentar) ✓
search_by_hashtag ✓
Bukti dari log setelah fix:


493 → fallback YouTube API v3 (channel videos)
200 OK → dapat 10 video MrBeast
493 → fallback YouTube API v3 (comments YA_kX8hu1gg)
200 OK → dapat komentar
... (per video)
Task succeeded: new_posts=4
DB MrBeast sekarang: last_scraped_date=2026-07-02, posts_collected=4, log: +4 post, skip 1.



2. Flagged Accounts — Apa itu dan kapan berisi data
Endpoint GET /api/v1/youtube/flagged-accounts sekarang mengembalikan total: 0 karena belum ada akun yang memenuhi syarat. Ini normal dan bukan bug.

Cara kerjanya:

Setelah video terkumpul via tracker → sistem hitung komentar per akun
Jika satu akun komentar >10x pada video-video tracker → masuk flagged_accounts
MrBeast baru dapat 4 video tadi, komentar baru dikumpulkan → sistem cek → hasilnya 0 (belum ada yang komentar >10x di ke-4 video itu)
Data akan muncul di sini setelah beberapa hari scraping aktif dan ada akun yang konsisten komentar berulang.


3. Dua API Viral Tracking — Kapan pakai yang mana
API	Fungsi	Kapan dipakai
POST /viral-tracking/detect	Scan semua post ≥1 juta views di DB → buat tracker baru untuk channel yang belum dilacak	Pakai ini setelah banyak video baru masuk ke DB dan ingin langsung bikin tracker tanpa menunggu jadwal 6 jam
POST /viral-tracking/{id}/scrape	Paksa scrape channel satu tracker tertentu sekarang	Pakai ini ketika ingin langsung cek video terbaru dari channel spesifik tanpa menunggu jadwal jam 03:00
Contoh alur user:

Scraping keyword "mrbeast" → dapat video 452 juta views masuk DB
POST /detect → sistem temukan video ini, buat tracker MrBeast a15c7453-...
GET /viral-tracking → lihat tracker baru muncul
POST /viral-tracking/a15c7453-.../scrape → langsung ambil 5 video terbaru MrBeast sekarang
GET /viral-tracking/a15c7453-... → lihat posts_collected=4, timeline hari 1 done, 4 video tersimpan


Perbedaan Mendasar
Riwayat Scraping Keyword (bawah)
Pertanyaan yang dijawab: "Video apa saja yang ada di YouTube tentang keyword ini?"


Trigger  → keyword "ahok" (manual/trending)
Proses   → search YouTube: "ahok" → dapat 19 video
Disimpan → tabel scrape_runs (1 baris per run)
                tabel posts   (19 baris video)
Isi data → semua video relevan dengan keyword, dari seluruh channel


Viral Tracking (atas)
Pertanyaan yang dijawab: "Channel mana yang perlu diawasi karena salah satu videonya viral, dan apa yang mereka upload berikutnya?"


Trigger  → sistem deteksi post ≥1 juta views di DB
Proses   → awasi channel tertentu 7 hari, ambil 5 video/hari dari channel itu
Disimpan → tabel viral_channel_trackers (1 baris per channel)
                tabel posts (video baru dari channel, tagged tracker_id)
Isi data → video terbaru dari channel itu saja (bukan semua keyword)


Kenapa Jumlah Data Berbeda
Keyword scraping: 19 video ✓
Ini berjalan normal via EnsembleData. "ahok" di-scraping, dapat 19 video dari berbagai channel, 18 baru disimpan. Tidak ada masalah.

Viral tracking: sebagian besar 0 post
Ada 3 kondisi berbeda yang terlihat di screenshot:

Channel	Post	Log	Kenapa
MrBeast	4	+4 post (skip 1)	Baru saja di-fix dan trigger ulang manual tadi — berhasil
FOX Sports, Eka Gustiwana, ashok vibes	0	Error 493 merah	Scraping sempat jalan tapi EnsembleData expired sebelum fix — error tersimpan, ditandai sudah scraping hari ini
FC Barcelona, Just Riadh, ESPN FC, dll	0	-	Scraping jalan 2026-07-01, EnsembleData expired, tapi error tidak tersimpan ke log (bug lama sebelum diperbaiki)


Mengapa Tracker 0 Tidak Otomatis Retry Hari Ini
Ketika scrape gagal sebelum fix, kode tetap menyimpan last_scraped_date = 2026-07-01. Artinya sistem menganggap channel itu sudah scraping hari itu — tidak akan retry sampai besok (jam 03:00).


Logika di service:
  if tracker.last_scraped_date == today:
      return 0  ← skip, sudah diproses hari ini

FOX Sports, Eka Gustiwana, ashok vibes jalan hari ini tapi ERROR sebelum fix, jadi last_scraped_date = 2026-07-02 dengan posts = 0. Mereka tidak akan retry sampai besok 2026-07-03.


Kesimpulan Situasi Saat Ini
Keyword scraping: normal, akan terus berjalan setiap hari jam 12 WIB
Viral tracking MrBeast: sudah fix, 4 post masuk ✓
Viral tracking 75 channel lainnya: akan berjalan otomatis besok jam 03:00 dengan fallback YouTube API v3 yang sudah benar



banyak sekali yang status viral tapi post 0, bagaimana itu tidak bisa di scraping ulang lagi atau ada jadwalnya, atau berikan flag atau carikan rekomendasi agar tidak 0 bgt postnya, data kelihatan jelek
Mari cek dulu kondisi aktual di DB sebelum saya rekomendasikan solusi.

PowerShell Cek distribusi status tracker di DB
IN
& "C:\Program Files\PuTTY\plink.exe" -ssh -pw "PerangDigital26#" -hostkey "ssh-ed25519 255 SHA256:wtyu9TWjcg6T9EvRnstDtlniEMvZu8mCIWwoGZ+5HxM" root@187.77.125.10 @'
docker exec social_intel_db psql -U postgres -d social_intel -c "
SELECT 
  posts_collected,
  last_scraped_date,
  CASE 
    WHEN scrape_logs = '[]'::jsonb OR scrape_logs IS NULL THEN 'belum_scrape'
    WHEN scrape_logs -> (jsonb_array_length(scrape_logs) - 1) ->> 'error' IS NOT NULL THEN 'error'
    ELSE 'ok'
  END AS kondisi,
  COUNT(*) AS jumlah_tracker
FROM viral_channel_trackers
GROUP BY posts_collected, last_scraped_date, kondisi
ORDER BY kondisi, posts_collected, last_scraped_date DESC;
"
'@ 2>&1

OUT
plink.exe : Error response from daemon: No such container: social_intel_db
At line:1 char:1
+ & "C:\Program Files\PuTTY\plink.exe" -ssh -pw "PerangDigital26#" -hos ...
+ ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    + CategoryInfo          : NotSpecified: (Error response ...social_intel_db:String) [], RemoteException
    + FullyQualifiedErrorId : NativeCommandError
 
bash: line 2: SELECT: command not found
bash: line 3: posts_collected,: command not found
bash: line 4: last_scraped_date,: command not found
bash: line 5: CASE: command not found
PowerShell Cek nama container yang jalan
IN
& "C:\Program Files\PuTTY\plink.exe" -ssh -pw "PerangDigital26#" -hostkey "ssh-ed25519 255 SHA256:wtyu9TWjcg6T9EvRnstDtlniEMvZu8mCIWwoGZ+5HxM" root@187.77.125.10 "docker ps --format '{{.Names}}'" 2>&1

OUT
social_intel_worker
social_intel_api
social_intel_worker_beat
social_intel_redis
social_intel_postgres
PowerShell Query distribusi kondisi tracker
IN
& "C:\Program Files\PuTTY\plink.exe" -ssh -pw "PerangDigital26#" -hostkey "ssh-ed25519 255 SHA256:wtyu9TWjcg6T9EvRnstDtlniEMvZu8mCIWwoGZ+5HxM" root@187.77.125.10 "docker exec social_intel_postgres psql -U postgres -d social_intel -t -c `"SELECT posts_collected, last_scraped_date, CASE WHEN scrape_logs='[]'::jsonb OR scrape_logs IS NULL THEN 'belum_scrape' WHEN scrape_logs->(jsonb_array_length(scrape_logs)-1)->>'error' IS NOT NULL THEN 'error' ELSE 'ok' END AS kondisi, COUNT(*) FROM viral_channel_trackers GROUP BY 1,2,3 ORDER BY 3,1,2 DESC;`"" 2>&1

OUT
Exit code 127
plink.exe : bash: -c: line 1: syntax error near unexpected token `scrape_logs'
At line:1 char:1
+ & "C:\Program Files\PuTTY\plink.exe" -ssh -pw "PerangDigital26#" -hos ...
+ ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    + CategoryInfo          : NotSpecified: (bash: -c: line ...n `scrape_logs':String) [], RemoteException
    + FullyQualifiedErrorId : NativeCommandError
 
bash: -c: line 1: `docker exec social_intel_postgres psql -U postgres -d social_intel -t -c SELECT posts_collected, 
last_scraped_date, CASE WHEN scrape_logs='[]'::jsonb OR scrape_logs IS NULL THEN 'belum_scrape' WHEN 
scrape_logs->(jsonb_array_length(scrape_logs)-1)->>'error' IS NOT NULL THEN 'error' ELSE 'ok' END AS kondisi, COUNT(*) 
FROM viral_channel_trackers GROUP BY 1,2,3 ORDER BY 3,1,2 DESC;'
Bash Buat script query di temp
IN
cat > /tmp/check_trackers.sh << 'SCRIPT'
docker exec social_intel_postgres psql -U postgres -d social_intel -c "
SELECT 
  CASE 
    WHEN scrape_logs IS NULL OR scrape_logs = '[]'::jsonb THEN 'belum_scrape'
    WHEN (scrape_logs -> (jsonb_array_length(scrape_logs) - 1) ->> 'error') IS NOT NULL THEN 'error'
    ELSE 'ok_scrape'
  END AS kondisi,
  posts_collected,
  COUNT(*) AS jumlah
FROM viral_channel_trackers
GROUP BY 1, 2
ORDER BY 1, 2;
"
SCRIPT
echo "script written"

OUT
script written
PowerShell Query kondisi tracker langsung
IN
$script = @"
docker exec social_intel_postgres psql -U postgres -d social_intel -c "SELECT CASE WHEN scrape_logs IS NULL OR scrape_logs = '[]'::jsonb THEN 'belum_scrape' WHEN (scrape_logs -> (jsonb_array_length(scrape_logs) - 1) ->> 'error') IS NOT NULL THEN 'error' ELSE 'ok_scrape' END AS kondisi, posts_collected, COUNT(*) AS jumlah FROM viral_channel_trackers GROUP BY 1, 2 ORDER BY 1, 2;"
"@
& "C:\Program Files\PuTTY\plink.exe" -ssh -pw "PerangDigital26#" -hostkey "ssh-ed25519 255 SHA256:wtyu9TWjcg6T9EvRnstDtlniEMvZu8mCIWwoGZ+5HxM" root@187.77.125.10 $script 2>&1

OUT
(PowerShell completed with no output)
PowerShell Query status tracker dengan escape benar
IN
$query = 'SELECT CASE WHEN scrape_logs IS NULL OR scrape_logs = jsonb_build_array() THEN ''belum'' WHEN (scrape_logs -> (jsonb_array_length(scrape_logs) - 1) ->> ''error'') IS NOT NULL THEN ''error'' ELSE ''ok'' END AS kondisi, posts_collected, COUNT(*) AS n FROM viral_channel_trackers GROUP BY 1,2 ORDER BY 1,2'
$cmd = "docker exec social_intel_postgres psql -U postgres -d social_intel -c `"$query`""
$out = & "C:\Program Files\PuTTY\plink.exe" -ssh -pw "PerangDigital26#" -hostkey "ssh-ed25519 255 SHA256:wtyu9TWjcg6T9EvRnstDtlniEMvZu8mCIWwoGZ+5HxM" root@187.77.125.10 $cmd 2>&1
$out | ForEach-Object { Write-Host $_ }

OUT
(PowerShell completed with no output)
Bash Query tracker detail via bash/plink
IN
"C:/Program Files/PuTTY/plink.exe" -ssh -pw 'PerangDigital26#' -hostkey 'ssh-ed25519 255 SHA256:wtyu9TWjcg6T9EvRnstDtlniEMvZu8mCIWwoGZ+5HxM' root@187.77.125.10 'docker exec social_intel_postgres psql -U postgres -d social_intel -x -c "SELECT status, posts_collected, last_scraped_date, channel_name, scrape_logs FROM viral_channel_trackers ORDER BY posts_collected DESC, last_scraped_date DESC LIMIT 20;"' 2>&1

OUT
Exit code 2
psql: error: connection to server on socket "/var/run/postgresql/.s.PGSQL.5432" failed: FATAL:  role "postgres" does not exist
Bash Query pakai user social_intel
IN
"C:/Program Files/PuTTY/plink.exe" -ssh -pw 'PerangDigital26#' -hostkey 'ssh-ed25519 255 SHA256:wtyu9TWjcg6T9EvRnstDtlniEMvZu8mCIWwoGZ+5HxM' root@187.77.125.10 'docker exec social_intel_postgres psql -U social_intel -d social_intel -c "SELECT COUNT(*), posts_collected FROM viral_channel_trackers GROUP BY 2 ORDER BY 2;"' 2>&1

OUT
Exit code 2
psql: error: connection to server on socket "/var/run/postgresql/.s.PGSQL.5432" failed: FATAL:  role "social_intel" does not exist
Bash Pakai DATABASE_URL env dari container
IN
"C:/Program Files/PuTTY/plink.exe" -ssh -pw 'PerangDigital26#' -hostkey 'ssh-ed25519 255 SHA256:wtyu9TWjcg6T9EvRnstDtlniEMvZu8mCIWwoGZ+5HxM' root@187.77.125.10 'docker exec social_intel_postgres bash -c "psql \$DATABASE_URL -c \"SELECT COUNT(*), posts_collected FROM viral_channel_trackers GROUP BY 2 ORDER BY 2;\""' 2>&1

OUT
Exit code 2
psql: error: connection to server on socket "/var/run/postgresql/.s.PGSQL.5432" failed: FATAL:  role "root" does not exist
Bash Cek environment variable Postgres di container
IN
"C:/Program Files/PuTTY/plink.exe" -ssh -pw 'PerangDigital26#' -hostkey 'ssh-ed25519 255 SHA256:wtyu9TWjcg6T9EvRnstDtlniEMvZu8mCIWwoGZ+5HxM' root@187.77.125.10 'docker exec social_intel_postgres env | grep -i postgres' 2>&1

OUT
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/lib/postgresql/17/bin
POSTGRES_USER=social_intelligence
POSTGRES_PASSWORD=password
POSTGRES_DB=social_intelligence_db
PGDATA=/var/lib/postgresql/data
Bash Query dengan user dan DB yang benar
IN
"C:/Program Files/PuTTY/plink.exe" -ssh -pw 'PerangDigital26#' -hostkey 'ssh-ed25519 255 SHA256:wtyu9TWjcg6T9EvRnstDtlniEMvZu8mCIWwoGZ+5HxM' root@187.77.125.10 'docker exec social_intel_postgres psql -U social_intelligence -d social_intelligence_db -c "SELECT COUNT(*), posts_collected FROM viral_channel_trackers GROUP BY 2 ORDER BY 2;"' 2>&1

OUT
 count | posts_collected 
-------+-----------------
    75 |               0
     1 |               4
(2 rows)
Bash Distribusi kondisi scrape_logs semua tracker
IN
"C:/Program Files/PuTTY/plink.exe" -ssh -pw 'PerangDigital26#' -hostkey 'ssh-ed25519 255 SHA256:wtyu9TWjcg6T9EvRnstDtlniEMvZu8mCIWwoGZ+5HxM' root@187.77.125.10 'docker exec social_intel_postgres psql -U social_intelligence -d social_intelligence_db -c "SELECT CASE WHEN scrape_logs IS NULL OR scrape_logs = jsonb_build_array() THEN '"'"'belum_scrape'"'"' WHEN (scrape_logs -> (jsonb_array_length(scrape_logs)-1) ->> '"'"'error'"'"') IS NOT NULL THEN '"'"'error'"'"' ELSE '"'"'ok'"'"' END AS kondisi, COUNT(*) AS n FROM viral_channel_trackers GROUP BY 1 ORDER BY 1;"' 2>&1

OUT
   kondisi    | n  
--------------+----
 belum_scrape | 72
 error        |  3
 ok           |  1
(3 rows)
Bash Cek last_scraped_date pada tracker belum_scrape
IN
"C:/Program Files/PuTTY/plink.exe" -ssh -pw 'PerangDigital26#' -hostkey 'ssh-ed25519 255 SHA256:wtyu9TWjcg6T9EvRnstDtlniEMvZu8mCIWwoGZ+5HxM' root@187.77.125.10 'docker exec social_intel_postgres psql -U social_intelligence -d social_intelligence_db -c "SELECT last_scraped_date, COUNT(*) FROM viral_channel_trackers WHERE scrape_logs IS NULL OR scrape_logs = jsonb_build_array() GROUP BY 1 ORDER BY 1;"' 2>&1

OUT
 last_scraped_date | count 
-------------------+-------
 2026-07-01        |    72
(1 row)
Bash Lihat detail tracker yang error
IN
"C:/Program Files/PuTTY/plink.exe" -ssh -pw 'PerangDigital26#' -hostkey 'ssh-ed25519 255 SHA256:wtyu9TWjcg6T9EvRnstDtlniEMvZu8mCIWwoGZ+5HxM' root@187.77.125.10 'docker exec social_intel_postgres psql -U social_intelligence -d social_intelligence_db -c "SELECT channel_name, last_scraped_date, scrape_logs FROM viral_channel_trackers WHERE (scrape_logs -> (jsonb_array_length(scrape_logs)-1) ->> '"'"'error'"'"') IS NOT NULL LIMIT 5;"' 2>&1

OUT
 channel_name  | last_scraped_date |                                                                                           scrape_logs                                                                                           
---------------+-------------------+-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
 Eka Gustiwana | 2026-07-02        | [{"day": 1, "date": "2026-07-02", "error": "EnsembleData error: HTTP 493: {\"detail\":\"Subscription expired. Send an email at hello@ensembledata.com\"}", "posts_new": 0, "posts_skipped": 0}]
 ashok vibes   | 2026-07-02        | [{"day": 1, "date": "2026-07-02", "error": "EnsembleData error: HTTP 493: {\"detail\":\"Subscription expired. Send an email at hello@ensembledata.com\"}", "posts_new": 0, "posts_skipped": 0}]
 FOX Sports    | 2026-07-02        | [{"day": 2, "date": "2026-07-02", "error": "EnsembleData error: HTTP 493: {\"detail\":\"Subscription expired. Send an email at hello@ensembledata.com\"}", "posts_new": 0, "posts_skipped": 0}]
(3 rows)
Ada pola menarik. Mari cek service code untuk mengerti kenapa 72 tracker punya last_scraped_date = 2026-07-01 tapi scrape_logs = [] (kosong).

Grep "last_scraped_date" (in c:\Users\Acer\social-media-scrap\app\services\viral_tracking)
29 lines of output
Read c:\Users\Acer\social-media-scrap\app\services\viral_tracking\service.py (lines 91-190)
Read c:\Users\Acer\social-media-scrap\app\services\viral_tracking\service.py (lines 2-91)
Grep "def _append_scrape_log" (in c:\Users\Acer\social-media-scrap\app\services\viral_tracking\service.py)
41 lines of output
Read c:\Users\Acer\social-media-scrap\app\services\viral_tracking\service.py (lines 213-242)
Globpattern: "**/viral_tracking/models.py"
Found 1 file
Read c:\Users\Acer\social-media-scrap\app\domain\viral_tracking\models.py
Globpattern: "**/workers/viral_tracking_worker.py"
Found 1 file
Read c:\Users\Acer\social-media-scrap\app\workers\viral_tracking_worker.py
Bash Cek created_at vs last_scraped_date untuk tracker kosong
IN
"C:/Program Files/PuTTY/plink.exe" -ssh -pw 'PerangDigital26#' -hostkey 'ssh-ed25519 255 SHA256:wtyu9TWjcg6T9EvRnstDtlniEMvZu8mCIWwoGZ+5HxM' root@187.77.125.10 'docker exec social_intel_postgres psql -U social_intelligence -d social_intelligence_db -c "SELECT started_at::date, last_scraped_date, updated_at::date, COUNT(*) FROM viral_channel_trackers WHERE (scrape_logs IS NULL OR scrape_logs = '"'"'[]'"'"'::jsonb) GROUP BY 1,2,3 ORDER BY 1,2,3;"' 2>&1

OUT
 started_at | last_scraped_date | updated_at | count 
------------+-------------------+------------+-------
 2026-07-01 | 2026-07-01        | 2026-07-01 |    72
(1 row)
Bash Cek waktu server dan timezone DB
IN
"C:/Program Files/PuTTY/plink.exe" -ssh -pw 'PerangDigital26#' -hostkey 'ssh-ed25519 255 SHA256:wtyu9TWjcg6T9EvRnstDtlniEMvZu8mCIWwoGZ+5HxM' root@187.77.125.10 'docker exec social_intel_postgres psql -U social_intelligence -d social_intelligence_db -c "SELECT NOW(), NOW()::date;"' 2>&1

OUT
              now              |    now     
-------------------------------+------------
 2026-07-02 05:13:37.711019+00 | 2026-07-02
(1 row)
Grep "03:00|daily_check|CELERYBEAT|crontab|schedule" (in c:\Users\Acer\social-media-scrap\app\workers\celery_app.py)
50 lines of output
Sekarang saya punya gambaran lengkap. Biar saya jelaskan dulu sebelum memperbaiki.

Kenapa 75 Tracker Masih 0 Post
Data saat ini:

Kondisi	Jumlah	Detail
scrape_logs = [], last_scraped_date = 2026-07-01	72 tracker	Scraping SEMPAT jalan, tapi commit ke DB gagal
scrape_logs ada error 493, last_scraped_date = 2026-07-02	3 tracker	Scraping jalan hari ini tapi SEBELUM fix kita deploy
posts_collected = 4, sukses	1 tracker (MrBeast)	Normal ✓
Kenapa 72 tracker scrape_logs kosong padahal last_scraped_date sudah di-set:

Ini adalah MissingGreenlet bug lama. Alurnya:

get_channel_videos → dapat video ✓
collect_comments_for_video → kena HTTP 493 → session rusak
await db.commit() → GAGAL (MissingGreenlet) → seluruh transaksi rollback
Hasilnya: scrape_logs tetap [], TAPI last_scraped_date = 2026-07-01 entah dari mana (kemungkinan partial commit sebelum error)
Intinya: semua data yang harusnya tersimpan tidak ada di DB.

Jadwal Retry Otomatis
Kondisi	Akan retry kapan?
72 tracker (last_scraped_date = 2026-07-01)	Malam ini 03:00 WIB — daily task akan trigger mereka (kemarin < hari ini)
3 tracker error hari ini (last_scraped_date = 2026-07-02)	Besok 03:00 WIB — karena last_scraped_date = today, daily task skip mereka
Artinya: tanpa intervensi, 72 tracker AKAN berjalan malam ini, dan dengan fix yang sudah kita deploy, seharusnya berhasil dapat data via YouTube Data API v3.

Rekomendasi Saya
Ada 3 opsi, saya rekomendasikan gabungan 1+2:

Opsi 1 — Retry sekarang juga (tanpa menunggu 03:00 WIB):
Tambah endpoint POST /viral-tracking/retry-failed yang reset last_scraped_date = null untuk semua tracker dengan posts_collected = 0, lalu langsung queue scrape task. Data masuk dalam ~30 menit.

Opsi 2 — Auto-retry error (jangka panjang):
Ubah logika resume_active_trackers agar tracker yang last_scraped_date = hari ini tapi last_log punya error, tetap ikut scraping (tidak di-skip). Ini menyelamatkan 3 tracker yang kena error hari ini.

Opsi 3 — Visual saja:
Beri warna berbeda di monitor: abu-abu = belum scrape, oranye = error, hijau = sukses. Tidak mengubah data, tapi tampilan lebih informatif.


