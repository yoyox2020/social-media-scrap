# Cara Kerja Scraping Instagram (via Apify)

> **Update 2026-07-03:** Scraping Instagram sudah dipindah dari EnsembleData ke
> Apify. Detail migrasi lengkap: [instagram-apify-migration.md](instagram-apify-migration.md).
> Metode & gotcha Apify: [apify-instagram-method.md](apify-instagram-method.md).
> Tabel sumber topik viral: [trend-recommendations.md](trend-recommendations.md).

Ada 3 jalur untuk scraping Instagram sekarang:

---

## Jalur 1 — Otomatis: Celery Beat jam 09:00 WIB (dari trend_recommendations)

```
Celery Beat 09:00 WIB → instagram_trend_recommendation_daily_task
                             ↓
                    run_daily_trend_scrape()
                        ├── Ambil topik trend_recommendations status='pending'
                        │   yang punya related_account platform='instagram'
                        ├── Urutkan by score tertinggi
                        ├── Ambil maks 3 topik (settings.instagram_trend_daily_budget)
                        └── Per topik:
                              ├── scrape 1 post + 10 komentar + hashtag via Apify
                              ├── analisis sentimen (lexicon, logika sama seperti dulu)
                              ├── catat ke scrape_runs (api_source='apify')
                              └── verifikasi: post berhasil? → status='used'
                                              gagal/0 post?  → tetap 'pending' (coba lagi besok)
```

**Sumber topik & akun** datang dari AI eksternal yang submit ke
`POST /trend-recommendations` (lihat [trend-recommendations.md](trend-recommendations.md)) —
**bukan** hasil discovery internal seperti sebelumnya (fitur cari-akun-viral-sendiri
via hashtag EnsembleData sudah dipensiunkan karena Apify tidak punya fitur itu).

**Kenapa dibatasi 3/hari?** Apify berbayar per run (compute units). Kalau ada
20 topik pending, cuma 3 dengan score tertinggi yang diproses hari itu — sisanya
tetap `pending` dan otomatis masuk antrian lagi besok (tanpa perlu trigger manual).

---

## Jalur 2 — Manual via API (bebas, tanpa batas budget)

### `GET /instagram/posts?username=<username>`
- Scrape kalau belum ada data hari ini (maks 5 post/username/hari)
- Kalau sudah pernah discrape hari ini → langsung baca dari DB, tidak hit Apify lagi

### `POST /instagram/scrape?username=<username>`
- Sama seperti di atas, tapi lewat Celery (background), langsung return 202

Keduanya **tidak kena budget 3/hari** — itu cuma berlaku untuk pipeline otomatis
trend_recommendations di Jalur 1. Manual selalu bisa dipanggil kapan saja.

```
POST /instagram/scrape?username=radityadika
  → Celery worker terima task (workers.instagram.scrape_username)
  → Apify actor: 1 call → post + komentar + hashtag
  → simpan posts + comments + lexicon_analyses ke DB
  → selesai dalam ±30-55 detik (tergantung jumlah komentar)
```

---

## Jalur 3 — Manual trigger batch harian (tanpa nunggu jam 09:00)

```
POST /instagram/trend-scrape/run
  → Celery worker jalankan run_daily_trend_scrape() SEKARANG JUGA
  → Logika sama persis dengan Jalur 1 (tetap ikut budget 3/hari,
    topik yang sudah 'used' hari ini tidak diulang)
```

Berguna untuk testing atau kalau tidak mau menunggu jadwal harian.

---

## Yang TIDAK bisa lagi (sengaja dinonaktifkan)

| Endpoint | Status | Kenapa |
|---|---|---|
| `GET /instagram/search?q=keyword` | **501 (nonaktif)** | Apify tidak punya fitur cari-akun-by-keyword/hashtag. Kalau sudah tahu username-nya, pakai `GET /instagram/posts` langsung |
| `POST /collectors/collect` (platform instagram) | **Tidak didukung** | Sama alasannya — butuh discovery-by-keyword |

Kalau butuh fitur cari-akun-by-keyword lagi di masa depan, perlu cari Apify
Actor lain yang punya fitur discovery, atau aktifkan lagi EnsembleData khusus
untuk fitur ini saja.

---

## Cek hasil

```bash
GET /instagram/trending?recommendation_date=2026-07-03          # topik viral hari ini + hasil scrape
GET /instagram/posts?username=<username>                        # post + komentar + sentimen 1 akun
GET /instagram/comments?username=<username>&sentiment=negatif   # filter komentar
```

Dashboard publik (tanpa login): `http://187.77.125.10:8000/scraping-status`

---

## Hasil uji coba nyata (2026-07-03)

`instagram_trend_recommendation_daily_task` dijalankan dengan 5 topik
trend_recommendations yang sudah ada (budget 3/hari, urut score tertinggi):

| Topik | Hasil | Status akhir |
|---|---|---|
| Bahlil Lahadalia (score 0.9) | ✅ 1 post, 9 komentar (1 positif, 8 netral) | `used` |
| Gibran Rakabuming Raka (0.85) | ✅ 1 post, 9 komentar (semua netral) | `used` |
| Pemadaman Listrik PLN (0.8) | ✅ 1 post, 10 komentar (4 negatif, 6 netral) | `used` |
| Koperasi Merah Putih (0.75) | Di luar budget hari itu | tetap `pending` (coba besok) |
| Piala Dunia 2026 (0.7) | Tidak ada akun Instagram di data | tetap `pending` |

Semua tercatat di `scrape_runs` (`api_source='apify'`, `status='success'`,
durasi 31-54 detik/topik).

**Verifikasi live via HTTP** (pakai token asli, bukan cuma cek database):
- `GET /instagram/trending` → 4 topik tampil, 3 `status:"used"` lengkap dengan
  post+komentar+sentimen, 1 `status:"pending"` dengan `posts: []`.
- `GET /instagram/search?q=test` → `HTTP 501` dengan pesan jelas.

---

## Catatan penting

| Hal | Keterangan |
|---|---|
| **Budget 3/hari** | Bisa diubah via `settings.instagram_trend_daily_budget` di `.env`, tanpa ubah kode |
| **Verifikasi scrape** | Cuma ditandai `used` kalau Apify benar-benar kembalikan ≥1 post. Gagal → tetap `pending`, dicoba lagi otomatis besok |
| **Sentiment** | Tetap pakai lexicon logic yang sama seperti sebelumnya (bukan sentiment bawaan Apify) |
| **Manual endpoint** | `GET /instagram/posts` dan `POST /instagram/scrape` tidak kena budget — bebas kapan saja |
| **Discovery-by-keyword** | Sudah tidak bisa (Apify tidak punya fitur ini) — `GET /instagram/search` dan `POST /collectors/collect` (instagram) dinonaktifkan |
| **Tabel `instagram_trending_accounts`** | Dibiarkan ada (dorman) — belum di-drop, sudah tidak ada kode yang memakainya |
| **Docker rebuild** | Sempat gagal karena masalah jaringan builder di server; `apify-client` untuk sementara di-install manual ke container yang jalan (tidak permanen — hilang kalau container di-recreate tanpa rebuild image yang benar) |
