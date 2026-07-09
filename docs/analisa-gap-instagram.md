# Analisa Instagram: Metode Scraping, Pencarian Keyword, dan Trending Monitoring

Tanggal: 2026-07-09. Dipicu oleh laporan user: "trending monitoring sepertinya
hanya menampilkan data dummy". Analisa kode + cek LANGSUNG ke database & container
produksi (187.77.125.10), bukan asumsi.

**Catatan keamanan penting dari sesi ini:** saat mengecek `.env` server, satu
perintah `grep`+`sed` yang salah pola SEMPAT menampilkan hampir seluruh nilai
`ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, `FIRECRAWL_API_KEY` di transcript chat.
User sudah diberi tahu saat itu juga dan disarankan rotate ketiga key tersebut.
Tidak ada nilai key yang diulang/dikutip di dokumen ini.

---

## 1. Metode Scraping Instagram — SEHAT, tidak ada masalah

`app/services/instagram/pipeline_service.py` + `app/services/instagram/providers/`:
- Provider abstraction dengan **auto-fallback Apify → EnsembleData**
  (`instagram_search_provider_order` default `"apify,ensembledata"`,
  `providers/registry.py:22-25`) — Instagram justru LEBIH baik dari Facebook
  di sisi ini (Facebook cuma Apify, lihat `docs/analisa-gap-facebook.md` gap 1).
- Dedup akun-per-hari sebelum panggil provider (`pipeline_service.py:76-93`) —
  bekerja seperti didesain.
- Post → tabel `posts`, hashtag → `entities`, sentimen post via IndoBERT
  (Celery task `analyze_post`), komentar via lexicon — sesuai
  `docs/dok-instagram-data-pipeline.md`.

**Tidak ada perubahan yang diperlukan di sini.**

## 2. Pencarian Keyword (`GET /instagram/posts/search`) — bekerja, tapi cuma 2 tingkat

Alur (`app/api/v1/instagram/router.py:457-590`):
1. Cari `posts.content`/`entities` (hashtag) di DB lokal.
2. Tidak ketemu → cari topik cocok di `trend_recommendations` (ILIKE) yang
   punya akun Instagram → ketemu & kuota harian ada → scrape sekarang;
   kuota habis → tetap `pending` untuk batch berikutnya.
3. Topik juga tidak ketemu → **langsung `"not_found"`** — TIDAK ada tingkat 3
   (search langsung ke Instagram via Apify) seperti yang baru dibangun untuk
   Facebook (`discover_facebook_topic_by_keyword`). Alasan tertulis di kode:
   "Apify tidak bisa cari-by-keyword sendiri" (`router.py:477`) — ini benar
   untuk actor yang SEKARANG dipakai (`ycQuEFDDZmgX7BAsL`, search-by-username
   saja), TAPI Apify punya actor lain (mis. hashtag/keyword scraper Instagram)
   yang belum dicoba. Jadi ini gap fitur (asimetri dengan Facebook), BUKAN
   penyebab "data dummy" — tidak masuk prioritas sesi ini kecuali diminta.

## 3. Trending Monitoring — DITEMUKAN 2 MASALAH NYATA

### Masalah A (UTAMA — ini yang bikin terlihat "dummy"): dashboard publik menampilkan tabel MATI sejak 2026-07-02

`GET /youtube/monitor-public` (dashboard `/scraping-status`, PUBLIK tanpa
auth) di `app/api/v1/youtube/router.py:2270-2304` dan `2430-2443` mengisi key
`"instagram.trending.accounts"` dari query:
```sql
SELECT ... FROM instagram_trending_accounts WHERE status = 'active'
ORDER BY rank ASC LIMIT 10
```

**`instagram_trending_accounts` adalah tabel dari pipeline discovery-by-hashtag
LAMA yang sudah dihapus totalitas kodenya sejak migrasi ke Apify (03-04 Juli
2026)** — tabelnya sendiri sengaja TIDAK dihapus ("dorman", lihat memory
project). Dicek langsung ke DB produksi hari ini:

| username | rank | trending_score | posts_collected | last_scraped_date | created_at |
|---|---|---|---|---|---|
| tukang_jelajah | 1 | 11.445 | 0 | 2026-07-03 (GAGAL, EnsembleData 493) | 2026-07-02 23:31:45 |
| radityadika | 2 | 7.05 | 0 | — (tidak pernah) | 2026-07-02 23:54:57 |
| awkarin | 3 | 5.0556 | 0 | — | 2026-07-02 23:54:57 |
| nihongo_mantappu | 4 | 3.625 | 0 | — | 2026-07-02 23:54:57 |
| benartajam | 5 | 0.5441 | 0 | — | 2026-07-02 23:54:57 |

Ciri-ciri data seed/test, bukan hasil scraping nyata: **`posts_collected = 0`
untuk SEMUA baris** (tidak pernah benar-benar scrape sukses satu pun), 4 dari
5 baris `created_at` PERSIS di detik yang sama (`23:54:57.009985`, insert
batch satu kali — cocok dengan `scripts/seed_instagram_trending_test.sql`
yang memang ada di repo), `discovered_via` cuma string hashtag statis
(`"#viral"`, `"#fyp"`, dst), dan `status='active'` tidak pernah berubah
walau tidak pernah ada aktivitas scraping sejak 6+ hari lalu.

**Yang membuatnya terlihat seperti data hidup (bukan cuma tabel kosong)**:
respons JSON-nya dibungkus dengan field yang KELIHATAN seperti pipeline aktif
— `"schedule": "09:00 WIB (daily, Celery Beat)"` (jadwal REAL milik pipeline
lain, `instagram_trend_scrape`, ditempel di sini juga) dan
**`"provider": "ensembledata"` yang sudah SALAH/USANG** (provider utama
Instagram sekarang Apify, EnsembleData cuma fallback — lihat bagian 1).

Section ini duduk BERSEBELAHAN dengan `"instagram_trend_scrape"` (section
LAIN yang genuinely live, sumbernya `get_trend_scrape_summary()`) di respons
JSON yang sama — dari sisi user, keduanya kelihatan sama-sama "bagian
monitoring Instagram", padahal satu mati total sejak 6+ hari, satu lagi hidup.

### Masalah B (kontribusi tambahan): AI viral discovery otomatis nyaris tidak pernah menghasilkan topik Instagram

Cek `scrape_runs` (`keyword_text='ai_viral_discovery'`) — scheduler-nya
TERBUKTI jalan tiap hari (`triggered_by='celery_beat'`, konsisten ~00:00 UTC
= 07:00 WIB, bug queue `"default"` yang dulu memblokir ini SUDAH diperbaiki
per `project_celery_default_queue_bug`), tapi:
- Anthropic masih gagal tiap run karena saldo habis (`credit balance is too
  low`) — otomatis fallback ke Ollama (`AI_DISCOVERY_PROVIDER=auto`, sudah
  aktif di server).
- Run HARI INI (2026-07-09, `status='success'`, `api_source='auto'`) memang
  submit 5 topik baru — TAPI **0 dari 5 punya akun Instagram** (3 Facebook,
  2 TikTok). Topik terakhir yang PUNYA akun Instagram dari sumber
  `ai_viral_discovery` genuinely: **TIDAK ADA SATU PUN** sejak fitur ini ada
  (`SELECT count(*) FROM trend_recommendations WHERE source='ai_viral_discovery'
  AND related_accounts::text ILIKE '%instagram%'` = 0 baris).
- Topik Instagram TERBARU di database manapun adalah **2026-07-04** (5 hari
  lalu), semuanya `source='ai_keyword_search'`/`'external_ai'` (pipeline lama
  yang sudah dihapus/submission manual) — bukan dari sistem otomatis yang
  jalan sekarang.
- Model Ollama (`qwen3:8b`, CPU-only, ~5.4 token/detik dari log container —
  1 run bisa 4-10 menit) sudah diketahui bermasalah kualitas ekstraksi akun
  (lihat memory `project_ollama_websearch_quality` — contoh nyata topik
  politik dikaitkan ke akun brand fashion yang tidak masuk akal). Ini bukan
  temuan baru, tapi baru sekarang terlihat DAMPAKNYA konkret ke Instagram:
  akun yang dihasilkan condong ke Facebook/TikTok, Instagram nyaris kosong.

### Temuan sampingan (bukan penyebab "dummy", tapi terkait & sebaiknya diperbaiki bareng)

**3 dari 5 slot budget harian Instagram terbuang percuma tiap hari** — 3
topik pending (`"Kabar konser Coldplay Jakarta 2026"`, `"War tiket..."`,
`"Waspada akun palsu..."`, tanggal 2026-07-03) SEMUANYA menunjuk ke akun
Instagram yang SAMA (`coldplay_jakarta`) yang gagal discrape (0 post,
kemungkinan username salah/tidak ada) — dicoba ulang 3x TERPISAH tiap hari
(scrape_runs 07-07, 07-08, 07-09 jam ~02:00 UTC = 09:00 WIB, semua gagal),
karena dedup akun-per-hari cuma cek tabel `posts` (butuh scrape SUKSES dulu
biar ke-dedup) — kalau selalu gagal, dedup tidak pernah kena, jadi tetap 3x
panggilan Apify terpisah per hari untuk akun yang sama.

Ini terjadi karena **`mark_failed_permanent_if_exhausted()` sengaja TIDAK
dipanggil untuk Instagram** (`app/services/instagram_trending/trend_scrape_service.py`
adalah file FROZEN, beda dengan Facebook/TikTok yang sudah dipasangi fungsi
ini — lihat docstring di `app/services/trend_recommendations/service.py:119-123`).
Efeknya: 3 dari 5 budget/hari (60%) terus terbuang selamanya untuk topik yang
sudah terbukti tidak akan pernah berhasil, mengurangi kapasitas efektif untuk
topik BARU jadi cuma 2/hari.

---

## Ringkasan akar masalah "trending monitoring data dummy"

1. **Penyebab LANGSUNG (paling mungkin ini yang dilihat user)**: dashboard
   publik menampilkan tabel `instagram_trending_accounts` yang mati total
   sejak 2026-07-02 — data seed/test, bukan hasil scraping nyata, disamarkan
   dengan label seolah bagian dari pipeline aktif (`schedule`, `provider`
   yang usang/salah).
2. **Penyebab TIDAK LANGSUNG**: bahkan pipeline yang GENUINELY live
   (`instagram_trend_scrape`, sumber `get_trend_scrape_summary()`) datanya
   sangat basi untuk Instagram spesifik (topik terakhir 5 hari lalu) karena
   AI viral discovery otomatis (yang sudah terbukti jadwalnya jalan) nyaris
   tidak pernah menghasilkan akun Instagram dari fallback Ollama.
3. **Kontributor sekunder**: 60% budget harian Instagram terbuang di topik
   yang sudah terbukti gagal berulang (tidak ada proteksi `failed_permanent`
   di Instagram, beda dari Facebook/TikTok).

## Opsi perbaikan (BELUM dieksekusi, menunggu keputusan user)

- **A. Hapus section `instagram_trending_accounts` di dashboard publik —
  SUDAH DIIMPLEMENTASI.** Detail di bawah.
- **B. Pasang `mark_failed_permanent_if_exhausted()` ke Instagram —
  SUDAH DIIMPLEMENTASI** (dikonfirmasi eksplisit user dulu, menyentuh file
  FROZEN). Detail di bawah.
- **C. Tambah tingkat 3 di `GET /instagram/posts/search` — SUDAH
  DIIMPLEMENTASI.** Detail di bawah.

## C — Detail implementasi (2026-07-09)

**Riset actor Apify** (via WebSearch + WebFetch, 2 kandidat ditemukan):
- `apify/instagram-hashtag-scraper` — search by hashtag ATAU keyword
  (`keywordSearch: true`), hasil LANGSUNG post/reel (caption, likes,
  komentar). **Dipilih user.**
- `apify/instagram-search-scraper` — punya mode `search type: user` (cari
  akun by keyword), analog paling dekat dengan Facebook
  `danek/facebook-search-ppr`. Tidak dipilih.

**Live-tested 2x via server produksi SEBELUM coding** (bukan asumsi dari
dokumentasi Apify, konsisten dengan cara kerja sesi-sesi Apify sebelumnya —
lihat `docs/apify-instagram-method.md`):
1. `hashtags:["coldplayjakarta"], keywordSearch:true, resultsLimit:2` →
   SUCCEEDED, 2 item, field lengkap dikonfirmasi (`id`, `shortCode`,
   `caption`, `url`, `ownerUsername`, `likesCount`, `commentsCount`,
   `timestamp`, `hashtags` sudah array terstruktur, `firstComment`,
   `latestComments`).
2. `hashtags:["jakarta"], keywordSearch:false, resultsLimit:5` → SUCCEEDED,
   5 item segar, tapi **`latestComments` selalu `[]` di kedua test**
   (kemungkinan karena akun Apify server ini FREE plan — log actor bilang
   "Free usage limited by first page of results"). `firstComment` (string
   tunggal) SELALU ada meski kosong — dipakai sebagai fallback grounded.

**File baru:**
- `app/integrations/apify/instagram_search.py` —
  `search_instagram_posts_by_keyword()` (panggil actor) +
  `extract_comments()` (ekstraksi komentar DEFENSIF: coba beberapa nama
  field umum di `latestComments`, fallback `firstComment`, TIDAK PERNAH
  crash kalau shape beda dari dugaan — mengingat shape aslinya belum
  100% terverifikasi live). Diuji standalone 4 skenario (shape asli
  terverifikasi, shape dict alternatif, shape tak dikenal/list-of-string,
  kosong total) — semua lolos.

**File diubah:**
- `app/shared/config.py` — `instagram_search_actor_id` (config-only, gampang
  diganti actor lain via `.env` tanpa ubah kode, pola sama dengan
  `facebook_search_actor_id`).
- `app/services/instagram/pipeline_service.py` —
  `save_instagram_keyword_search_results()`: simpan hasil ke
  posts/comments/entities. **Dedup pakai `external_id = shortCode`, SKEMA
  SAMA dengan `scrape_instagram_posts()`** (jalur per-username yang sudah
  ada) — post yang sama otomatis tidak dobel walau ditemukan lewat jalur
  manapun. Hashtag dari `item["hashtags"]` (sudah terstruktur dari Apify,
  TIDAK perlu regex-extract ulang seperti jalur lama).
- `app/api/v1/instagram/router.py` — `GET /instagram/posts/search` tingkat 3
  BARU: kalau topik juga tidak ketemu di `trend_recommendations`, panggil
  `search_instagram_posts_by_keyword(q_clean, max_results=5)` (dibatasi 5
  hasil per panggilan, MIRRORING batasan cost-control yang sama dipakai
  Facebook `discover_facebook_topic_by_keyword`, meski `limit` query param
  endpoint ini bisa sampai 100 — supaya biaya Apify per keyword-miss tetap
  kecil & terkontrol) → simpan via fungsi di atas → `source:
  "scraped_now_keyword_search"`. `ScrapeRun` dicatat (`api_source:
  "apify_keyword_search"`) untuk monitoring, konsisten pola commit
  `status='running'` segera supaya kelihatan live. Gagal panggil Apify ATAU
  0 hasil → tetap `source: "not_found"` dengan pesan jelas (beda pesan untuk
  dua kasus itu). Docstring endpoint + docstring modul router diperbarui,
  comment "2. Ketemu topik" lama direname jadi "2b" supaya tidak bentrok
  penomoran dengan tingkat 3 baru.

**Perbedaan arsitektur signifikan dari Facebook (disengaja, bukan
inkonsistensi):** Facebook tingkat 3 cari AKUN dulu (`discover_facebook_topic_by_keyword`)
lalu scrape akun itu lewat pipeline per-akun yang sudah ada, DAN submit ke
`trend_recommendations` supaya bisa di-scrape ulang lebih lengkap oleh batch
harian. Instagram tingkat 3 di sini TIDAK menyentuh `trend_recommendations`
sama sekali — actor-nya langsung mengembalikan POST lintas-akun, jadi tidak
ada satu "akun" tunggal yang masuk akal untuk disubmit ke pipeline budget
harian. Trade-off: lebih sederhana & tidak menyentuh apapun yang berhubungan
dengan tabel frozen, TAPI tidak "belajar" akun baru untuk scraping berulang
seperti Facebook.

**Diverifikasi**: syntax-check (`ast.parse`) lolos untuk 4 file. Unit test
standalone `extract_comments()` 4 skenario lolos. **BELUM diverifikasi
end-to-end live** (belum ada request nyata ke endpoint dengan keyword yang
genuinely trigger tingkat 3) — rencana verifikasi setelah deploy: cari
keyword yang dipastikan tidak ada di DB/trend_recommendations, panggil
endpoint, cek response `source` + cek baris baru masuk ke `posts`/`entities`
dengan `metadata->>'source' = 'apify_keyword_search'`.
- **D. Perbaiki kualitas AI viral discovery — SUDAH DIIMPLEMENTASI (prioritas
  pertama, dipilih user karena ini inti pencarian otomatis).**

## D — Detail implementasi (2026-07-09)

File: `app/ai/llm/viral_discovery_service.py` (BUKAN file frozen — cuma
`trend_scrape_service.py` yang frozen, file ini terpisah & sudah biasa
diubah, lihat docstring `viral_discovery_scrape_service.py`).

`_extract_items()` (baris ~152) ditambah 2 lapis validasi baru, di luar
filter platform yang sudah ada:
1. **Sanity check username**: tolak kalau mengandung spasi (slug akun
   platform manapun tidak pernah ada spasi) — menangkap bug nyata yang sudah
   terdokumentasi (`"TRUTH PrevaiL"` untuk akun Facebook, lihat memory
   `project_ollama_websearch_quality`). Berlaku untuk SEMUA provider
   (Anthropic/OpenAI/Ollama), tanpa risiko menolak akun valid (slug asli
   memang tidak pernah ada spasi).
2. **Grounding check** (parameter baru `search_text: str | None`): username
   HARUS literal muncul (case-insensitive) di teks hasil pencarian nyata,
   kalau tidak — ditolak sebagai kemungkinan halusinasi. Menangkap bug nyata
   kedua yang terdokumentasi (topik "AI Disinformation dalam Pemilu
   Indonesia" dikaitkan ke `dior_indonesia`, brand fashion yang sama sekali
   tidak disebut di hasil pencarian).
   - **Cuma aktif untuk provider Ollama** (`_find_via_ollama`, satu-satunya
     provider yang melakukan web search secara manual/dieksekusi kode kita
     sendiri sehingga teksnya bisa dikumpulkan — `accumulated_search_text`
     diisi tiap kali `_web_search()` dipanggil, digabung jadi satu string
     saat validasi).
   - **Sengaja TIDAK aktif kalau `has_web_search=False`** (FIRECRAWL/TAVILY
     key kosong) — `search_text=None` membuat lapis ini di-skip total,
     supaya tidak menolak SEMUA hasil cuma karena tidak ada teks pencarian
     untuk dicocokkan (`has_web_search=False` sudah punya jalur sendiri:
     model diberi tahu eksplisit "tidak ada akses internet, skor rendah" —
     keputusan user sebelumnya "lebih baik ada topik meski kadang salah,
     daripada 0" tetap dihormati untuk skenario ini).
   - **Sengaja TIDAK diaktifkan untuk Anthropic/OpenAI** — kedua provider itu
     TIDAK pernah terbukti berhalusinasi di produksi (masalah kualitas yang
     terdokumentasi CUMA dari qwen3:8b/Ollama); Anthropic pula pakai
     web_search bawaan yang dieksekusi server-side Anthropic sendiri (teks
     hasil pencariannya tidak mudah diakses kode kita untuk dicocokkan tanpa
     kerja tambahan) — tidak ada bukti butuh, jadi tidak ditambahkan
     (menghindari kerja/risiko yang tidak perlu).

Pesan balik ke model juga diperbaiki: dulu selalu bilang
`"Diterima N topik"` (N = jumlah yang DIKIRIM model, bukan yang LOLOS
validasi) — sekarang `"Diterima X dari N topik (sisanya ditolak: ...)"` biar
akurat kalau nanti dicek lewat log.

**Diuji standalone** (5 skenario, tanpa dependency lain): akun grounded+valid
diterima, akun ter-hallucinated (persis kasus dior_indonesia) ditolak, username
berspasi (persis kasus TRUTH PrevaiL) ditolak, `search_text=None` cuma jalankan
sanity+platform filter (tidak menolak semua), item dgn akun campuran
grounded+tidak-grounded menyisakan cuma yang grounded. Semua lolos.
Syntax-check file penuh juga lolos.

**Yang SENGAJA tidak diubah** (di luar scope "kualitas", bukan bug): pesan
`"Diterima X dari N"` tidak memicu model untuk retry submit di iterasi yang
sama (loop tetap `break` setelah submit_calls pertama, sesuai desain lama
"cukup satu putaran submit") — mengubah ini butuh pertimbangan lebih matang
soal risiko infinite-loop/durasi run (Ollama ~5.4 token/detik di server ini,
1 run bisa 4-10 menit), tidak masuk scope perbaikan validasi kali ini.

**Belum bisa diverifikasi live** (butuh 1x siklus Celery Beat berikutnya,
~07:00 WIB besok, ATAU trigger manual `POST /instagram/trend-scrape/run`
setara — belum dijalankan sesi ini, tunggu keputusan user kapan mau tes).

## A — Detail implementasi (2026-07-09)

Dipilih: **hapus section-nya total** (bukan relabel/ganti sumber) — karena
data live yang setara SUDAH ADA di section lain (`instagram_trend_scrape`,
sumber `get_trend_scrape_summary()`), jadi tidak ada informasi yang hilang,
cuma bagian yang mati/menyesatkan yang dibuang.

**2 file diubah:**
1. `app/api/v1/youtube/router.py` (`GET /youtube/monitor-public`) — hapus
   query `ig_trending_rows`/`ig_last_discovery`/`ig_last_err_row` (semua baca
   dari `instagram_trending_accounts`) dan key `"instagram.trending"` dari
   respons JSON. Field `"instagram.total_posts"`, `"total_comments"`,
   `"accounts_scraped_today"` (sumbernya tabel `posts`/`comments`, GENUINELY
   live) TETAP ADA, tidak dihapus. `last_err_at` (dipakai status
   `ensemble_data`) disederhanakan jadi cuma dari `scrape_runs` (`ed_err_at`)
   — sumber live, tidak kehilangan cakupan nyata (tabel dorman itu toh sudah
   tidak pernah update sejak 6+ hari, kontribusinya ke deteksi "error
   terkini" sudah nol secara efektif).
2. `app/main.py` (dashboard HTML `/scraping-status`) — section "Instagram
   Trending" (title, 3 card mati: Akun Trending/Last Discovery/Jadwal, dan
   tabel rank/username/trending-score/dst yang PERSIS menampilkan 5 baris
   seed palsu) dihapus. 3 card yang datanya genuinely live (Total Posts,
   Total Komentar, Scrape Hari Ini) dipertahankan di bawah judul baru
   "Instagram — Statistik". JS yang membaca elemen yang dihapus
   (`ig-accounts`/`ig-discovery`/`ig-table`) ikut dihapus. CSS `.pill-ig-rank`
   yang jadi mati ikut dibuang (`.pill-waiting` DIPERTAHANKAN, masih dipakai
   section lain).

**Ini kemungkinan besar section PERSIS yang dimaksud user sebagai "data
dummy"** — kartu "Akun Trending"/"Last Discovery" + tabel dengan kolom
"Trending Score"/"Engagement" berisi 5 akun (`tukang_jelajah`, `radityadika`,
`awkarin`, `nihongo_mantappu`, `benartajam`) dengan angka yang terlihat
presisi/meyakinkan tapi sebenarnya seed statis sejak 2026-07-02, tidak pernah
berubah — visual paling mencolok dibanding masalah B (data live tapi basi,
kurang terlihat "dummy" secara visual, lebih terlihat "kosong").

**Diverifikasi**: syntax-check Python (`ast.parse`) lolos untuk kedua file;
JS yang di-embed diekstrak dari `<script>` block lalu dicek `node --check`
(lolos) — konsisten dengan cara verifikasi dashboard di sesi-sesi sebelumnya
(lihat memory project_phase_status). Grep memastikan tidak ada sisa referensi
ke `igt.`/`ig.trending`/elemen HTML yang sudah dihapus.

**Tabel `instagram_trending_accounts` itu sendiri TIDAK dihapus dari
database** — cuma tidak dibaca lagi oleh endpoint ini. Kalau mau benar-benar
di-drop, itu keputusan terpisah (destruktif, perlu konfirmasi eksplisit,
belum diminta).

**Belum di-deploy ke server produksi** — cuma perubahan lokal, menunggu
keputusan user kapan mau deploy (lihat [[feedback_git_workflow]]: fitur
app/ real biasanya push+deploy rutin, tapi untuk sesi campuran begini lebih
aman ditanya dulu momen deploy-nya, terutama karena masih ada B yang perlu
konfirmasi terpisah).

## B — Detail implementasi (2026-07-09, dikonfirmasi eksplisit user)

File **FROZEN** yang diubah (izin eksplisit diminta & didapat sebelum edit,
sesuai [[feedback_trend_recommendations_frozen]]):
`app/services/instagram_trending/trend_scrape_service.py`.

Perubahan (mirroring PERSIS pola yang sudah terbukti bekerja di Facebook
`run_daily_trend_scrape_facebook()` dan TikTok):
1. `run_daily_trend_scrape()`: setelah tiap percobaan scrape gagal & topik
   masih `pending`, panggil `mark_failed_permanent_if_exhausted(db, topic)`
   (`app/services/trend_recommendations/service.py`) — kalau topik itu sudah
   gagal >= 3x (`FAILED_PERMANENT_THRESHOLD`, dihitung dari `scrape_runs`,
   lintas platform), status berubah jadi `failed_permanent` dan otomatis
   tidak lagi kepilih query `WHERE status='pending'` manapun.
2. `get_trend_scrape_summary()`: ditambah pelacakan `failed_permanent` —
   `summary.failed_permanent_with_instagram_account` (hitungan) dan
   `failed_permanent_topics` (daftar), sama pola dengan
   `get_facebook_trend_scrape_summary()`. Tanpa ini, efek fix di atas jadi
   tidak terlihat di monitoring manapun (`GET /instagram/trend-scrape/status`,
   dashboard publik).
3. Docstring modul (alur harian) dan docstring `GET /instagram/trend-scrape/status`
   (`app/api/v1/instagram/router.py`, bukan frozen) diperbarui supaya tidak
   menyesatkan — sebelumnya bilang "gagal TETAP pending, dicoba lagi
   selamanya" yang sekarang tidak akurat lagi.

**Efek nyata yang diharapkan**: 3 topik pending yang menunjuk akun
`coldplay_jakarta` (sudah gagal scrape 3+ hari berturut-turut per data
`scrape_runs` yang dicek live) akan ditandai `failed_permanent` pada
percobaan berikutnya yang gagal (siklus 09:00 WIB berikutnya, atau trigger
manual `POST /instagram/trend-scrape/run`) — membebaskan slot budget harian
Instagram dari 2/5 efektif menjadi 5/5 untuk topik baru.

**Diverifikasi**: syntax-check (`ast.parse`) lolos untuk kedua file. Kode
membaca `topic.status` SETELAH proses scrape (baris yang sama dengan versi
Facebook) — kalau baru saja berhasil (`status="used"`), blok
`if topic.status == "pending"` otomatis tidak jalan, konsisten dengan desain:
cuma topik yang MASIH pending (gagal) yang dicek jatah kegagalannya.

**Belum diverifikasi LIVE** (belum di-deploy ke server, belum ada siklus
scrape baru yang jalan dengan kode ini) — rencana verifikasi: deploy, tunggu
siklus 09:00 WIB atau trigger manual, cek `scrape_runs`+`trend_recommendations`
apakah 3 topik `coldplay_jakarta` genuinely berubah jadi `failed_permanent`
setelah percobaan berikutnya.
