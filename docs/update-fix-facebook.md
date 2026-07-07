# Update & Fix Facebook — 06-07 Juli 2026

Rangkuman semua perubahan pada satu sesi kerja panjang: mulai dari insiden keamanan
Redis, perbaikan pipeline scraping, sampai fitur discovery Facebook baru. Semua item
di bawah sudah di-deploy ke server produksi (187.77.125.10) dan diverifikasi live
(curl asli/query DB langsung), bukan simulasi — kecuali yang ditandai eksplisit
"belum diverifikasi".

Commit terkait (urut kronologis): `e91463f` `5053c5a` `6aa24ff` `75f1273` `26191ee`
`a161411` `f213968` `869ebe0` `03b2e1d` `d0fa319`.

---

## 1. Insiden keamanan Redis (ditemukan, bukan direncanakan)

**Temuan**: Redis production ter-hijack — `role:slave` mereplikasi dari IP asing
(`109.244.159.27:29846`, link down), akibat Redis expose ke `0.0.0.0:6379` tanpa
password (`protected-mode: no`). Efek: `worker`/`worker-ai` crash-loop total,
SEMUA Celery task (bukan cuma Facebook) berhenti ±1.5 hari (2026-07-05 16:50 s/d
ditemukan).

**Fix** ([docker-compose.yml](../docker-compose.yml)):
- `redis-cli REPLICAOF NO ONE` (lepas dari master asing)
- Redis sekarang `--requirepass ${REDIS_PASSWORD} --protected-mode yes`, port
  publik dihapus (cuma reachable via docker network `social_intel_net`)
- Elasticsearch (`9200`) & Ollama (`11434`) — port publik JUGA ditutup (pola
  exposure sama seperti Redis, meski belum sampai di-exploit)
- Postgres (`5432`) **SENGAJA dibiarkan publik** atas keputusan eksplisit user
  (password server saat ini masih default/lemah — belum diubah, tahu risikonya)
- `REDIS_PASSWORD` disimpan di `.env` server saja, tidak pernah masuk git

**Verifikasi**: semua container `Up`/`healthy` setelah fix, `curl` dari container
`api` ke Redis/Elasticsearch/Ollama tetap berhasil (akses internal tidak
terganggu).

---

## 2. Bug besar: Celery queue `"default"` tidak pernah dikonsumsi worker manapun

**Ini akar masalah sebenarnya** kenapa AI viral discovery & auto-scrape harian
"tidak pernah jalan sendiri" — bukan cuma soal saldo Anthropic.

**Temuan** ([app/workers/celery_app.py](../app/workers/celery_app.py)): 7 entri
`beat_schedule` (viral-discovery-daily, instagram-trend-recommendation-daily,
facebook-trend-recommendation-daily, youtube-trending-daily, viral-tracking x2,
retry-embeddings) di-set `"options": {"queue": "default"}`. Tapi
`docker-compose.yml` cuma punya 2 consumer: `worker` (dengar
`collector,processing,reports,celery`) dan `worker-ai` (dengar `ai,celery`) —
**tidak ada yang dengar `"default"`**. Dibuktikan: `redis-cli LLEN default` = 77
pesan menumpuk selamanya.

**Fix**: hapus semua override `queue="default"`, biarkan jatuh ke
`task_default_queue` bawaan Celery (`"celery"`) yang memang dikonsumsi.

**Verifikasi**: `celery_app.send_task(..., queue=None)` (meniru persis cara Beat
kirim task) langsung tereksekusi dalam hitungan detik, bukan macet lagi.

---

## 3. Paritas API Facebook dengan Instagram (7 endpoint)

File: [app/api/v1/facebook/router.py](../app/api/v1/facebook/router.py),
[app/workers/facebook_trending_worker.py](../app/workers/facebook_trending_worker.py)
— pola disalin dari [app/api/v1/instagram/router.py](../app/api/v1/instagram/router.py).

| Endpoint | Fungsi |
|---|---|
| `GET /facebook/trending` | Topik trending dari `trend_recommendations` + post/sentimen |
| `GET /facebook/analysis/summary` | Agregat sentimen lintas semua post Facebook |
| `GET /facebook/comments` | List komentar dengan filter (username/post_id/sentiment/tanggal) |
| `POST /facebook/scrape` | Trigger scrape identifier via Celery (`workers.facebook.scrape_identifier`, task baru) |
| `POST /facebook/trend-scrape/run` | Trigger manual batch harian (tanpa nunggu jadwal) |
| `GET /facebook/trend-scrape/status` | Monitoring pending/used + riwayat run |

**Diverifikasi live**: `POST /facebook/scrape?username=narasi.tv` → real hit ke
Apify, 1 post asli tersimpan, dicek lagi via `GET /facebook/posts`.

**Bug ketemu+diperbaiki saat proses ini**: task baru sempat `NotRegistered`
karena `worker-ai` belum di-restart dengan kode baru (2 container share queue
`celery`, race lama yang sudah terdokumentasi). Fix: selalu restart KEDUA
`worker` dan `worker-ai` setelah deploy task baru, bukan cuma salah satu.

---

## 4. AI Viral Discovery — web search Ollama (Firecrawl → Tavily auto-switch)

**Masalah awal**: provider `ollama` di
[app/ai/llm/viral_discovery_service.py](../app/ai/llm/viral_discovery_service.py)
sebelumnya TIDAK punya browsing sama sekali (beda dengan Claude yang punya
`web_search` bawaan server-side).

**Dibangun** — tool `web_search` custom, model TIDAK eksekusi sendiri (tidak
punya akses jaringan), kode ini yang eksekusi lalu kirim hasil balik sebagai
pesan `role="tool"`:

```
Ollama minta web_search(query) → kode kita:
  1. Coba Firecrawl (api.firecrawl.dev/v1/search) dulu — hasil lebih
     relevan/spesifik per perbandingan langsung
  2. Firecrawl gagal/limit/kosong → fallback Tavily (api.tavily.com/search)
  3. Keduanya gagal → kasih tahu model apa adanya (BUKAN pura-pura ada hasil)
```

Config baru: `settings.firecrawl_api_key`, `settings.tavily_api_key`
([app/shared/config.py](../app/shared/config.py)). Key asli di `.env` server
(bukan git).

**Masalah kualitas ditemukan**: qwen3:8b (model Ollama yang dipakai) sering
mengarang/salah kaitkan akun meski hasil pencarian nyata (contoh: topik
"disinformasi Pemilu" dikaitkan ke `dior_indonesia`, brand fashion). Dua
perbaikan ditambahkan:

- **(A) Ekstraksi kandidat akun otomatis dari URL** — regex parse
  `facebook.com/...` dan `instagram.com/...` di teks hasil pencarian, sodorkan
  sebagai daftar eksplisit ke model ("WAJIB pakai username PERSIS dari daftar
  ini"). Konstanta filter (`_FB_RESERVED_PATHS`/`_IG_RESERVED_PATHS`) sengaja
  dipisah supaya gampang ditambah kalau ada pola URL baru yang bukan akun.
- **(B) Paksa 1x retry pencarian** (`FORCE_RETRY_LIMIT`) — kalau model
  menyerah (jawab teks tanpa tool_call) padahal belum submit apa pun, kode
  suntik pesan dorongan untuk coba search lagi, bukan langsung berhenti.

**Status provider aktif**: `AI_DISCOVERY_PROVIDER=ollama` di server (keputusan
eksplisit user, menerima risiko kualitas — "lebih baik ada topik daripada 0").
Diverifikasi live via task produksi asli: submit topik pertama yang PERNAH ada
dengan `source='ai_viral_discovery'` — *"PM Narendra Modi's Indonesia Tour"*
→ akun Facebook `TRUTH PrevaiL` (catatan: mengandung spasi, kemungkinan nama
tampilan bukan slug valid — self-healing, akan gagal scrape & tetap `pending`
kalau memang tidak valid, tidak merusak data).

**Follow-up diminta user, BELUM dibangun**: auto-switch `anthropic ⇄ ollama`
otomatis berdasarkan ketersediaan saldo — desain konkretnya belum dibahas.

---

## 5. `POST /facebook/discover` — cari akun Facebook TANPA AI menebak

**Alasan dibangun**: yield AI viral discovery (poin 4) untuk akun Facebook
rendah/kadang salah. Solusinya bukan memperbaiki AI lagi, tapi cari jalur lain
yang sama sekali tidak butuh AI menebak apa pun.

**Ditemukan via Apify Store API** (`curl api.apify.com/v2/store?search=facebook+search`):
actor `danek/facebook-search-ppr` — BEDA dari actor lama
(`ycQuEFDDZmgX7BAsL`, cuma scrape profil yang SUDAH diketahui) — actor baru ini
genuinely SEARCH Facebook by keyword, return post asli + data `author`
(id/url/name) **terstruktur**.

**File baru**:
- [app/integrations/apify/facebook_search.py](../app/integrations/apify/facebook_search.py)
  — `search_facebook_by_keyword()` + `extract_identifier()`. Identifier
  diambil dari `author.url` (regex, handle 2 bentuk URL: `/people/<nama>/<id>/`
  ambil ID di akhir, atau `/<slug>` biasa) — BUKAN dari `author.id` langsung
  karena profil personal bisa berformat `pfbid...` (token internal, tidak
  valid untuk scrape ulang).
- [app/services/facebook/trend_scrape_service.py](../app/services/facebook/trend_scrape_service.py)
  — `discover_facebook_topic_by_keyword()`: search → extract akun unik →
  submit ke `trend_recommendations` (`source='manual_facebook_search'`) lewat
  `submit_recommendations()` yang SUDAH ADA (tidak diubah).
- Endpoint: `POST /facebook/discover?keyword=...&max_results=...&location=...`

**Config baru**: `settings.facebook_search_actor_id = "danek/facebook-search-ppr"`.

**Harga**: pay-per-result (~$0.003/hasil per Juli 2026), pakai
`APIFY_API_TOKEN` yang sama (bukan langganan baru). **Akun Apify FREE dibatasi
5 hasil/panggilan** (batasan dari provider, bukan kode kita).

**Diverifikasi live 2x**:
1. Query "Piala Dunia U-18" → 5 post berita sepak bola Indonesia asli, relevan
2. Lewat endpoint asli, "Piala Presiden 2026" → 5 post + 5 akun ter-extract
   benar (`sports.indosiar`, `gsa.socer`, + 3 ID numerik dari URL
   `/permalink.php?...&id=X`), berhasil masuk `trend_recommendations`
   (dicek langsung ke DB: `created: ["Piala Presiden 2026"]`)

**Belum diverifikasi**: apakah identifier hasil extract ini 100% bisa
di-scrape sukses oleh pipeline harian (`scrape_facebook_posts_via_provider` /
actor `ycQuEFDDZmgX7BAsL`) — baru sampai tahap submit ke
`trend_recommendations`, belum ditunggu giliran scrape budget harian
memprosesnya.

**Sekarang ada 2 jalur yang mengisi `trend_recommendations` untuk Facebook**
(tidak saling konflik, cuma entry point beda ke `submit_recommendations()`
yang sama):
1. AI Viral Discovery (Ollama, otomatis 07:00 WIB) — `source='ai_viral_discovery'`
2. `POST /facebook/discover` (manual, user trigger keyword) — `source='manual_facebook_search'`

---

## 6. `GET /facebook/posts/search` — filter rentang tanggal + tampilkan semua data lokal

`q` sekarang **opsional**:
- **`q` diisi**: perilaku lama (cari keyword/hashtag, fallback cari topik +
  scrape via `trend_recommendations` kalau tak ketemu di DB) — tidak berubah.
- **`q` kosong**: listing SEMUA post Facebook di database lokal (urut
  `published_at` terbaru dulu), TANPA fallback scrape (tidak ada keyword untuk
  dicocokkan ke topik).

`date_from`/`date_to` (filter `published_at`) berlaku di KEDUA mode. Ditambah
`offset` untuk pagination, dan `total` = jumlah row SEBENARNYA yang cocok
filter (bukan cuma count di halaman itu) — untuk data besar, loop
`offset += limit` sampai `offset >= total`.

```
GET /facebook/posts/search?date_from=2026-07-01&date_to=2026-07-07&limit=100
```

**Diverifikasi live, termasuk edge case**:
- `q` kosong → 3 post lokal ✓
- `date_to=2026-07-05` → total turun 3→2 (post tgl 07-06 ke-exclude benar) ✓
- Format tanggal salah → HTTP 422 (bukan crash) ✓
- `date_from > date_to` (rentang terbalik) → kosong dengan aman, bukan error ✓
- `limit=1&offset=0` vs `offset=1` → dapat post yang BEDA (pagination genuinely jalan) ✓
- `q` + `date_from`/`date_to` sekaligus → filter gabungan (AND) ✓
- `limit=99999` (di luar batas 100) → HTTP 422 ✓

---

## 7. Dashboard `/scraping-status` — perbaikan akurasi

**Masalah ditemukan**: label node "AI Discovery" di visualisasi pipeline
hardcode teks **"Claude web_search"** — jadi salah info sejak provider
di-switch ke Ollama (poin 4).

**Fix**:
- [app/services/trend_recommendations/viral_discovery_scrape_service.py](../app/services/trend_recommendations/viral_discovery_scrape_service.py):
  `ScrapeRun.api_source` untuk run `ai_viral_discovery` sekarang diisi dinamis
  dari `settings.ai_discovery_provider` (dulu hardcode
  `"anthropic_web_search"`). `get_viral_discovery_trace()` expose field
  `api_source` ini ke response.
- [app/main.py](../app/main.py): label node "AI Discovery" sekarang baca
  `aiRun.api_source` (mapping: `anthropic`→"Claude web_search",
  `ollama`→"Ollama + Firecrawl/Tavily", `openai`→"OpenAI (tanpa browsing)").
- Sekalian perbaiki `get_viral_discovery_trace()`: pencarian scrape_attempt
  per topik dulu hardcode `platform='instagram'` — jadi topik yang akunnya
  CUMA Facebook (mis. hasil AI viral discovery) tidak pernah kelihatan status
  scrape-nya di dashboard. Sekarang match `keyword_text` lintas platform
  (tanpa filter platform).

**Anomali ditemukan, BELUM terpecahkan** (dicatat jujur, bukan disembunyikan):
ada 1 row `scrape_runs` (`2026-07-07 00:00:00 UTC`, jadwal otomatis 07:00 WIB)
yang errornya spesifik Anthropic ("credit balance too low") padahal
`AI_DISCOVERY_PROVIDER` seharusnya sudah `"ollama"` saat itu. Log
`worker-beat` di sekitar waktu itu sudah hilang (container sempat
di-restart/recreate berkali-kali sesi ini). Dikonfirmasi ulang: setting yang
terbaca SEKARANG di container yang jalan = `"ollama"` — jadi tidak
mempengaruhi run berikutnya, tapi penyebab pasti row lama ini belum
terjawab.

---

## Ringkasan file & folder yang berubah/baru

```
app/
├── ai/llm/viral_discovery_service.py          [UBAH] web search Firecrawl/Tavily, ekstraksi kandidat, forced retry
├── api/v1/facebook/router.py                  [UBAH] +7 endpoint parity, /discover, date-range search
├── integrations/apify/
│   ├── facebook.py                            (tidak diubah — scrape profil yang sudah diketahui)
│   └── facebook_search.py                     [BARU] search Facebook by keyword (danek/facebook-search-ppr)
├── main.py                                     [UBAH] fix label provider AI di dashboard /scraping-status
├── services/
│   ├── facebook/trend_scrape_service.py       [UBAH] +discover_facebook_topic_by_keyword()
│   └── trend_recommendations/viral_discovery_scrape_service.py  [UBAH] api_source dinamis, trace lintas platform
├── shared/config.py                            [UBAH] +firecrawl_api_key, +tavily_api_key, +facebook_search_actor_id
└── workers/
    ├── celery_app.py                           [UBAH] hapus routing queue="default"
    └── facebook_trending_worker.py             [UBAH] +task workers.facebook.scrape_identifier

docker-compose.yml                              [UBAH] Redis auth+tutup port, ES/Ollama tutup port, worker OLLAMA_BASE_URL
.env (server, TIDAK di git)                      [UBAH] +REDIS_PASSWORD +FIRECRAWL_API_KEY +TAVILY_API_KEY, AI_DISCOVERY_PROVIDER=ollama
```

## Third-party yang terlibat

| Layanan | Fungsi di sistem ini | Status |
|---|---|---|
| **Apify** (`ycQuEFDDZmgX7BAsL`) | Scrape post dari profil Facebook yang SUDAH diketahui namanya | Aktif, lama |
| **Apify** (`danek/facebook-search-ppr`) | Search Facebook by keyword, dapat post+author terstruktur | **Baru**, aktif, pay-per-result |
| **Ollama** (`qwen3:8b`, self-hosted) | Model AI viral discovery (provider aktif sekarang) | Aktif |
| **Firecrawl** | Web search primary untuk Ollama | **Baru**, aktif |
| **Tavily** | Web search fallback untuk Ollama | **Baru**, aktif (fallback) |
| **Anthropic (Claude)** | Provider AI discovery alternatif (saldo habis, tidak aktif) | Standby |
| **Redis** | Broker Celery — **sempat kena hijack**, sudah diamankan (password + tutup port) | Aktif |
