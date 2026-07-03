# Migrasi Instagram: EnsembleData → Apify

Dokumen ini mencatat migrasi scraping Instagram dari EnsembleData ke Apify
(2026-07-03), termasuk alasan, arsitektur baru, endpoint yang berubah, dan
hasil uji coba end-to-end di production.

Terkait: [apify-instagram-method.md](apify-instagram-method.md) (metode & gotcha Apify),
[trend-recommendations.md](trend-recommendations.md) (tabel sumber topik viral).

---

## Kenapa migrasi

EnsembleData subscription expired (error 493) sejak 2026-07-02/03, memblokir
semua scraping Instagram (tidak ada fallback saat itu — lihat analisa di
riwayat kerja). Apify sudah terbukti berhasil scrape Instagram/Facebook/TikTok
lengkap dengan sentiment analysis selama outage tersebut, sehingga dipilih
sebagai pengganti permanen untuk Instagram.

## Aturan bisnis budget harian

Karena Apify berbayar per run, scraping otomatis dari `trend_recommendations`
dibatasi:

- **Maksimal 3 topik/hari** (`settings.instagram_trend_daily_budget`), diambil
  dari topik `status='pending'` yang punya `related_accounts` platform
  `instagram`, urut **score tertinggi**.
- **1 post per topik** (`settings.instagram_trend_posts_per_topic`), dengan
  komentar + hashtag + sentimen (`settings.instagram_trend_comments_per_post`, default 10).
- **Verifikasi hasil**: kalau Apify berhasil kembalikan ≥1 post → `status='used'`
  + dicatat ke tabel `scrape_runs` (`api_source='apify'`). Kalau gagal/0 post →
  tetap `pending`, otomatis dicoba lagi besok (budget hari itu hangus untuk topik itu, tidak diulang paksa).
- **Scraping manual (by username, bukan dari trend_recommendations) tidak kena budget ini** —
  `GET /instagram/posts` dan `POST /instagram/scrape` tetap bebas jalan kapan saja.

Semua angka ini bisa diubah lewat `.env` / `app/shared/config.py` tanpa ubah kode:
```
apify_api_token=...
apify_actor_id=ycQuEFDDZmgX7BAsL
instagram_trend_daily_budget=3
instagram_trend_posts_per_topic=1
instagram_trend_comments_per_post=10
```

## Apa yang berubah

### Kode baru
| File | Fungsi |
|---|---|
| `app/integrations/apify/instagram.py` | Wrapper `ApifyClient`, panggil Actor secara async (`asyncio.to_thread`) |
| `app/services/instagram/pipeline_service.py` | Ditulis ulang total — parsing hasil Apify (post+comment per baris → di-group per post), dedup post via shortcode, dedup komentar via hash (Apify tidak kasih comment_id stabil), ekstrak hashtag dari caption. **Sentiment tetap pakai lexicon logic lama** (`app.ai.lexicon.service.analyze`), bukan sentiment bawaan Apify |
| `app/services/instagram_trending/trend_scrape_service.py` | Service baru: `run_daily_trend_scrape()` — logika budget harian di atas |
| `app/workers/instagram_trending_worker.py` | Task lama (`instagram_trending_daily_task`, `instagram_trending_scrape_account_task`) diganti `workers.instagram_trend_recommendation.daily`. Task manual `workers.instagram.scrape_username` tetap ada |

### Dihapus (retired)
- `app/services/instagram_trending/service.py`, `scorer.py`, `providers/` — pipeline discovery-by-hashtag lama (cari akun viral sendiri via EnsembleData). **Digantikan** oleh `trend_recommendations` (AI eksternal yang submit topik+akun, lihat [trend-recommendations.md](trend-recommendations.md)) — Apify tidak punya fitur cari-by-hashtag jadi discovery internal ini tidak bisa dipertahankan.
- `app/integrations/instagram/connector.py` — connector EnsembleData Instagram, sudah tidak dipakai sama sekali.
- `"instagram"` dihapus dari `SUPPORTED_COLLECTION_PLATFORMS` (`app/integrations/ensemble_data/endpoints.py`) dan dari `collector_default_platforms` — lihat alasan di bawah.

### Endpoint yang berubah
| Endpoint | Perubahan |
|---|---|
| `GET /instagram/trending` | Sekarang baca dari `trend_recommendations` (bukan tabel `instagram_trending_accounts` lama) — tampilkan topik + status (`pending`/`used`) + post/komentar/sentimen kalau sudah discrape |
| `GET /instagram/search` | **Dinonaktifkan (HTTP 501)** — Apify tidak punya fitur cari-akun-by-keyword/hashtag, EnsembleData yang punya fitur ini sudah dilepas |
| `POST /instagram/trending/{username}/scrape` | Dihapus, diganti `POST /instagram/trend-scrape/run` (trigger manual batch harian, tetap ikut budget) |
| `POST /collectors/collect` (platform `instagram`) | Sudah tidak didukung (schema validation menolak dengan pesan jelas) — alasan sama seperti `/search`, jalur ini juga butuh discovery-by-keyword |
| `GET /instagram/posts`, `POST /instagram/scrape` | Tidak berubah secara API, tapi sekarang Apify di baliknya (bukan EnsembleData). Tetap manual/bebas budget |

### Tidak dihapus (sengaja dibiarkan, keputusan produksi)
Tabel `instagram_trending_accounts` (dan model-nya) **tidak di-drop** — datanya
tetap ada, tapi sudah tidak ada kode yang menulis/membaca dari situ lagi
(dorman). Alasan: drop tabel adalah tindakan destruktif di production yang
butuh konfirmasi eksplisit terpisah, belum diminta.

## Hasil uji coba end-to-end (2026-07-03)

Dijalankan langsung di production pakai 5 topik `trend_recommendations` yang
sudah ada (submission sebelumnya: Bahlil Lahadalia, Gibran Rakabuming Raka,
Pemadaman Listrik PLN, Koperasi Merah Putih, Piala Dunia 2026 — 4 di antaranya
punya akun Instagram).

**Batch harian (`run_daily_trend_scrape`)** — budget 3, urut score tertinggi:

| Topik | Username IG | Hasil | Status akhir |
|---|---|---|---|
| Bahlil Lahadalia (0.9) | `bahlillahadalia` | ✅ 1 post, 9 komentar (1 positif, 8 netral) | `used` |
| Gibran Rakabuming Raka (0.85) | `gibran_rakabuming` | ✅ 1 post, 9 komentar (semua netral) | `used` |
| Pemadaman Listrik PLN (0.8) | `pln123_official` | ✅ 1 post, 10 komentar (4 negatif, 6 netral) | `used` |
| Koperasi Merah Putih (0.75) | `kemenkop` | Tidak diproses (di luar budget 3) | tetap `pending` |
| Piala Dunia 2026 (0.7) | — (tidak ada akun IG) | Dilewati (tidak ada related_account instagram) | tetap `pending` |

Semua tercatat di `scrape_runs` dengan `api_source='apify'`, `status='success'`,
durasi 31-54 detik per topik.

**Verifikasi live via HTTP (dengan token asli, bukan cuma cek DB):**
- `GET /instagram/trending` → 4 topik tampil, 3 `status:"used"` lengkap dengan
  post+komentar+sentimen, 1 `status:"pending"` dengan `posts: []`.
- `GET /instagram/search?q=test` → `HTTP 501` dengan pesan jelas.

## Kebutuhan lain / follow-up yang belum diputuskan

1. **`instagram_trending_accounts`** — tabel dorman, tunggu keputusan apakah perlu di-drop.
2. **Docker image rebuild gagal** karena `Network is unreachable` ke `files.pythonhosted.org`
   saat `docker compose build` di server (masalah jaringan/DNS builder, bukan dari kode ini).
   Workaround sementara: `apify-client` di-`pip install` langsung ke container yang jalan
   (`social_intel_api`, `social_intel_worker`, `social_intel_worker_beat`). **Ini tidak permanen** —
   kalau container di-recreate tanpa rebuild image yang benar, dependency ini akan hilang lagi.
   Perlu investigasi masalah jaringan Docker di server terpisah dari task ini.
3. **`GET /instagram/search` dan `POST /collectors/collect` (instagram)** — didisable, bukan
   dihapus total. Kalau nanti butuh fitur "cari akun by keyword" lagi, perlu cari Apify Actor
   lain yang punya fitur discovery, atau kembalikan EnsembleData khusus untuk fitur ini saja.
