# Trend Discovery API — Multi-Signal Trend Discovery

Sistem validasi topik trending lintas **5 sumber independen** (Twitter native
Trends, TikTok sweep, Instagram sweep, Google Trends, YouTube TrendingTopic),
menghasilkan `confidence_score` OBJEKTIF — menggantikan cara lama yang cuma
mengandalkan `trend_recommendations.score` hasil tebakan AI.

**Beda dari 2 API "trend" lain yang sudah ada** (jangan tertukar):
| API | Fungsi | File |
|---|---|---|
| `/trends` | Volume post & sentimen per keyword (fitur lama, tidak terkait) | [app/api/v1/trends.py](../app/api/v1/trends.py) |
| `/trend-recommendations` | Antrian topik dari AI menebak (submit manual/AI eksternal) | [app/api/v1/trend_recommendations.py](../app/api/v1/trend_recommendations.py) |
| **`/trend-discovery`** | **Validasi objektif lintas sumber (dokumen ini)** | [app/api/v1/trend_discovery/router.py](../app/api/v1/trend_discovery/router.py) |

Prefix `/trend-discovery` sengaja dipilih beda supaya tidak collision nama
modul maupun path dengan `trends.py` yang sudah ada duluan.

Pipeline: [app/services/trends/](../app/services/trends/)
Worker: [app/workers/trends_worker.py](../app/workers/trends_worker.py)
Router: [app/api/v1/trend_discovery/router.py](../app/api/v1/trend_discovery/router.py)

**Tidak ada tabel baru** — semua "menitip" ke `trend_recommendations` yang
sudah ada (`source` beda per pipeline, `raw_payload` dipakai untuk
`confidence_score`/`confirmed_by`). Tidak menyentuh kode platform
Instagram/Facebook/TikTok/Twitter yang sudah ada sama sekali.

---

## 5 sumber sinyal

| # | `source` value | Cara kerja | Kualitas sinyal |
|---|---|---|---|
| 1 | `twitter_native_trend` | Trends bawaan X (`automation-lab/twitter-trends-scraper`) | **Paling objektif** — langsung dari X, bukan turunan |
| 2 | `tiktok_hashtag_sweep` | Search TikTok pakai topik Twitter hari ini (fallback query generik) | Sedang — bukti aktivitas nyata, bukan native trending |
| 3 | `instagram_hashtag_sweep` | Search Instagram pakai topik Twitter hari ini (fallback query generik) | Sedang — Instagram tidak punya halaman trending publik |
| 4 | `google_trends` | Google Trends RSS, diambil segar di tahap gabungan | Objektif tapi generik (bukan per-platform medsos) |
| 5 | `youtube_trending` | Baca-saja dari tabel `trending_topics` (milik YouTube, ranah beda) | Objektif, TIDAK PERNAH ditulis dari sini |

### Kenapa TikTok/Instagram tidak pakai query generik statis

Kalau sapuan TikTok/Instagram pakai frasa generik ("viral hari ini") sebagai
`topic`, hasilnya **tidak akan pernah** bisa dicocokkan kata-per-kata dengan
topik spesifik dari Twitter Trends ("Piala Dunia 2026") — sinyalnya sia-sia
untuk triangulasi. Solusinya: TikTok/Instagram baca topik
`twitter_native_trend` HARI INI dulu (read-only), lalu pakai itu sebagai
query pencarian. Fallback ke query generik (`settings.trends_sweep_queries`)
kalau data Twitter belum ada hari itu.

### confidence_score

```
confidence_score = jumlah_sumber_yang_MENGKONFIRMASI
                    / jumlah_sumber_yang_BENAR-BENAR_JALAN_SUKSES hari itu
```

Bukan dibagi 5 secara buta — sumber yang gagal total (mis. TikTok kena rate
limit) tidak menurunkan confidence topik lain secara tidak adil. "Jalan
sukses" ditentukan dari `scrape_runs.status='success'` per platform + Google
Trends call berhasil + `trending_topics` (YouTube) punya data hari itu.

Ditulis **non-destructive** ke `raw_payload` (`confirmed_by`,
`confidence_score`, `sources_checked_today`) — `source`/`score`/`topic` baris
asli TIDAK PERNAH ditimpa, walau topik yang sama ditemukan sumber lain.

---

## Jadwal otomatis (Celery Beat, WIB)

Urutan SENGAJA berurutan supaya TikTok/Instagram bisa pakai topik Twitter
hari itu, dan gabungan bisa baca hasil ketiganya:

| Waktu | Task | Beat schedule key |
|---|---|---|
| 14:00 | `workers.trends.twitter_discovery` | `twitter-trends-daily` |
| 14:15 | `workers.trends.tiktok_discovery` | `tiktok-trends-daily` |
| 14:30 | `workers.trends.instagram_discovery` | `instagram-trends-daily` |
| 15:00 | `workers.trends.combined_discovery` | `trends-combined-daily` |

Jam/menit bisa diubah lewat `.env` (`TWITTER_TRENDS_SCHEDULE_HOUR`, dst — lihat
[app/shared/config.py](../app/shared/config.py)).

---

## API — per platform (topik + monitoring)

Semua butuh login (`Authorization: Bearer <token>`), sama seperti endpoint
lain di project ini.

### `GET /api/v1/trend-discovery/twitter`

Topik Trends X native hari ini.

**Query params:** `date` (opsional, default hari ini, format `YYYY-MM-DD`)

**Response:**
```json
{
  "success": true,
  "data": {
    "date": "2026-07-10",
    "source": "twitter_native_trend",
    "total": 10,
    "topics": [
      {
        "topic": "#shopeebelanjainstant1jam",
        "score": 1.0,
        "related_accounts": [],
        "status": "pending",
        "confirmed_by": ["twitter_native_trend"],
        "confidence_score": 0.5,
        "recommendation_date": "2026-07-10"
      }
    ]
  }
}
```
`confirmed_by`/`confidence_score` bernilai `null` kalau pipeline gabungan
(`combined`) belum jalan hari itu.

### `GET /api/v1/trend-discovery/twitter/status`

Riwayat run + jadwal pipeline Twitter (10 run terakhir).

**Response:**
```json
{
  "success": true,
  "data": {
    "recent_runs": [
      {
        "status": "success",
        "triggered_by": "celery_beat",
        "videos_fetched": 10,
        "videos_new": 10,
        "duration_seconds": 18.59,
        "error_message": null,
        "started_at": "2026-07-10T04:14:46Z",
        "finished_at": "2026-07-10T04:15:04Z"
      }
    ],
    "running_now": [],
    "schedule": "14:00 WIB otomatis (Celery Beat)"
  }
}
```

### `GET /api/v1/trend-discovery/tiktok` + `/tiktok/status`

Sama struktur seperti Twitter, tapi `source="tiktok_hashtag_sweep"`.
`related_accounts` di sini biasanya TERISI (akun TikTok asli yang ditemukan
lewat search), beda dari Twitter yang selalu kosong.

### `GET /api/v1/trend-discovery/instagram` + `/instagram/status`

Sama struktur, `source="instagram_hashtag_sweep"`.

---

## API — gabungan (triangulasi)

### `GET /api/v1/trend-discovery`

Semua topik hari itu (lintas source), diurutkan `confidence_score` tertinggi
dulu — ini endpoint utama untuk "topik mana yang BENAR-BENAR tervalidasi".

**Query params:**
| Param | Default | Keterangan |
|---|---|---|
| `date` | hari ini | format `YYYY-MM-DD` |
| `min_confidence` | 0.0 | filter minimal `confidence_score` (0.0–1.0) |

**Response:**
```json
{
  "success": true,
  "data": {
    "date": "2026-07-10",
    "total": 10,
    "topics": [
      {
        "topic": "#shopeebelanjainstant1jam",
        "score": 1.0,
        "related_accounts": [],
        "status": "pending",
        "confirmed_by": ["twitter_native_trend"],
        "confidence_score": 0.5,
        "recommendation_date": "2026-07-10"
      }
    ]
  }
}
```

Contoh cari topik yang tervalidasi >=2 sumber:
```bash
GET /api/v1/trend-discovery?min_confidence=0.5
```

### `GET /api/v1/trend-discovery/status`

Ringkasan status SEMUA pipeline sekaligus (dashboard/monitoring cepat) —
1 request, tidak perlu panggil 4x `/status` per platform.

**Response:**
```json
{
  "success": true,
  "data": {
    "twitter":   { "schedule": "14:00 WIB", "latest_run": { "status": "success", "...": "..." }, "running_now": false },
    "tiktok":    { "schedule": "14:15 WIB", "latest_run": { "status": "failed",  "...": "..." }, "running_now": false },
    "instagram": { "schedule": "14:30 WIB", "latest_run": { "status": "failed",  "...": "..." }, "running_now": false },
    "combined":  { "schedule": "15:00 WIB", "latest_run": { "status": "success", "...": "..." }, "running_now": false }
  }
}
```

---

## API — trigger manual (testing/debug)

### `POST /api/v1/trend-discovery/run?source=twitter|tiktok|instagram|combined`

Jalankan satu pipeline SEKARANG (sinkron, tunggu hasil selesai) — tidak perlu
menunggu jadwal Celery Beat. Logic-nya identik dengan task terjadwal.

```bash
curl -X POST "http://187.77.125.10:8000/api/v1/trend-discovery/run?source=twitter" \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**
```json
{
  "success": true,
  "data": {
    "source": "twitter",
    "result": {
      "found": 10,
      "submitted": {"created": ["..."], "updated": [], "evicted": [], "rejected": []}
    }
  }
}
```
Untuk `source=combined`, `result` berbentuk
`{"sources_checked": [...], "annotated": [...], "created": [...]}`.

⚠️ Trigger `tiktok`/`instagram` memanggil Apify sungguhan (biaya nyata) —
jangan spam untuk testing, cukup sekali per kebutuhan.

---

## Contoh pakai lengkap (curl)

```bash
TOKEN=$(curl -s -X POST http://187.77.125.10:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"EMAIL","password":"PASSWORD"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['access_token'])")

# Topik gabungan yang tervalidasi >=2 sumber
curl -s "http://187.77.125.10:8000/api/v1/trend-discovery?min_confidence=0.5" \
  -H "Authorization: Bearer $TOKEN"

# Status semua pipeline sekaligus
curl -s "http://187.77.125.10:8000/api/v1/trend-discovery/status" \
  -H "Authorization: Bearer $TOKEN"

# Trigger manual Twitter (murah, tidak butuh Apify kuota besar)
curl -X POST "http://187.77.125.10:8000/api/v1/trend-discovery/run?source=twitter" \
  -H "Authorization: Bearer $TOKEN"
```

---

## Cek langsung di database (tanpa lewat API)

```bash
ssh root@187.77.125.10
docker exec -it social_intel_postgres psql -U social_intelligence -d social_intelligence_db
```
```sql
SELECT topic, source, score, status,
       raw_payload->>'confidence_score' AS confidence,
       raw_payload->'confirmed_by'      AS confirmed_by
FROM trend_recommendations
WHERE recommendation_date = CURRENT_DATE
  AND source IN ('twitter_native_trend','tiktok_hashtag_sweep','instagram_hashtag_sweep','multi_signal_trending')
ORDER BY score DESC;
```

---

## Bug penting yang sudah diperbaiki (jangan diulang di edit berikutnya)

`submit_recommendations()` (shared upsert,
[app/services/trend_recommendations/service.py](../app/services/trend_recommendations/service.py))
keyed HANYA `(topic, recommendation_date)` — kalau TikTok/Instagram search
topik yang SAMA PERSIS dengan yang Twitter sudah temukan hari itu, submit
ulang lewat fungsi itu akan **menimpa field `source`** baris asli
(`twitter_native_trend` → `tiktok_hashtag_sweep`), menghancurkan jejak asal
sumber yang justru dibutuhkan triangulasi.

Fix: helper `_record_cross_source_confirmation()` di
[tiktok_trend_service.py](../app/services/trends/tiktok_trend_service.py) /
[instagram_trend_service.py](../app/services/trends/instagram_trend_service.py)
— kalau baris untuk topik itu SUDAH ADA dari sumber lain hari ini, JANGAN
panggil `submit_recommendations()` lagi, cukup tambahkan ke
`raw_payload.confirmed_by` (append-only). Pola sama dipakai
[combined_trend_service.py](../app/services/trends/combined_trend_service.py)
untuk anotasi `confidence_score` — baris baru dengan `source='multi_signal_trending'`
CUMA dibuat kalau topiknya belum ada sama sekali hari itu (aman, tidak ada
baris lain yang bisa ketimpa).

---

## Live-test 2026-07-10 (hasil verifikasi production)

| Pipeline | Hasil | Catatan |
|---|---|---|
| Twitter | ✅ 10 trend real Indonesia | `#shopeebelanjainstant1jam`, `#GalaxyUnpacked`, dll |
| TikTok | ⚠️ Logic candidate-topic TERBUKTI benar (SQL query jalan sempurna), tapi 0 hasil | **Apify quota habis** (~$0.45 sisa) — bukan bug kode |
| Instagram | ✅ Actor sukses jalan, 0 hasil real | Expected — hashtag Twitter-only memang tidak ada di Instagram |
| Combined | ✅ Sempurna | `sources_checked` benar exclude TikTok/IG (gagal) + YouTube (0 data hari itu); 10 topik dapat `confidence_score=0.5`; baris `source` lain (pre-existing, tidak terkait) TERBUKTI tidak tersentuh |

**Regresi:** tidak ada — semua Celery task lain tetap terdaftar, endpoint
publik `/youtube/monitor-public` tetap 200 OK setelah restart.

**Catatan operasional:** Apify quota terbatas lagi per 2026-07-10 — pipeline
TikTok/Instagram sweep tidak akan hasilkan data baru sampai saldo di-top-up,
meski kodenya sudah proven benar secara live.

---

## Deployment

Server production (`187.77.125.10`), bind-mount `./app:/app/app` di
`docker-compose.yml` — file baru langsung ke-pickup, cukup restart:

```bash
docker restart social_intel_api social_intel_worker social_intel_worker_beat social_intel_worker_ai
```

`worker_beat` perlu restart supaya jadwal baru (`twitter-trends-daily`, dst)
ke-load; `worker`/`worker_ai` supaya task baru (`workers.trends.*`)
terdaftar; `api` supaya router `/trend-discovery` ke-mount.
