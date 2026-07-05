# Dokumentasi: Provider Abstraction (Apify ↔ EnsembleData) + Viral Discovery Otomatis

Dokumentasi ini mencakup 2 fitur yang dibangun dalam satu sesi (04-05 Juli 2026),
sesuai permintaan:

1. **Provider abstraction untuk pencarian Instagram** — auto-switch antar
   provider third-party (Apify/EnsembleData), kuota harian, dan kemampuan
   ganti provider ke depan tanpa ubah kode.
2. **Viral discovery otomatis harian** — AI cari topik+akun Instagram yang
   viral hari itu (berita + Instagram publik), simpan ke `trend_recommendations`.

> **Catatan penting:** `trend_recommendations` (model, `submit_recommendations()`,
> endpoint publik `POST/GET /trend-recommendations`, `run_daily_trend_scrape()`)
> **dibekukan** — kedua fitur di bawah menulis/membaca situ lewat fungsi yang
> sudah ada, tidak pernah mengubah logic intinya. Lihat `docs/trend-recommendations.md`
> untuk dokumentasi fitur inti itu.

---

## Permintaan 1 — Provider Abstraction Pencarian Instagram

### Apa itu "pencarian" di sini

Bukan discovery-by-hashtag/keyword (Apify tidak punya fitur itu sama sekali).
"Pencarian" = **cari & scrape sebuah profil Instagram berdasarkan username**
— persis yang dilakukan Actor Apify `ycQuEFDDZmgX7BAsL`
("social-media-sentiment-analysis-tool": kasih nama profil, dia cari &
scrape profil itu di FB/IG/TikTok sekaligus, plus komentar + sentimen).

### Struktur folder & file

```
app/services/instagram/
├── pipeline_service.py          (sudah ada, sekarang pakai provider abstraction)
├── quota_service.py             (BARU — kuota harian bersama)
└── providers/                   (BARU — seluruh folder)
    ├── __init__.py
    ├── base.py                  (interface: BaseInstagramSearchProvider)
    ├── apify_provider.py        (wrapper Apify — provider utama)
    ├── ensemble_data_provider.py (wrapper EnsembleData — provider fallback)
    └── registry.py               (daftar provider + fungsi fallback otomatis)
```

### Cara kerja

```
pipeline_service.scrape_instagram_posts(username, ...)
        │
        ▼
registry.search_profile_with_fallback(username, max_posts, max_comments)
        │
        │  baca settings.instagram_search_provider_order
        │  default: "apify,ensembledata"
        │
        ├─► coba provider #1 (Apify)
        │     berhasil? → return (rows, "apify")
        │     gagal?    → log warning, lanjut ke provider berikutnya
        │
        └─► coba provider #2 (EnsembleData)
              berhasil? → return (rows, "ensembledata")
              gagal?    → semua provider habis → raise error terakhir
```

**Semua provider mengembalikan data dalam bentuk yang SAMA** (bentuk asli
output Apify: `postUrl`, `postDescription`, `postLikesCount`, `commentText`,
dst) — jadi `pipeline_service.py` (yang menyimpan ke DB + analisis sentimen)
**tidak perlu tahu provider mana yang sebenarnya dipakai**. Ini kunci
abstraksinya:

| File | Isi |
|---|---|
| `providers/base.py` | `BaseInstagramSearchProvider(ABC)` — kontrak: method `search_profile(username, max_posts, max_comments) -> list[dict]` |
| `providers/apify_provider.py` | Wrapper tipis — Apify sudah menghasilkan bentuk data yang jadi "standar", jadi tidak perlu transformasi |
| `providers/ensemble_data_provider.py` | Panggil 3 endpoint EnsembleData (`user/info` → `user/posts` → `post/comments`), **transformasi manual** ke bentuk standar Apify |
| `providers/registry.py` | `PROVIDERS` dict (nama → class) + `search_profile_with_fallback()` — loop coba tiap provider sesuai urutan config |

### Cara ganti/atur provider (sesuai permintaan "tanpa ubah kode")

Di `.env` server:
```bash
# Urutan fallback — provider pertama dicoba duluan
INSTAGRAM_SEARCH_PROVIDER_ORDER=apify,ensembledata

# Cuma pakai satu provider (matikan yang lain)
INSTAGRAM_SEARCH_PROVIDER_ORDER=apify

# Balik urutan (EnsembleData duluan)
INSTAGRAM_SEARCH_PROVIDER_ORDER=ensembledata,apify
```
Ganti urutan/nonaktifkan provider yang **sudah terdaftar** = murni ubah baris
di atas + restart container supaya proses baca ulang `.env`.

**Batasan jujur:** kalau nanti mau tambah provider yang **benar-benar baru**
(belum pernah ada, misal Actor Apify lain atau API pihak ketiga lain), tetap
perlu:
1. Buat 1 file baru `providers/nama_provider_baru.py` — implement
   `BaseInstagramSearchProvider`, isi method `search_profile()`.
2. Tambah 1 baris di `PROVIDERS` dict (`providers/registry.py`).

Ini **tidak bisa dihindari** — kode yang benar-benar tahu cara bicara ke API
baru itu harus ditulis sekali. Yang dijamin "tanpa ubah kode" adalah
**reorder/nonaktifkan provider yang sudah ada**, bukan menambah yang sama
sekali baru dari udara.

### Kuota harian search ad-hoc ("minimal 3, sisa dipakai di API Instagram lain")

**File:** `app/services/instagram/quota_service.py`

Tidak ada tabel counter baru — dihitung ulang tiap kali dari tabel
`scrape_runs` yang sudah ada (pola yang sama dengan `ig_scraped_today` di
dashboard). **Ini kuota terpisah** dari budget harian `trend_recommendations`
di bawah — dibedakan lewat `triggered_by` (`manual_api`/`manual_cli` di sini,
`celery_beat` untuk pipeline trend).

```python
# Config (app/shared/config.py / .env)
instagram_search_daily_min: int = 3       # minimal panggilan search dijamin/hari
instagram_shared_daily_budget: int = 10   # total kuota harian (search + API lain)
```

Logika `enforce_quota(db, operation="search")`:
1. Hitung berapa panggilan **search** hari ini (`keyword_text LIKE 'search:%'`)
   dan total panggilan Instagram ad-hoc hari ini (`triggered_by IN
   ('manual_api','manual_cli')`, TIDAK `'celery_beat'`).
2. Kalau search belum sampai **3** hari itu → selalu boleh (floor terjamin).
3. Kalau sudah lewat 3 tapi total belum sampai **10** → masih boleh (pakai
   sisa kuota bersama).
4. Kalau sudah 10 → ditolak (`ExternalAPIError`).

Dipanggil di 2 titik pemicu manual: `GET /instagram/posts` dan
`POST /instagram/scrape` (`instagram_scrape_username_task`). Pipeline harian
`trend_recommendations` (Subsistem B di bawah) **tidak kena kuota ini sama
sekali** — dia punya budget & logika seleksi sendiri.

### Bukti kerja nyata (diverifikasi 04-05 Juli 2026)

```bash
docker exec social_intel_api python -c "
import asyncio
from app.services.instagram.providers.registry import search_profile_with_fallback
async def main():
    rows, provider = await search_profile_with_fallback('bahlillahadalia', 1, 3)
    print('provider_used:', provider, '| rows:', len(rows))
asyncio.run(main())
"
# Output: provider_used: apify | rows: 3
```

**Catatan status EnsembleData:** subscription EnsembleData sedang **expired**
(HTTP 493) per pengecekan terakhir — provider `ensembledata` akan selalu
gagal cepat sampai subscription diperbarui. Ini bukan bug; itu justru
membuktikan mekanisme fallback bekerja (kalau Apify juga gagal, sistem akan
otomatis coba EnsembleData, dan baru benar-benar gagal kalau keduanya mati).

---

## Permintaan 2 — Viral Discovery Otomatis Harian

### Apa yang dikerjakan

Task terjadwal baru (Subsistem A) yang **setiap hari** (jam diatur via `.env`,
default 07:00 WIB) menyuruh AI (Claude/OpenAI/Ollama, pilih via `.env`)
mencari topik/isu yang **benar-benar viral hari itu** — bukan satu keyword
tertentu, tapi sapuan terbuka lintas berita Indonesia + Instagram publik
(politik, hiburan, olahraga, produk viral, dll). Untuk tiap topik, WAJIB ada
akun Instagram nyata yang terkait — kalau tidak ketemu akunnya, topik itu
tidak disertakan.

Hasilnya masuk ke `trend_recommendations` (status `pending`) lewat fungsi
frozen `submit_recommendations()`, lalu dikonsumsi oleh pipeline scrape
Instagram yang **sudah ada** (Subsistem B, jam diatur via `.env`, default
09:00 WIB — 2 jam setelah A, supaya topik baru berpeluang langsung discrape
di hari yang sama).

### Struktur folder & file

```
app/ai/llm/
└── viral_discovery_service.py        (BARU — otak AI-nya, provider Claude/OpenAI/Ollama)

app/services/trend_recommendations/
└── viral_discovery_scrape_service.py (BARU — orkestrasi, TERPISAH dari
                                        trend_scrape_service.py yang dibekukan)

app/workers/
├── viral_discovery_worker.py         (BARU — task Celery Subsistem A)
└── celery_app.py                     (diubah — +1 include, +2 beat schedule, jam via .env)
```

### Cara kerja — dua subsistem berantai, bukan satu sistem monolitik

```
[Subsistem A — default 07:00 WIB, jam diatur via .env]
Celery Beat → workers.viral_discovery.daily_scan
        │
        ▼
viral_discovery_scrape_service.run_daily_viral_discovery(db)
        │
        ├─ 1. Catat 1 baris ScrapeRun (status='running') — "bukti pencarian hari ini"
        │
        ├─ 2. Panggil viral_discovery_service.find_daily_viral_topics()
        │       │
        │       ├─ provider dari AI_DISCOVERY_PROVIDER (anthropic/openai/ollama)
        │       ├─ hanya "anthropic" (Claude) yang punya web_search bawaan —
        │       │   openai/ollama function-calling saja, TIDAK bisa browsing
        │       ├─ System prompt: "cari topik viral HARI INI, berita + IG
        │       │   publik Indonesia, WAJIB ada akun instagram nyata,
        │       │   maks viral_discovery_max_topics topik, jangan mengarang"
        │       └─ tool submit_trend_topics(items=[...])
        │           → return list [{topic, score, related_accounts}, ...]
        │
        ├─ 3. Kalau ada hasil: panggil submit_recommendations() — FUNGSI
        │       FROZEN, dipanggil apa adanya (sama seperti yang dipakai
        │       POST /trend-recommendations publik)
        │       → topik masuk trend_recommendations, status='pending',
        │         source='ai_viral_discovery'
        │
        └─ 4. Update ScrapeRun: status=success/failed, videos_fetched=jumlah
               topik ditemukan, videos_new=jumlah yang benar-benar 'created'

                    ↓ (topik menunggu di tabel trend_recommendations)

[Subsistem B — default 09:00 WIB, jam diatur via .env, SUDAH ADA sebelum sesi ini, TIDAK DIUBAH]
run_daily_trend_scrape() — app/services/instagram_trending/trend_scrape_service.py
        │
        ├─ Ambil SEMUA TrendRecommendation dengan status='pending'
        │   (lintas tanggal — bukan cuma hari ini), urutkan SCORE TERTINGGI
        │   dulu (bukan berdasarkan terbaru/tanggal submit)
        ├─ Filter yang punya akun Instagram valid
        ├─ Ambil sejumlah `instagram_trend_daily_budget` (skrng 5) teratas
        │   dari hasil filter+sort itu — SATU pool tunggal, tidak ada split
        │   "3 terbaru + 3 pending lama"
        │
        └─ Untuk tiap topik terpilih: scrape via provider abstraction (Apify,
           fallback EnsembleData)
              berhasil (>=1 post)? → topic.status = 'used'
              gagal (exception/0 post)? → ScrapeRun dicatat status='failed',
                TAPI topic.status TETAP 'pending' (tidak ada flag "gagal"
                terpisah di topiknya) → otomatis ikut diranking ulang lagi
                besok kalau scorenya masih masuk top-budget
```

**Poin penting yang sering disalahpahami:**
- Tidak ada pembagian eksplisit "3 topik terbaru + 3 topik pending lama, sisanya masuk antrian berikutnya". Seleksinya murni **satu ranking by score**, dipotong di angka budget. Topik pending yang tidak kebagian (score lebih rendah) otomatis "menunggu giliran" hanya karena dia tetap `status='pending'` dan ikut di-query ulang besok — bukan karena ada struktur antrian terpisah.
- Tidak ada kolom/flag "failed" di tabel `trend_recommendations` sendiri. Kegagalan hanya tercatat di baris `ScrapeRun` (log per-attempt); topiknya sendiri tetap `pending` sampai suatu saat berhasil di-scrape (jadi `used`) atau tergeser keluar dari `trend_recommendations` lewat mekanisme eviction `submit_recommendations()` (skor terendah tergusur kalau `MAX_PER_DAY=20` per tanggal penuh dan ada topik baru dengan skor lebih tinggi).

Keduanya cuma terhubung lewat tabel `trend_recommendations` — A menulis, B
membaca. Tidak ada pemanggilan langsung antar keduanya, jadi masing-masing
bisa gagal sendiri-sendiri tanpa menjatuhkan yang lain (persis yang kelihatan
di dashboard: A gagal karena saldo Anthropic habis, B tetap jalan normal
pakai data lama).

### Jadwal Celery Beat — bisa diatur via `.env` (ditambahkan 05 Juli 2026)

Awalnya jam eksekusi (07:00 dan 09:00 WIB) di-hardcode sebagai `crontab()` di
`celery_app.py`. Sekarang dibaca dari config, default tetap sama seperti
sebelumnya:

```bash
# .env
VIRAL_DISCOVERY_SCHEDULE_HOUR=7
VIRAL_DISCOVERY_SCHEDULE_MINUTE=0
INSTAGRAM_TREND_SCRAPE_SCHEDULE_HOUR=9
INSTAGRAM_TREND_SCRAPE_SCHEDULE_MINUTE=0
```

Ganti jam = ubah 4 baris ini di `.env`, lalu `docker compose up -d
--force-recreate --no-deps api worker worker-beat worker-ai` (perlu
`--force-recreate`, bukan `restart` biasa, karena env var baru) supaya
proses baca ulang `.env`; kemudian install ulang paket yang ikut terhapus
(`pip install apify-client anthropic openai` di tiap container yang
di-recreate — gotcha bawaan server ini, `docker compose build` gagal karena
`Network is unreachable` ke PyPI).

### Konfigurasi lengkap

```python
# app/shared/config.py
ai_discovery_provider: str = "anthropic"     # anthropic | openai | ollama
anthropic_api_key: str = ""
anthropic_model: str = "claude-opus-4-8"
openai_api_key: str = ""
openai_model: str = "gpt-4o"
viral_discovery_max_topics: int = 10         # batas topik/hari, sengaja di
                                              # bawah MAX_PER_DAY=20 (batas
                                              # trend_recommendations) supaya
                                              # tidak memonopoli slot harian

instagram_trend_daily_budget: int = 5        # maks topik discrape/hari (Subsistem B)
instagram_trend_posts_per_topic: int = 1
instagram_trend_comments_per_post: int = 10

viral_discovery_schedule_hour: int = 7
viral_discovery_schedule_minute: int = 0
instagram_trend_scrape_schedule_hour: int = 9
instagram_trend_scrape_schedule_minute: int = 0
```

**Catatan provider AI:** ganti `AI_DISCOVERY_PROVIDER` di `.env` = ganti
manual saja, **tidak ada auto-fallback** antar provider AI (beda dengan
provider scraping Instagram di atas yang auto-fallback). Kalau provider
gagal (mis. saldo habis), run hari itu gagal — bukan otomatis coba provider
lain. Hanya "anthropic" yang genuinely bisa browsing web hari ini; openai/
ollama function-calling saja (hasil bisa basi, dari pengetahuan training).

### Cara pantau "bukti status pencarian tiap hari"

1. **Dashboard publik**: `http://187.77.125.10:8000/scraping-status`
   → section "Instagram Trend-Scrape" — kartu **"Dari AI Viral Discovery"**
   (jumlah topik pending dari sumber ini), tabel **"Riwayat Scrape
   Instagram"** dengan kolom **"Sumber"** (pill "AI Discovery" untuk baris
   dari `anthropic_web_search`), dan diagram **"Alur Pipeline Live"** —
   animasi titik-titik menunjukkan tepat di subsistem mana batch hari itu
   berhenti (real, berbasis korelasi run AI terakhir → topik yang dihasilkan
   → status scrape tiap topik itu, bukan status independen tiap subsistem).

2. **API (butuh login)**: `GET /api/v1/instagram/trend-scrape/status`
   ```json
   {
     "summary": {
       "ai_viral_discovery_pending": 3,
       ...
     },
     "recent_runs": [
       {
         "topic": "ai_viral_discovery",
         "api_source": "anthropic_web_search",
         "status": "success",
         "videos_fetched": 5,
         "videos_new": 4,
         "started_at": "2026-07-06T00:00:12+00:00"
       }
     ],
     "viral_discovery_trace": { "ai_run": {...}, "topics": [...] }
   }
   ```

3. **Trigger manual** (tanpa nunggu jadwal, untuk testing):
   ```bash
   docker exec social_intel_worker python -c "
   from app.workers.viral_discovery_worker import viral_discovery_daily_task
   print(viral_discovery_daily_task.delay().id)
   "
   ```

### Status verifikasi (05 Juli 2026)

✅ Seluruh pipeline **terbukti berjalan benar secara teknis** — ScrapeRun
tercatat, error ditangani dengan baik, task selesai tanpa crash. Jadwal
07:00/09:00 WIB terbukti live dibaca dari `.env` (dicek langsung lewat
`printenv` di dalam container dan `celery_app.conf.beat_schedule`).

❌ **Belum bisa dibuktikan menghasilkan topik nyata** — satu-satunya test run
gagal karena **saldo API Anthropic habis** ("Your credit balance is too low
to access the Anthropic API"). Ini murni masalah billing eksternal, bukan
bug kode. Begitu saldo di-top-up di [console Anthropic](https://console.anthropic.com/settings/billing),
fitur ini otomatis jalan penuh — baik lewat jadwal terjadwal maupun trigger
manual di atas.

---

## Ringkasan Perubahan File

| File | Status | Keterangan |
|---|---|---|
| `app/shared/config.py` | diubah | provider order, kuota, `viral_discovery_max_topics`, jadwal beat |
| `app/services/instagram/providers/*.py` | **baru** | abstraksi provider (5 file) |
| `app/services/instagram/quota_service.py` | **baru** | kuota harian search ad-hoc |
| `app/services/instagram/pipeline_service.py` | diubah | 1 baris: pakai provider abstraction, bukan Apify langsung |
| `app/api/v1/instagram/router.py` | diubah | `GET /instagram/posts`: wire quota check + catat ScrapeRun |
| `app/workers/instagram_trending_worker.py` | diubah | `scrape_username_task`: wire quota check + catat ScrapeRun |
| `app/ai/llm/viral_discovery_service.py` | **baru** | otak AI viral discovery (multi-provider) |
| `app/services/trend_recommendations/viral_discovery_scrape_service.py` | **baru** | orkestrasi (terpisah dari file frozen) |
| `app/workers/viral_discovery_worker.py` | **baru** | task Celery Subsistem A |
| `app/workers/celery_app.py` | diubah | +1 worker module, +2 beat schedule, jam via config/.env |
| `app/services/instagram_trending/trend_scrape_service.py` | diubah | HANYA `get_trend_scrape_summary()` (fungsi baca-saja) ditambah field baru + `viral_discovery_trace` |
| `app/main.py` | diubah | dashboard: kartu, kolom, dan diagram alur pipeline live untuk AI Viral Discovery |

**Tidak diubah sama sekali** (frozen, sesuai instruksi): `app/domain/trend_recommendations/*`,
`app/services/trend_recommendations/service.py`, `app/api/v1/trend_recommendations.py`,
fungsi `run_daily_trend_scrape()` di dalam `trend_scrape_service.py` (hanya
fungsi baca `get_trend_scrape_summary()` di file yang sama yang ditambah).

Semua perubahan sudah di-commit ke git dan di-push ke GitHub, serta di-deploy
+ diverifikasi jalan di server production (187.77.125.10).

---

## Inti Desain — Ringkasan

1. **Dua subsistem berantai, bukan satu sistem monolitik.** A (AI discovery)
   menulis ke `trend_recommendations`; B (scrape worker) membaca dari situ.
   Tidak ada pemanggilan langsung antar keduanya — masing-masing bisa gagal
   sendiri tanpa menjatuhkan yang lain.
2. **Semua panggilan ke pihak ketiga dibungkus jadi "provider yang bisa
   ditukar"** — pola yang sama (registry dict + fallback/pilihan berurutan,
   config-driven) dipakai di 2 tempat: scrape Instagram (Apify → EnsembleData,
   auto-fallback) dan AI discovery (Claude/OpenAI/Ollama, pilih manual via
   `AI_DISCOVERY_PROVIDER`, tidak auto-fallback).
3. **Kuota, budget, dan jadwal semuanya di config, bukan hardcode** —
   `instagram_trend_daily_budget`, `instagram_search_daily_min`,
   `instagram_shared_daily_budget`, `viral_discovery_max_topics`, dan jam
   Celery Beat (`*_schedule_hour`/`*_schedule_minute`) semua bisa diubah
   lewat `.env` tanpa ubah kode.
4. **Monitoring melacak batch nyata, bukan status independen** — dashboard
   `/scraping-status` mengambil 1 run AI terakhir, cari topik persis yang
   dihasilkan run itu, lalu cek status scrape masing-masing topik itu,
   sehingga diagram alur menunjukkan tepat di mana proses berhenti untuk
   batch itu.
5. **Satu aturan yang mengikat semuanya**: `trend_recommendations` (tabel,
   endpoint publik, `submit_recommendations()`, `run_daily_trend_scrape()`)
   tidak pernah diubah — semua fitur baru cuma menambah cara baru untuk
   MENULIS ke situ (AI discovery) atau MEMBACA statusnya (monitoring), tidak
   pernah menyentuh logic intinya.
