# Redesain Metode Pencarian Threads — Skema & Arsitektur

Status: **SEBAGIAN BERJALAN — Fase 0 SELESAI+deploy+live-verified
2026-07-20.** Lihat §13 utk rencana bertahap fase berikutnya.

## 0. Prinsip desain (batasan yang WAJIB dipegang)

1. **Additive only, tidak ada yang dihapus.** 30 post Threads yang sudah ada
   sekarang TETAP ada, tidak ada migrasi destruktif, tidak ada drop
   kolom/tabel. Semua perubahan skema = kolom baru (nullable) atau tabel
   baru — sesuai instruksi eksplisit user ("data yang ada sesuaikan saja
   yg ada sekarang jgn dihapus").
2. **Nama endpoint API yang sudah ada TIDAK berubah** (`GET/POST
   /threads/search`, `GET /threads/posts/{id}`, `POST
   /threads/trend-scrape/run`, `GET /threads/trend-scrape/status`, `GET
   /threads/monitor`) — isi/logika di baliknya yang disesuaikan ke desain
   baru. Endpoint BARU cuma ditambah kalau memang belum ada kapasitasnya
   sama sekali (lihat §9).
3. **Satu skema tabel `posts`/`comments`/`lexicon_analyses`/`scrape_runs`
   dipakai bersama SEMUA platform** (bukan tabel khusus Threads) — supaya
   semua data "sama" seperti yang diminta, dan dashboard lintas-platform
   tidak perlu logic terpisah.
4. **Tidak menyenggol kode platform lain.** Semua worker/service Threads
   tetap terpisah total (pola yang sudah ada sejak awal).
5. **Jujur soal keterbatasan API pihak ketiga** — beberapa hal yang
   diminta (views, reach, daftar nama akun yang me-repost) **tidak
   tersedia** dari EnsembleData untuk post Threads milik orang lain (lihat
   §7). Desain ini tidak akan mengklaim kemampuan yang tidak benar-benar
   bisa dipenuhi.

---

## 1. Skema data

### 1.1 Tabel yang DIPAKAI ULANG (tidak berubah struktur)

| Tabel | Peran untuk Threads |
|---|---|
| `posts` | 1 baris = 1 post Threads. `platform='threads'`, `external_id`=pk post, `metadata` JSONB nampung likes/replies/reposts/quotes/author info tambahan, `media` utk gambar/video, `metrics` JSONB opsional utk histori angka. |
| `comments` | 1 baris = 1 balasan (top-level ATAU sub-balasan — dibedakan lewat kolom baru, lihat §1.2). `post_id` FK ke post induk (root), `author`=siapa yang membalas. |
| `lexicon_analyses` | Hasil klasifikasi sentimen per komentar (lexicon + LLM Sentiment Agent) — `comment_id` FK, `final_label` dipakai kalau sudah direview LLM. |
| `scrape_runs` | 1 baris = 1 percobaan scrape (sukses/gagal), `platform='threads'`, `keyword_text`, `triggered_by` (celery_beat/manual_cli/topic_search/queue_retry — nilai baru ditambah, lihat §4). |
| `trend_recommendations` | READ-ONLY (frozen), sumber topik utk jalur otomatis #2 (lihat §4). |
| `search_topics` / `search_topic_keywords` | READ-ONLY dari sisi Threads, sumber topik utk jalur otomatis #3 BARU (lihat §4). |

### 1.2 Kolom BARU (additive, nullable — tidak mengganggu data lama)

```sql
-- Migrasi baru, additive only
ALTER TABLE comments ADD COLUMN parent_comment_id UUID NULL
  REFERENCES comments(id) ON DELETE CASCADE;
CREATE INDEX ix_comments_parent_comment_id ON comments(parent_comment_id);
```

- `parent_comment_id IS NULL` → balasan **top-level** (langsung ke post pemilik).
- `parent_comment_id = <id balasan lain>` → **sub-komentar** (balasan ke
  balasan), sekarang bisa dikelompokkan LEWAT ID (bukan cuma
  `metadata.reply_to` yang isinya nama akun, rawan salah cocok kalau ada
  nama kembar).
- Kolom ini nullable dan tidak menyentuh baris lama sama sekali — semua
  komentar Threads/platform lain yang sudah ada otomatis `NULL` (dianggap
  top-level, tidak berubah perilaku).
- Field `metadata.reply_to` (nama akun) tetap dipertahankan sebagai
  cadangan/audit, TIDAK dihapus.

### 1.3 Tabel BARU: antrian pencarian tertunda

```sql
CREATE TABLE threads_search_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    keyword_text VARCHAR(255) NOT NULL,
    source VARCHAR(30) NOT NULL,      -- 'manual' | 'trend_recommendation' | 'topic_search'
    source_ref_id UUID NULL,          -- id trend_recommendations / search_topics kalau ada
    status VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending | done | failed_permanent
    attempts INT NOT NULL DEFAULT 0,
    last_error TEXT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ NULL
);
CREATE INDEX ix_threads_queue_status ON threads_search_queue(status, requested_at);
```

Ini yang menjawab permintaan **"jika pencarian pending oleh worker atau
kuota tidak ada maka dia akan ke kuota utama atau menunggu"** — lihat
alur lengkap di §3.

### 1.4 Kolom `metadata` post Threads — bentuk standar (schema JSON internal)

Supaya "semua data sama" seperti diminta, `posts.metadata` utk
`platform='threads'` distandarkan bentuknya:

```jsonc
{
  "likes": 0,
  "replies": 0,
  "reposts": 0,
  "quotes": 0,
  "views": null,          // SELALU null -- lihat keterbatasan §7
  "reach": null,           // SELALU null -- lihat keterbatasan §7
  "author_full_name": "...",
  "author_verified": false,
  "author_follower_count": null,   // best-effort, kalau EnsembleData kirim
  "reposted_by": []        // SELALU kosong -- lihat keterbatasan §7
}
```

Field yang secara jujur TIDAK BISA diisi (`views`, `reach`,
`reposted_by`) tetap DITULIS dengan nilai `null`/`[]` eksplisit (bukan
dihilangkan) — supaya frontend/dashboard tahu pasti "memang tidak
tersedia", bukan lupa diambil.

---

## 2. Alur pencarian bertingkat (tier), berlaku utk SEMUA jalur trigger

Pola ini SAMA persis dengan yang sudah dipakai `topic_search.py` (tier-1
DB → tier-3 antrian), diterapkan konsisten ke Threads:

```
1. TIER 1 -- Cek database dulu
   Ada post dgn keyword ini yg di-collect < N jam terakhir (default 24 jam,
   bisa diatur)? -> pakai data itu, TIDAK panggil EnsembleData sama sekali.

2. TIER 2 -- Data tidak ada / sudah basi -> cek kapasitas worker
   - Kalau slot task Threads yg sedang berjalan BELUM penuh (`threads_max_concurrent_jobs`,
     default 2) -> jalankan pencarian LANGSUNG (real-time, seperti sekarang).
   - Kalau slot PENUH -> masuk `threads_search_queue` (status='pending'),
     TIDAK di-drop, akan diproses tick berikutnya.

3. TIER 3 -- Eksekusi (langsung atau dari antrian)
   - Coba search via pool token (rotasi otomatis, lihat §3).
   - SUKSES -> simpan ke posts/comments (lewat AI Cleaning Agent dulu,
     §8), tandai queue 'done' kalau asalnya dari antrian.
   - GAGAL krn SEMUA token exhausted -> JANGAN gagal permanen, biarkan
     status 'pending' di `threads_search_queue` (retry tick berikutnya,
     max attempts sebelum 'failed_permanent' spy tidak nyangkut selamanya).
   - GAGAL krn alasan lain (0 hasil, dll) -> tandai 'done' juga (bukan
     error, memang tidak ada datanya).
```

Task Celery Beat baru **`threads-queue-drain`** jalan tiap 10 menit,
ambil item `status='pending'` di `threads_search_queue` (FIFO, dibatasi
budget per tick spy tidak numpuk quota), coba eksekusi ulang.

---

## 3. Rotasi kuota otomatis + jatuh ke "kuota utama" / menunggu

Sudah ada pool 5 token (`ensembledata_pool`) — TIDAK dibangun ulang,
cuma diperjelas alurnya sesuai permintaan:

```
Panggilan EnsembleData masuk
  -> ambil daftar token: [token1..token5], urut yg BELUM ditandai exhausted dulu
  -> coba token pertama
       - 200 OK -> selesai, pakai hasil ini
       - 495 (quota habis) / 492 (email blm verifikasi) -> mark_exhausted(token, TTL 20 jam), lanjut token berikutnya
  -> SEMUA token exhausted?
       -> "kuota utama" (fallback tunggal .env `ENSEMBLE_DATA_API_TOKEN`) SUDAH otomatis
          ikut jadi anggota pool (ditambahkan 2026-07-20) -- jadi sudah tercakup,
          tidak perlu jalur terpisah lagi.
       -> Kalau BENAR-BENAR semua (termasuk fallback) habis -> request TIDAK gagal ke user,
          tapi masuk `threads_search_queue` (status='pending') -- otomatis dicoba lagi
          tiap 10 menit oleh `threads-queue-drain` sampai salah satu token pulih
          (reset harian) atau sampai batas attempts (mis. 48x = 8 jam) -> failed_permanent.
```

Ini yang menjawab **"jika kuota habis lakukan rotasi otomatis"** (sudah
ada) + **"jika pencarian pending oleh worker atau kuota tidak ada maka
dia akan ke kuota utama atau menunggu"** (bagian "menunggu" = antrian
otomatis di atas).

---

## 3.1 Temuan penting: `trend_recommendations.status` DIBAGI BERSAMA semua platform

Ditemukan live 2026-07-20 (query nyata ke tabel): kolom `status` di
`trend_recommendations` **BUKAN per-platform** — begitu SATU platform
(mis. Facebook, lewat `manual_facebook_search`) berhasil scrape topik
"jampidsus" dan menandainya `status='used'`, topik itu **HILANG dari
daftar `pending` utk SEMUA platform lain** (Threads, TikTok, Twitter,
dst), walau platform lain itu belum pernah mencobanya sama sekali.
Dicek langsung: `raw_payload` tidak menyimpan info platform mana yang
sudah pakai topik itu.

**Solusi TANPA menyentuh tabel `trend_recommendations` (FROZEN, lihat
[[feedback_trend_recommendations_frozen]]) dan TANPA mengubah kode
platform lain sama sekali:**

```sql
-- Tabel pendamping BARU, terpisah total dari trend_recommendations
CREATE TABLE trend_recommendation_platform_usage (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trend_recommendation_id UUID NOT NULL REFERENCES trend_recommendations(id) ON DELETE CASCADE,
    platform VARCHAR(30) NOT NULL,   -- 'threads', dst
    used_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (trend_recommendation_id, platform)
);
```

- Threads (dan HANYA Threads utk sekarang) query topik pakai:
  `trend_recommendations` **TANPA filter `status='pending'`**, tapi
  filter `NOT EXISTS (SELECT 1 FROM trend_recommendation_platform_usage
  WHERE trend_recommendation_id = tr.id AND platform = 'threads')` —
  artinya: "topik yang belum pernah SAYA (Threads) coba", terlepas dari
  status global-nya.
- Setelah Threads coba (berhasil ATAU tidak ketemu hasil), insert 1 baris
  ke tabel baru ini (BUKAN mengubah `trend_recommendations.status`).
- Facebook/TikTok/Twitter/Instagram/News **TIDAK PERLU DIUBAH SAMA
  SEKALI** — mereka tetap baca `status='pending'` seperti biasa, tidak
  tahu-menahu soal tabel baru ini. Kalau nanti platform lain MAU ikut
  pola per-platform ini, tinggal ditambahkan belakangan, TIDAK WAJIB
  sekarang.

---

## 4. Semua sumber trigger pencarian (5 jalur, endpoint nama TETAP)

| # | Jalur | Endpoint (nama tetap) | Frekuensi | `triggered_by` |
|---|---|---|---|---|
| 1 | Manual/API | `POST /threads/search?q=` | Kapan saja | `manual_cli` |
| 2 | Trend Recommendations | Beat: `threads-trend-recommendation-daily` | 1x/hari, 3 topik | `celery_beat` |
| 3 | Manual trigger jalur #2 | `POST /threads/trend-scrape/run` | Kapan saja | `manual_cli` |
| 4 | **BARU** — Topic Search | Beat: `threads-topic-search-sync` | Tiap topik baru/keyword baru masuk `search_topics`, otomatis ikut disearch Threads (kalau platform "threads" ada di `topic.platforms`) | `topic_search` |
| 5 | **BARU** — Antrian tertunda | Beat: `threads-queue-drain` | Tiap 10 menit | `queue_retry` |

Jalur #4 (topic-search) BARU: saat ini `search_topics` sudah mendukung
Threads sebagai salah satu platform (`_resolve_platforms()` sudah
termasuk `threads`), TAPI proses tier-1/tier-3 di `topic_search.py`
generik lintas-platform — untuk Threads spesifik, `search_threads_posts()`
dipanggil oleh queue Celery yang SAMA seperti sekarang (`process_confirmed_
search_queue_task`), TIDAK perlu worker terpisah, cukup pastikan fungsi
itu lewat alur tier §2 yang baru (DB cek dulu, dsb).

---

## 5. Top 20 & Recent 20 post per hari

**Keterbatasan penting**: EnsembleData Threads TIDAK expose endpoint
"trending/explore feed" (cuma `keyword search`, `user posts`, `post
replies` — sudah diverifikasi §connector.py). Jadi "Top 20" & "Recent 20"
**bukan hasil scrape baru**, tapi **query agregat dari data yang SUDAH
ada** di `posts` (hasil semua jalur di §4 digabung):

```sql
-- Top 20 (like_count tertinggi, sepanjang data tersimpan)
SELECT * FROM posts WHERE platform='threads'
ORDER BY (metadata->>'likes')::int DESC NULLS LAST LIMIT 20;

-- Recent 20 (published_at terbaru)
SELECT * FROM posts WHERE platform='threads'
ORDER BY published_at DESC NULLS LAST LIMIT 20;
```

Dijadwalkan sebagai task ringan `threads-daily-top-recent-snapshot`
(1x/hari) yang cuma menyimpan HASIL QUERY ini (bukan scrape baru) ke
`scrape_runs` sbg snapshot log (`api_source='internal_aggregate'`) —
supaya ada histori "Top 20 hari ini vs kemarin" tanpa boros kuota
EnsembleData sama sekali. Endpoint baru: `GET /threads/top-recent`
(lihat §9).

---

## 6. Balasan lengkap + sub-komentar

- Post → balasan top-level → sub-balasan (balasan ke balasan) — 3 level,
  disimpan SEMUA di tabel `comments` yang sama, dibedakan
  `parent_comment_id` (§1.2).
- **Keterbatasan jujur** (sudah didokumentasikan di connector.py sejak
  2026-07-19): 1x panggilan `/threads/post/replies` TERBUKTI cuma
  mengembalikan SEBAGIAN balasan kalau jumlahnya banyak (tidak ada
  cursor/pagination yang terbukti berfungsi). Desain baru: coba kirim
  parameter `depth` bertingkat (1, 2, 3...) best-effort, gabung+dedup
  hasil by `pk` balasan, TAPI status akhir tetap jujur dilaporkan --
  field baru `posts.metadata.replies_coverage` = `"complete"` |
  `"partial"` (dibandingkan `direct_reply_count` asli vs jumlah
  tersimpan), supaya dashboard bisa tampilkan "28 dari 58 balasan
  tersimpan" alih-alih pura-pura lengkap.

---

## 7. Metrik: yang BISA vs TIDAK BISA dipenuhi

| Diminta | Status | Keterangan |
|---|---|---|
| Likes | ✅ Bisa | `metadata.likes`, sudah ada. |
| Jumlah balasan | ✅ Bisa | `metadata.replies` + hitung asli dari tabel `comments`. |
| Repost count (angka) | ✅ Bisa | `metadata.reposts`, sudah ada. |
| Views per post | ❌ TIDAK BISA | Threads tidak expose view-count publik per post pihak lain (beda dari TikTok/YouTube) — API resmi maupun EnsembleData tidak menyediakan ini utk akun bukan milik sendiri. |
| Jangkauan (reach) | ❌ TIDAK BISA | Reach adalah metrik Insights, HANYA terlihat oleh pemilik akun sendiri di aplikasi resminya — tidak ada API pihak ketiga yang bisa mengaksesnya utk akun orang lain. |
| Siapa yang me-repost | ❌ TIDAK BISA (saat ini) | EnsembleData cuma kasih angka repost, TIDAK ada endpoint daftar identitas yang repost. Kalau nanti ketemu endpoint yang expose ini, baru bisa ditambah — belum ada di API yang terverifikasi. |
| Siapa pemilik post/komentar/sub-komentar | ✅ Bisa | `posts.author`, `comments.author`, dikaitkan `post_id`+`parent_comment_id`. |

Field yang "TIDAK BISA" tetap disimpan sbg `null`/`[]` eksplisit (§1.4),
bukan pura-pura ada. Kalau di kemudian hari EnsembleData menambah endpoint
baru yang mendukung ini, tinggal isi field yang sudah disiapkan, TANPA
migrasi skema lagi.

---

## 8. AI Cleaning/Filtering Agent (BARU, bisa ON/OFF)

Agent baru (mirip pola Sentiment Agent yang sudah ada) yang berjalan
SEBELUM data masuk `posts`/`comments`:

- **Fungsi**: filter spam/post tidak relevan/duplikat mendekati
  (near-duplicate) sebelum simpan — pakai LLM (OpenRouter, model
  hemat/gratis) menilai tiap batch hasil scrape: "relevan dgn keyword
  ini?" (ya/tidak) + cek kemiripan konten dgn post yg sudah tersimpan.
- **Toggle ON/OFF** — `app/services/threads_cleaning_agent/config.py`
  (`get/set_enabled()`, Redis, pola SAMA persis dgn
  `sentiment_agent/config.py`).
- **Kalau OFF** — semua hasil scrape masuk apa adanya (perilaku SAMA
  seperti sekarang, tidak ada perubahan).
- **Kalau ON** — tiap post/komentar baru dicek dulu, yang ditolak
  TETAP disimpan di `scrape_runs.error_message`/log (bukan hilang tanpa
  jejak) dgn tag `[FILTERED]`, supaya bisa diaudit.
- **Pengaturan di halaman `/scraping-status`** — section baru "Threads
  Cleaning Agent" persis pola section lain (status ON/OFF, pilih
  model, token OpenRouter sendiri via kredensial native, tanpa
  restart).

---

## 9. Sentiment Agent — perluasan ke platform Threads

Sentiment Agent (LLM opini kedua utk lexicon) SAAT INI baru cover
`platform='youtube'`. Perluasan:

- Query kandidat review ditambah `OR p.platform = 'threads'` (satu baris
  perubahan filter, tidak mengubah cara kerja intinya).
- Karena sekarang ada `parent_comment_id`, hasil klasifikasi bisa
  dikelompokkan per POST → per KOMENTAR TOP-LEVEL → per SUB-KOMENTAR
  dengan jelas lewat rantai `posts.id -> comments.post_id ->
  comments.parent_comment_id -> comments.id` — memudahkan agent
  pengelompokan negatif/positif per level yang diminta.
- Tidak perlu tabel baru — `lexicon_analyses.comment_id` sudah cukup,
  tinggal ikutkan `comments.parent_comment_id` saat query/agregasi di
  endpoint dashboard.

---

## 10. Endpoint — nama tetap, isi baru, endpoint baru seperlunya

| Endpoint | Status | Perubahan |
|---|---|---|
| `GET /threads/search` | Nama TETAP | Isi diarahkan ke Tier-1 (§2) — baca DB, TIDAK auto-scrape sinkron. |
| `POST /threads/search` | Nama TETAP | Masuk alur tier §2 penuh (cek DB dulu → cek slot worker → antre kalau penuh). |
| `GET /threads/posts/{id}` | Nama TETAP | Tambah balasan bersarang (`parent_comment_id`) di response, `replies_coverage` ikut ditampilkan. |
| `POST /threads/trend-scrape/run` | Nama TETAP | Tidak berubah signature, isi ikut alur tier+antrian baru. |
| `GET /threads/trend-scrape/status` | Nama TETAP | Tambah info `queue_pending_count` dari `threads_search_queue`. |
| `GET /threads/monitor` | Nama TETAP | Tambah ringkasan cleaning agent + antrian. |
| `GET /threads/top-recent` | **BARU** | §5 — Top 20 & Recent 20, query agregat. |
| `GET/POST /threads/cleaning-agent/config` | **BARU** | §8 — toggle ON/OFF + pengaturan model. |

---

## 11. Ringkasan migrasi (semua additive, aman)

1. ✅ **SELESAI** — Migrasi 025: `comments.parent_comment_id` (nullable)
   + index, tabel `threads_search_queue`. Deploy+live-verified
   2026-07-20, 30 post lama tetap utuh.
2. **BELUM** — Migrasi baru: tabel `trend_recommendation_platform_usage`
   (§3.1).
3. **BELUM** — Redis config baru: `threads_cleaning_agent:*` (enabled,
   model, api_key), `threads_search:max_concurrent_jobs`,
   `threads_search:cache_freshness_hours`.
4. **BELUM** — Beat schedule baru: `threads-queue-drain` (10 menit),
   `threads-daily-top-recent-snapshot` (1x/hari).
5. **BELUM** — Sentiment Agent: 1 baris filter query ditambah
   `platform='threads'`.
6. TIDAK ADA endpoint yang dihapus/diganti nama. TIDAK ADA data
   dihapus.

---

## 12. Konfirmasi (SUDAH diputuskan 2026-07-20, pakai default)

1. Freshness cache Tier-1: **24 jam**.
2. `threads_max_concurrent_jobs`: **2**.
3. Model LLM Cleaning Agent: sama dgn Discovery Agent (OpenRouter
   gratis).
4. Retry max `threads_search_queue`: **48x (~8 jam, tiap 10 menit)**.

---

## 13. Rencana bertahap (fase implementasi)

Dipecah supaya tiap fase bisa selesai+deploy+verifikasi sendiri-sendiri
(titik aman berhenti kapan saja), tidak ada yang menggantung setengah
jalan.

| Fase | Isi | Risiko/Kompleksitas | Status |
|---|---|---|---|
| **0** | Migrasi dasar: `parent_comment_id` + `threads_search_queue` | Rendah | ✅ **SELESAI** 2026-07-20 |
| **1** | Alur tier pencarian (§2): cek DB dulu → cek slot worker → jalan/antre. Ubah isi `GET/POST /threads/search` (nama endpoint tetap). | Sedang — inti logika baru | ✅ **SELESAI** 2026-07-21 |
| **2** | Worker `threads-queue-drain` (proses `threads_search_queue` tiap 10 menit) + config `max_concurrent_jobs`/`cache_freshness_hours` | Sedang | ✅ **SELESAI** 2026-07-21 |
| **3** | Migrasi `trend_recommendation_platform_usage` (§3.1) + ubah query `threads_trend_recommendation_daily_task` supaya Threads tidak lagi "kehabisan" topik yang sudah dipakai platform lain | Rendah (additive, cuma Threads yg berubah) | ✅ **SELESAI** 2026-07-21 |
| **4** | Integrasi topic-search (§4 jalur #4) — pastikan `search_threads_posts()` yg dipanggil dari antrian topic-search ikut lewat alur tier Fase 1 | Rendah (reuse Fase 1) | Belum |
| **5** | `GET /threads/top-recent` (Top 20 & Recent 20, query agregat, §5) + snapshot harian | Rendah | Belum |
| **6** | Sentiment Agent: extend ke `platform='threads'` (1 baris filter) | Sangat rendah | Belum |
| **7** | AI Cleaning/Filtering Agent (§8) — LLM filter sebelum simpan, toggle ON/OFF di `/scraping-status` | Tinggi — fitur baru paling besar, butuh testing paling banyak | Belum |
| **8** | Update dashboard `/threads/monitor` + `/scraping-status`: tampilkan status antrian, cleaning agent, `replies_coverage` | Rendah | Belum |

**Urutan pengerjaan disarankan**: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 (tiap
fase saling membangun di atas fase sebelumnya, kecuali Fase 6 yang bisa
dikerjakan kapan saja/independen kalau mau diselipkan lebih awal karena
sangat kecil).
