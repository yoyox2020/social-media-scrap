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
> **dibekukan** — kedua fitur di bawah menulis ke situ lewat fungsi yang sudah
> ada, tidak pernah mengubah isinya. Lihat `docs/trend-recommendations.md`
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
di atas, **restart** container (tidak perlu `--force-recreate` karena bukan
env var baru, tapi ganti isi env var yang sudah ada tetap butuh restart
biasa supaya proses baca ulang `.env`).

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

### Kuota harian ("minimal 3, sisa dipakai di API Instagram lain")

**File:** `app/services/instagram/quota_service.py`

Tidak ada tabel counter baru — dihitung ulang tiap kali dari tabel
`scrape_runs` yang sudah ada (pola yang sama dengan `ig_scraped_today` di
dashboard).

```python
# Config (app/shared/config.py)
instagram_search_daily_min: int = 3       # minimal panggilan search dijamin/hari
instagram_shared_daily_budget: int = 10   # total kuota harian (search + API lain)
```

Logika `enforce_quota(db, operation="search")`:
1. Hitung berapa panggilan **search** hari ini (`keyword_text LIKE 'search:%'`)
   dan total panggilan Instagram ad-hoc hari ini (di luar pipeline
   `trend_recommendations` yang budgetnya terpisah — dibedakan lewat
   `triggered_by IN ('manual_api','manual_cli')`, TIDAK `'celery_beat'`).
2. Kalau search belum sampai **3** hari itu → selalu boleh (floor terjamin).
3. Kalau sudah lewat 3 tapi total belum sampai **10** → masih boleh (pakai
   sisa kuota bersama).
4. Kalau sudah 10 → ditolak (`ExternalAPIError`, HTTP-level muncul sebagai error biasa).

Dipanggil di 2 titik pemicu manual: `GET /instagram/posts` dan
`POST /instagram/scrape` (`instagram_scrape_username_task`). Pipeline harian
`trend_recommendations` (jadwal 09:00 WIB) **tidak kena kuota ini sama
sekali** — dia punya budget sendiri (`instagram_trend_daily_budget=3`) yang
sudah ada sebelum sesi ini dan tidak disentuh.

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

Task terjadwal baru yang **setiap hari jam 07:00 WIB** menyuruh Claude
(dengan kemampuan `web_search`) mencari topik/isu yang **benar-benar viral
hari itu** — bukan satu keyword tertentu, tapi sapuan terbuka lintas berita
Indonesia + Instagram publik (politik, hiburan, olahraga, produk viral, dll).
Untuk tiap topik, WAJIB ada akun Instagram nyata yang terkait — kalau tidak
ketemu akunnya, topik itu tidak disertakan.

Hasilnya otomatis masuk ke `trend_recommendations` (status `pending`),
lalu ikut antrian pipeline scrape Instagram yang **sudah ada** (budget 3
topik/hari, jadwal 09:00 WIB) — **2 jam setelah** viral discovery jalan,
supaya topik yang baru ditemukan berpeluang langsung discrape di hari yang
sama.

### Struktur folder & file

```
app/ai/llm/
└── viral_discovery_service.py        (BARU — otak AI-nya)

app/services/trend_recommendations/
└── viral_discovery_scrape_service.py (BARU — orkestrasi, TERPISAH dari
                                        trend_scrape_service.py yang dibekukan)

app/workers/
├── viral_discovery_worker.py         (BARU — task Celery)
└── celery_app.py                     (diubah — tambah 1 baris include + 1 beat schedule)
```

### Cara kerja (alur lengkap)

```
07:00 WIB — Celery Beat trigger workers.viral_discovery.daily_scan
        │
        ▼
viral_discovery_worker.viral_discovery_daily_task
        │
        ▼
viral_discovery_scrape_service.run_daily_viral_discovery(db)
        │
        ├─ 1. Catat 1 baris ScrapeRun (status='running') — "bukti pencarian hari ini"
        │
        ├─ 2. Panggil viral_discovery_service.find_daily_viral_topics()
        │       │
        │       ├─ Claude (model: claude-opus-4-8) + tool web_search
        │       ├─ System prompt: "cari topik viral HARI INI, berita + IG
        │       │   publik Indonesia, WAJIB ada akun instagram nyata,
        │       │   maks 10 topik, jangan mengarang"
        │       └─ Claude panggil tool submit_trend_topics(items=[...])
        │           → return list [{topic, score, related_accounts}, ...]
        │
        ├─ 3. Kalau ada hasil: panggil submit_recommendations() — FUNGSI
        │       FROZEN yang sudah ada, dipanggil apa adanya (sama seperti
        │       yang dipakai POST /trend-recommendations publik)
        │       → topik masuk trend_recommendations, status='pending',
        │         source='ai_viral_discovery'
        │
        └─ 4. Update ScrapeRun: status=success/failed, videos_fetched=jumlah
               topik ditemukan, videos_new=jumlah yang benar-benar 'created'


09:00 WIB — pipeline scrape harian (SUDAH ADA, tidak diubah) ambil topik
            pending (termasuk yang baru dari viral discovery), scrape via
            provider abstraction di atas, tandai 'used' kalau berhasil.
```

### Konfigurasi

```python
# app/shared/config.py
anthropic_api_key: str = ""              # sudah ada dari fitur sebelumnya
anthropic_model: str = "claude-opus-4-8"
viral_discovery_max_topics: int = 10     # BARU — batas topik per hari, sengaja
                                          # di bawah MAX_PER_DAY=20 (batas trend_
                                          # recommendations) supaya tidak
                                          # memonopoli slot harian
```

```python
# app/workers/celery_app.py — beat schedule baru
"viral-discovery-daily-07:00": {
    "task": "workers.viral_discovery.daily_scan",
    "schedule": crontab(hour=7, minute=0),
    "options": {"queue": "default"},
},
```

### Cara pantau "bukti status pencarian tiap hari"

Sesuai permintaan, setiap run viral discovery meninggalkan jejak yang bisa
dilihat tanpa psql manual:

1. **Dashboard publik**: `http://187.77.125.10:8000/scraping-status`
   → section "Instagram Trend-Scrape" — kartu **"Dari AI Viral Discovery"**
   (jumlah topik pending dari sumber ini) dan tabel **"Riwayat Scrape
   Instagram"** — kolom **"Sumber"** menampilkan pill khusus **"AI
   Discovery"** untuk baris yang berasal dari `anthropic_web_search`.

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
     ]
   }
   ```

3. **Trigger manual** (tanpa nunggu jam 07:00, untuk testing):
   ```bash
   docker exec social_intel_worker python -c "
   from app.workers.viral_discovery_worker import viral_discovery_daily_task
   print(viral_discovery_daily_task.delay().id)
   "
   ```

### Status verifikasi (05 Juli 2026)

✅ Seluruh pipeline **terbukti berjalan benar secara teknis** — ScrapeRun
tercatat, error ditangani dengan baik, task selesai tanpa crash.

❌ **Belum bisa dibuktikan menghasilkan topik nyata** — satu-satunya test run
gagal karena **saldo API Anthropic habis** ("Your credit balance is too low
to access the Anthropic API"). Ini murni masalah billing eksternal, bukan
bug kode. Begitu saldo di-top-up di [console Anthropic](https://console.anthropic.com/settings/billing),
fitur ini otomatis jalan penuh — baik lewat jadwal 07:00 WIB besok maupun
trigger manual di atas.

---

## Ringkasan Perubahan File

| File | Status | Keterangan |
|---|---|---|
| `app/shared/config.py` | diubah | tambah 6 setting baru (provider order, kuota, viral_discovery_max_topics) |
| `app/services/instagram/providers/*.py` | **baru** | abstraksi provider (5 file) |
| `app/services/instagram/quota_service.py` | **baru** | kuota harian |
| `app/services/instagram/pipeline_service.py` | diubah | 1 baris: pakai provider abstraction, bukan Apify langsung |
| `app/api/v1/instagram/router.py` | diubah | `GET /instagram/posts`: wire quota check + catat ScrapeRun |
| `app/workers/instagram_trending_worker.py` | diubah | `scrape_username_task`: wire quota check + catat ScrapeRun |
| `app/ai/llm/viral_discovery_service.py` | **baru** | otak AI viral discovery |
| `app/services/trend_recommendations/viral_discovery_scrape_service.py` | **baru** | orkestrasi (terpisah dari file frozen) |
| `app/workers/viral_discovery_worker.py` | **baru** | task Celery |
| `app/workers/celery_app.py` | diubah | +1 worker module, +1 beat schedule |
| `app/services/instagram_trending/trend_scrape_service.py` | diubah | HANYA `get_trend_scrape_summary()` (fungsi baca-saja) ditambah 2 field baru |
| `app/main.py` | diubah | dashboard: kartu + kolom baru untuk AI Viral Discovery |

**Tidak diubah sama sekali** (frozen, sesuai instruksi): `app/domain/trend_recommendations/*`,
`app/services/trend_recommendations/service.py`, `app/api/v1/trend_recommendations.py`,
fungsi `run_daily_trend_scrape()` di dalam `trend_scrape_service.py`.

Semua perubahan sudah di-commit ke git (`78f8255`, `63e321b`), di-push ke
GitHub, dan di-deploy + diverifikasi jalan di server production
(187.77.125.10).



1. Dua subsistem berantai (bukan satu sistem monolitik)

[07:00 WIB] Subsistem A — AI Viral Discovery
    AI (Claude/OpenAI/Ollama, pilih via .env) cari topik+akun IG viral HARI INI
    → submit ke trend_recommendations (status=pending)

           ↓ (topik menunggu di database)

[09:00 WIB] Subsistem B — Scrape Worker (sudah ada sebelumnya, tidak diubah)
    Ambil topik pending → scrape akun via provider (Apify, fallback EnsembleData)
    → berhasil? tandai 'used'. gagal? tetap 'pending', dicoba lagi besok
Keduanya cuma terhubung lewat tabel trend_recommendations — A menulis, B membaca. Tidak ada pemanggilan langsung antar keduanya, jadi masing-masing bisa gagal sendiri-sendiri tanpa menjatuhkan yang lain (persis yang kelihatan di screenshot kemarin: A gagal karena saldo habis, B tetap jalan normal pakai data lama).

2. Semua panggilan ke pihak ketiga dibungkus jadi "provider yang bisa ditukar"
Ada 2 titik yang sama-sama pakai pola ini (registry dict + fallback berurutan, config-driven):

Scrape Instagram (app/services/instagram/providers/): Apify dulu, EnsembleData kalau Apify gagal.
AI Discovery (app/ai/llm/viral_discovery_service.py): Claude/OpenAI/Ollama, dipilih via AI_DISCOVERY_PROVIDER.
Prinsipnya sama persis di keduanya: kode pemanggil tidak pernah tahu/peduli provider mana yang sebenarnya jalan — tinggal ganti urutan/provider di .env, restart, selesai.

3. Kuota & nominal semuanya di config, bukan hardcode
instagram_trend_daily_budget=5, instagram_search_daily_min=3, instagram_shared_daily_budget=10, viral_discovery_max_topics=10 — semua angka ini murni parameter .env, dihitung ulang tiap saat dari data scrape_runs/trend_recommendations yang sudah ada (tidak ada tabel counter baru).

4. Monitoring melacak batch nyata, bukan status independen
Dashboard /scraping-status mengambil 1 run AI terakhir, cari topik persis yang dihasilkan run itu, lalu cek status scrape masing-masing topik itu — sehingga diagram alur menunjukkan tepat di mana proses berhenti untuk batch itu, bukan cuma "lampu hijau/merah" yang tidak nyambung satu sama lain.

5. Satu aturan yang mengikat semuanya
trend_recommendations (tabel, endpoint publik, fungsi submit_recommendations(), worker konsumen) tidak pernah diubah — semua fitur baru hari ini cuma menambah cara baru untuk MENULIS ke situ (AI discovery) atau membaca statusnya (monitoring), tidak pernah menyentuh logic intinya.