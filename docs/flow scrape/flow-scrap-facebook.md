# Flow Scrape Facebook — Auto-Discovery via trend_recommendations

Dibangun 05 Juli 2026, mirroring arsitektur Instagram
(`docs/dok-ai-viral-discovery-otomatis.md`,
`docs/dok-instagram-data-pipeline.md`) tapi **terpisah total** — tidak
menyentuh fungsi Instagram yang frozen.

---

## Latar belakang — kenapa dua provider, kenapa bukan Meta saja

### Meta Graph API resmi — diverifikasi live 05 Juli 2026 dengan token asli

User memberikan token Facebook asli untuk dites langsung (bukan asumsi):

| Test | Hasil |
|---|---|
| Token user (`FadLy RA`) | ✅ Hidup, kelola 6 Page: BaliDay, RedTeam.ID, Celebes Nusa Digital, Toraja ID, PT. Celebes Tekno Global, Toraja Indie |
| Token kedua yang diberikan | ❌ Expired sejak Mei 2026 |
| Baca post Page **milik sendiri** (RedTeam.ID) | ✅ Berhasil |
| Baca post Page **publik di luar milik sendiri** (nike) | ❌ Diblokir: `"This endpoint requires ... 'Page Public Content Access' feature"` — fitur ini harus di-approve Meta di level App (App Review + verifikasi bisnis), bukan sekadar izin token |
| Cari page by keyword (`/search?type=page`) | ❌ Mati total: `"New Pages Experience Is Not Supported"` — Meta sudah menghapus endpoint ini |

**Kesimpulan**: token Meta yang ada genuinely berguna, tapi **cuma untuk 6 Page yang dikelola sendiri** — tidak bisa dipakai untuk akun manapun yang ditemukan AI discovery (yang jelas bukan salah satu dari 6 Page itu). Ini pembatasan dari Meta, bukan masalah kode.

### Apify — diverifikasi live sebagai alternatif

Actor yang sama dengan Instagram (`ycQuEFDDZmgX7BAsL`, sub-actor
`apify/facebook-posts-scraper` + `apify/facebook-comments-scraper`) terbukti
bisa scrape **page publik manapun**, termasuk yang bukan dikelola sendiri:

```
pratiwinoviyanthireal → 4 item, post+komentar nyata, profileFollowers: 8.319.658+
detikcom → 0 post (page tertentu kadang blokir scraper tanpa login, variatif per-halaman)
```

Bentuk datanya **identik** dengan Instagram (`postUrl`, `postDescription`,
`postLikesCount`, `commentText`, dst — cuma `targetPlatform:'facebook'`).

### Keputusan

Pakai **Apify sebagai satu-satunya provider aktif** sekarang, tapi bangun
lewat **provider abstraction** (persis pola Instagram: `BaseFacebookSearchProvider`
+ registry + fallback) supaya provider lain (Meta app Business terverifikasi
nanti, atau pihak ketiga lain) tinggal ditambah ke `PROVIDERS` dict +
`FACEBOOK_SEARCH_PROVIDER_ORDER` di `.env` — **tanpa ubah kode pemanggil**.

---

## 2 fakta yang mengubah desain awal

**Fakta 1** — `related_accounts` sebenarnya SUDAH mendukung Facebook,
kolomnya JSON bebas (`list[dict]`), sudah terbukti menyimpan
`platform: "twitter"` dan `platform: "tiktok"` di data live sebelumnya.
**Tidak perlu migrasi/schema baru.**

**Fakta 2** — yang benar-benar menghalangi: prompt AI Discovery
(`viral_discovery_service.py`) di-hardcode cuma minta akun Instagram —
"kalau tidak menemukan akun Instagram sama sekali untuk suatu topik, jangan
sertakan topik itu." Jadi walaupun AI tahu ada page Facebook untuk suatu
topik, dia diinstruksikan membuang topik itu kalau tidak ada Instagram-nya
juga. **Ini yang diubah**, bukan tabelnya. `_extract_items()` juga
sebelumnya memfilter keras `platform == "instagram"` — jadi walau prompt
sudah dibuka, kode ini akan tetap membuang akun Facebook kalau tidak ikut
diperbaiki (sudah diperbaiki juga).

---

## Flow lengkap

```
[Subsistem A — AI Discovery, DIPERLUAS dari yang sudah ada]
Prompt AI (app/ai/llm/viral_discovery_service.py) sekarang boleh sertakan
akun Facebook (platform='facebook') JUGA, tidak wajib selalu ada Instagram.
        │
        ▼
submit_recommendations() — FROZEN, TIDAK DISENTUH. Kolom related_accounts
sudah JSON bebas, sudah otomatis terima platform apapun tanpa ubah kode.
        │
        ▼
trend_recommendations (status='pending', related_accounts bisa berisi
instagram DAN/ATAU facebook untuk topik yang sama, tabel yang SAMA,
tidak dipisah per platform)

                    ↓ (topik menunggu, dibaca ulang tiap hari)

[Subsistem B — Facebook Scrape Worker, BARU, terpisah dari Instagram]
run_daily_trend_scrape_facebook()  — app/services/facebook/trend_scrape_service.py
        │
        ├─ Ambil topik pending dengan related_account platform='facebook',
        │   urut score tertinggi, maks facebook_trend_daily_budget (default 5)
        │
        ├─ Skip kalau akun sudah discrape hari ini (dedup akun-per-hari,
        │   sama pola dengan Instagram — dicek ke posts table langsung)
        │
        ├─► search_profile_with_fallback() — app/services/facebook/providers/registry.py
        │     └─► ApifyFacebookProvider (satu-satunya provider aktif)
        │           berhasil? → simpan
        │           gagal?    → [slot provider ke-2 kosong, siap diisi nanti —
        │                        inilah "slot switch" yang diminta]
        │
        └─ Verifikasi (>=1 post) → topic.status='used'. Gagal → tetap pending.
        │
        ▼
Simpan ke tabel GENERIC yang SAMA dengan Instagram (platform='facebook'):
  - posts.url              ← postUrl
  - posts.content          ← postDescription
  - posts.metadata_.likes  ← postLikesCount
  - posts.metadata_.comments ← postCommentsCount
  - comments.content       ← commentText
  - entities (HASHTAG)     ← hashtag di caption
  - sentiments             ← dispatch workers.analyze_post (IndoBERT, async)
  - lexicon_analyses       ← sentimen komentar (lexicon)
  - scrape_runs            ← bukti tiap percobaan (platform='facebook')
```

## Jadwal

`facebook-trend-recommendation-daily` — default **10:00 WIB**, 1 jam setelah
Instagram (09:00), supaya tidak rebutan resource. Configurable via `.env`:
`FACEBOOK_TREND_SCRAPE_SCHEDULE_HOUR`/`_MINUTE`.

## File yang dibuat/diubah

| File | Status | Keterangan |
|---|---|---|
| `app/integrations/apify/facebook.py` | **Baru** | Panggilan Actor Apify mentah untuk Facebook |
| `app/services/facebook/providers/base.py` | **Baru** | `BaseFacebookSearchProvider(ABC)` |
| `app/services/facebook/providers/apify_provider.py` | **Baru** | Wrapper Apify |
| `app/services/facebook/providers/registry.py` | **Baru** | `PROVIDERS` dict + fallback — slot provider ke-2 kosong, siap diisi |
| `app/services/facebook/pipeline_service.py` | Diubah | Tambah `scrape_facebook_posts_via_provider()` — TERPISAH dari `scrape_facebook_posts()` (Meta resmi, dipakai `GET /facebook/posts`, tidak disentuh) |
| `app/services/facebook/trend_scrape_service.py` | **Baru** | `run_daily_trend_scrape_facebook()` — Subsistem B, mirroring Instagram TAPI terpisah total |
| `app/workers/facebook_trending_worker.py` | **Baru** | Task Celery `workers.facebook_trend_recommendation.daily` |
| `app/workers/celery_app.py` | Diubah | +1 include, +1 beat schedule |
| `app/ai/llm/viral_discovery_service.py` | Diubah | Prompt & `_extract_items()` sekarang terima `platform='facebook'` juga (sebelumnya cuma `instagram`) |
| `app/shared/config.py` | Diubah | `facebook_search_provider_order`, `facebook_trend_daily_budget` (default 5), `facebook_trend_posts_per_topic`, `facebook_trend_comments_per_post`, jadwal beat |

**Tidak diubah sama sekali** (frozen): `run_daily_trend_scrape()` Instagram,
`submit_recommendations()`, model/skema `trend_recommendations`,
`scrape_facebook_posts()` (Meta resmi, ad-hoc, dipakai `GET /facebook/posts`).

## Verifikasi live (05 Juli 2026)

1. `search_profile_with_fallback('pratiwinoviyanthireal', 1, 2)` → `provider_used: apify`, 2 baris data nyata.
2. `scrape_facebook_posts_via_provider()` → 2 post tersimpan (`platform='facebook'`), 2 komentar + lexicon sentiment, 3 hashtag masuk `entities` (`relawan`, `kemanusiaan`, `pedulisesama`).
3. Panggil ulang akun yang sama → `provider_used: cached_today`, dedup akun-per-hari terbukti jalan.
4. **End-to-end penuh** (dengan 1 baris topik uji coba di `trend_recommendations`, disetujui user, dibiarkan sebagai riwayat — topic `TEST_FACEBOOK_PIPELINE_VERIFICATION`): `run_daily_trend_scrape_facebook()` → topik diambil, discrape (resolve via dedup), `status` berubah `pending → used`, `scrape_runs` tercatat `success`.

## Batasan yang masih berlaku

- **Belum ada topik nyata dengan akun Facebook** — menunggu AI Discovery
  benar-benar berhasil jalan (masih terhalang saldo Anthropic habis, sama
  seperti Instagram).
- **Provider cuma 1 (Apify)** — kalau Apify gagal untuk suatu akun, TIDAK
  ada fallback lain saat ini (slot kosong, siap diisi begitu ada provider
  lain, misal Meta app Business terverifikasi, atau pihak ketiga lain).
- **Belum terintegrasi ke dashboard `/scraping-status`** — monitoring
  "Sedang Berjalan Sekarang" & pipeline flow diagram saat ini cuma pantau
  Instagram; belum diminta untuk Facebook di sesi ini.
