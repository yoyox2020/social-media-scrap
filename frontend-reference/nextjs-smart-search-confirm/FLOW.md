# Smart Search — Alur, API, dan Logika (Dokumentasi Integrasi)

Dokumen ini rujukan tunggal untuk mengintegrasikan Smart Search ke frontend
yang sudah ada (screen "Master Data Topik" + dropdown "Pilih topik..." +
tombol Search). Semua yang tertulis di sini SUDAH diuji langsung ke API
produksi (`https://api.dismi.xyz/api/v1`), bukan rancangan teoritis.

---

## 1. Ikhtisar

User ketik **topik** (nama) + satu atau lebih **keyword**. Sistem:

1. Cari dulu di database sendiri (gratis, instan).
2. Kalau tidak ketemu, **minta izin dulu** ke user sebelum keluar mencari
   ke pihak ketiga (Facebook/Instagram/TikTok/Twitter/YouTube/berita —
   semua berbayar/berkuota).
3. Kalau user setuju, pencarian ke pihak ketiga jalan **di background**
   (bukan menahan browser user), **satu keyword×platform per satu waktu**
   (berurutan, bukan bersamaan).
4. Hasil otomatis tersimpan ke database begitu ketemu — tinggal
   di-refresh/di-poll untuk melihatnya muncul.

---

## 2. Diagram Alur

```
┌──────────┐     1. POST /search/topics              ┌─────────┐
│ FRONTEND │ ───────────────────────────────────────▶ │ BACKEND │
│ (browser)│     { topics, confirm_third_party:false }│ (FastAPI)│
└──────────┘                                           └────┬────┘
                                                             │ cek DB
                                                             │ (tier-1, ILIKE
                                                             │  per-kata)
                              ┌──────────────────────────────┘
                              ▼
                    data ADA?  ──── YA ──▶ balas "found" + hasil
                              │
                              NO
                              ▼
                 balas "needs_confirmation"
                 (BELUM ada panggilan keluar apa pun)
                              │
┌──────────┐                 ▼
│ FRONTEND │  tampilkan dialog "Cari ke sumber luar?"
│          │  user klik "Ya"
└────┬─────┘
     │ 2. POST /search/topics (PAYLOAD SAMA PERSIS)
     │    { topics, confirm_third_party:true }
     ▼
┌─────────┐
│ BACKEND │── cek DB LAGI (verifikasi ulang, siapa tahu sudah ada) ──┐
└────┬────┘                                                          │
     │ masih kosong                                    sudah ada ────┘──▶ balas "found"
     ▼                                                  (batal ke luar)
 daftarkan ke Celery task (antrian)
 balas "queued" INSTAN (~0.1-0.5 detik, TIDAK menunggu)
     │
     │  (di background, terpisah dari request user)
     ▼
┌──────────────────────────────────────────┐
│ Celery worker: proses SATU-PER-SATU       │
│  keyword1 × platformA → Apify/Firecrawl/  │
│  keyword1 × platformB → YouTube Data API  │
│  keyword2 × platformA → ...               │
│  (berurutan, bisa 8 detik s/d 90+ detik   │
│  PER item tergantung platform)            │
│  → hasil tersimpan ke tabel `posts`       │
└──────────────────────────────────────────┘

┌──────────┐  3. GET /search/topics/{id}  (polling tiap beberapa detik)
│ FRONTEND │ ───────────────────────────────────────▶ balas data terkini
│          │  ulangi sampai total_posts > 0 / user berhenti nunggu
└──────────┘
```

---

## 3. Semua API yang Dipakai

| # | Method + Path | Kegunaan | Dipakai di UI mana |
|---|---|---|---|
| 1 | `POST /search/topics` | Cari (dan opsional simpan) topik BARU atau topik dengan nama yang sudah ada — payload penuh (name+keywords) | Form "Master Data Topik" (`save_topic:true, auto_crawl` default true, `confirm_third_party:false`) DAN search box bebas |
| 2 | `POST /search/topics/{topic_id}/search` | Cari ulang topik yang **SUDAH tersimpan**, cukup kirim `topic_id` | Dropdown "Pilih topik..." + tombol Search |
| 3 | `GET /search/topics/{topic_id}` | Detail satu topik: semua keyword + post + sentimen. Dipakai jadi **target polling** setelah confirm | Klik topik di dashboard, dan polling pasca-konfirmasi |
| 4 | `GET /search/topics/list` | Daftar semua topik tersimpan (buat isi dropdown + list "Master Data Topik") | Kedua screen |
| 5 | `GET /search/topics/keywords` | Semua keyword yang PERNAH dicari, lintas topik+platform, tanpa perlu filter ID apa pun | Screen "riwayat pencarian" (kalau ada) |
| 6 | `POST /search/topics/{topic_id}/schedule` | Aktif/nonaktifkan pemindaian otomatis harian | Toggle "pantau otomatis" di detail topik |
| 7 | `DELETE /search/topics/{topic_id}` | Hapus topik (soft-delete — data post/keyword TETAP aman) | Ikon tong sampah |

Base URL: `https://api.dismi.xyz/api/v1`. Semua butuh header
`Authorization: Bearer <token>` — **JANGAN taruh token di kode frontend
langsung**, lihat bagian 6.

---

## 4. Detail Tiap Endpoint

### 4.1 `POST /search/topics` — cari/simpan topik

**Request** (contoh nyata, sudah diuji):
```json
{
  "topics": [{ "name": "Riset Kemacetan Jakarta", "keywords": ["macet", "lampu merah"] }],
  "platforms": [],
  "save_topic": true,
  "confirm_third_party": false
}
```
- `platforms` kosong/tidak dikirim = otomatis SEMUA platform (facebook, instagram, news, tiktok, twitter, youtube).
- `confirm_third_party` **SELALU `false` di percobaan pertama**.

**Response — kasus data BELUM ada** (`status: "needs_confirmation"`):
```json
{
  "success": true,
  "data": {
    "status": "needs_confirmation",
    "needs_confirmation_keywords": ["lampu merah"],
    "note": "Keyword dengan status 'needs_confirmation' tidak ditemukan di database. Kirim ulang request yang SAMA dengan confirm_third_party=true untuk mencari ke third-party.",
    "topics": [{
      "topic_id": "3f1a2b4c-...",
      "status_per_keyword": { "macet": "found", "lampu merah": "needs_confirmation" },
      "results": [{ "platform": "facebook", "title": "...post utk keyword \"macet\" yg sudah ketemu..." }]
    }]
  }
}
```

**Kirim ulang setelah user klik "Ya"** — payload **PERSIS SAMA**, cuma:
```json
{ "...": "...", "confirm_third_party": true }
```

**Response — sekarang antrian** (`status: "queued"`):
```json
{
  "success": true,
  "data": {
    "status": "queued",
    "queued_keywords": ["lampu merah"],
    "note": "Keyword dengan status 'queued' sedang dicari ke third-party SATU PER SATU di background..."
  }
}
```
Balasan ini **instan** (~0.1–0.5 detik) — TIDAK menunggu hasil pencarian.

---

### 4.2 `POST /search/topics/{topic_id}/search` — cari ulang topik tersimpan

Cocok untuk dropdown yang sudah tahu `topic_id` (dari `GET /search/topics/list`), tidak perlu kirim ulang name/keywords/platforms.

**Request:**
```json
{ "confirm_third_party": false, "limit_per_keyword": 10 }
```
**Response** sama bentuknya dengan 4.1, cuma scope-nya satu topik (tidak dibungkus array `topics`).

---

### 4.3 `GET /search/topics/{topic_id}` — polling hasil

Panggil berkala (disarankan tiap 8 detik) setelah dapat `status: "queued"`:
```json
{
  "success": true,
  "data": {
    "topic_id": "3f1a2b4c-...",
    "total_posts": 5,
    "keyword_details": [
      {
        "keyword": "lampu merah",
        "total_posts": 5,
        "posts": [ { "platform": "youtube", "title": "...", "url": "..." } ],
        "last_rescanned_at": "2026-07-12T07:41:18Z"
      }
    ]
  }
}
```
Kalau `total_posts` masih 0, berarti masih diproses di background — poll lagi nanti. `last_rescanned_at` berubah begitu item MULAI diproses (penanda "sedang jalan"), sebelum hasilnya benar-benar ada.

---

### 4.4 `GET /search/topics/list` — isi dropdown & daftar topik

```json
{
  "success": true,
  "data": {
    "total": 2,
    "items": [
      { "topic_id": "3f1a2b4c-...", "name": "Riset Kemacetan Jakarta", "keywords": ["macet","lampu merah"], "total_posts": 12 }
    ]
  }
}
```
`topic_id` di sini yang dipakai sebagai value tersembunyi tiap opsi dropdown.

---

### 4.5 `DELETE /search/topics/{topic_id}`
```json
{ "success": true, "data": { "message": "Topik 'Riset Kemacetan Jakarta' dinonaktifkan" } }
```
Soft-delete — `posts`/`keyword` yang sudah ditemukan TETAP ada di database, cuma topik-nya yang disembunyikan dari daftar.

---

## 5. Logika Penting (kenapa dirancang begini)

1. **Kenapa verifikasi database DUA KALI** (sebelum tanya user, DAN lagi setelah user klik "Ya")?
   Antara user melihat dialog sampai klik "Ya", bisa saja data itu sudah keburu ada (dari
   pencarian user lain, atau pemindaian otomatis harian). Cek ulang mencegah pencarian
   dobel yang sia-sia/boros biaya ke Apify/Firecrawl.

2. **Kenapa keyword multi-kata dicocokkan per-kata (AND), bukan frasa utuh?**
   Konten hasil scrape asli (artikel berita, caption medsos) nyaris tidak pernah
   mengandung frasa pencarian PERSIS berurutan sama, walau semua katanya memang ada.
   Contoh: keyword "kebakaran hutan kalimantan" akan cocok dengan artikel yang
   mengandung kata "kebakaran", "hutan", DAN "kalimantan" di mana pun posisinya —
   bukan harus berurutan persis begitu.

3. **Kenapa pencarian ke third-party TIDAK sinkron (harus antri/background)?**
   Satu panggilan ke Apify/Firecrawl/YouTube API bisa 8 detik sampai 90+ detik.
   Kalau ditunggu langsung di request HTTP yang sama, browser/reverse-proxy bisa
   timeout duluan sebelum selesai — apalagi kalau ada beberapa keyword sekaligus.

4. **Kenapa diproses SATU-PER-SATU (bukan sekaligus/paralel)?**
   Supaya beban ke tiap layanan pihak ketiga rata & mudah dilacak (satu baris
   `ScrapeRun` per keyword×platform di database), dan supaya kegagalan satu item
   (mis. satu platform error) tidak menghentikan/mengacaukan item lainnya.

5. **Kapan proses background "berhenti"?**
   Tidak ada batas waktu paksa dari kode di SERVER — task jalan sampai SEMUA
   item dalam antrian selesai diproses (sukses ATAU gagal-tercatat, keduanya
   dianggap "selesai"), lalu berhenti sendiri. **Penting:** "selesai" TIDAK
   sama dengan "ketemu" — pencarian bisa selesai sukses dengan hasil NOL
   (keyword itu genuinely tidak ada di platform tsb), itu bukan error.

   Polling di **frontend** (`components/TopicSearchBox.tsx`) punya batas
   tunggu sendiri **2 menit** (`POLL_TIMEOUT_MS`) + tombol **"Hentikan
   pemantauan"** supaya user bisa berhenti kapan saja — begitu salah satu
   tercapai, status pindah ke `"stopped"` (BUKAN `"queued"` selamanya).
   Ini murni soal browser berhenti bertanya, TIDAK membatalkan apa pun di
   server (server tidak bisa dibatalkan paksa di tengah jalan, tapi biasanya
   sudah keburu selesai duluan krn tiap item cuma perlu 8–90 detik). Kalau
   user buka lagi topik itu nanti, hasilnya (kalau ada) sudah tersimpan.

---

## 6. Pemetaan ke Frontend yang Sudah Ada

### Screen "Master Data Topik" (form Nama Topik + Keyword + tombol "Simpan Topik")
- Tombol **"Simpan Topik"** → `POST /search/topics` dengan `confirm_third_party:false`
  (biasanya `save_topic:true`). Kalau ada keyword yang butuh konfirmasi, tampilkan
  saja indikator "beberapa keyword belum ada data" — screen ini fokus SIMPAN
  definisi topik, bukan tempat dialog konfirmasi muncul (itu di screen pencarian).
- **Daftar Topik** (list + tag keyword + ikon hapus) → `GET /search/topics/list` +
  `DELETE /search/topics/{id}`.

### Screen dropdown "Pilih topik..." + tombol Search
- Dropdown diisi dari `GET /search/topics/list` (`name` ditampilkan, `topic_id`
  disimpan sbg value).
- Klik **Search** → `POST /search/topics/{topic_id}/search` dengan
  `confirm_third_party:false`.
- Kalau responsnya ada `needs_confirmation_keywords`, **di sinilah** dialog
  konfirmasi muncul ("Data X tidak ditemukan, cari ke sumber luar?").
- User klik "Ya" → panggil endpoint yang SAMA, `confirm_third_party:true`.
- Mulai polling `GET /search/topics/{topic_id}` sampai hasil muncul atau user
  pindah halaman.

---

## 7. Isi Folder Ini

| File | Fungsi |
|---|---|
| `lib/smart-search-api.ts` | Client API bertipe — **salin ini ke project frontend asli** (dipakai kalau frontend sudah punya sistem auth sendiri, token dikirim sbg parameter) |
| `components/TopicSearchBox.tsx` | Contoh komponen React memakai `lib/smart-search-api.ts` langsung (token dari prop) |
| `components/DemoTopicSearchBox.tsx` | Versi DEMO (dipakai `app/page.tsx`) — panggil proxy lokal `/api/search-topics*`, TIDAK butuh token di browser sama sekali |
| `app/api/search-topics/route.ts`, `app/api/search-topics/[topicId]/route.ts` | Proxy server-side utk demo — token dibaca dari `.env.local` (`DEMO_TOKEN`, TANPA prefix `NEXT_PUBLIC_`) |
| `simulate-flow.ts` | Skrip verifikasi via terminal (tanpa browser), jalankan: `DEMO_TOKEN=<token> npx tsx simulate-flow.ts` |
| `README.md` | Ringkasan singkat + cara menjalankan ulang demo/simulasi |
| `FLOW.md` (file ini) | Dokumentasi lengkap alur+API+logika+pemetaan UI |

**Folder `app/`, `package.json`, `node_modules/`, `.env.local` adalah scaffold
demo SEMENTARA** — aman dihapus kapan saja setelah tidak dibutuhkan lagi
(sudah dikonfirmasi berjalan). Yang perlu DIPERTAHANKAN untuk integrasi
sungguhan: `lib/`, `components/TopicSearchBox.tsx`, `FLOW.md`, `README.md`.


catatan deploymet rangkuman
ikhtisiar
1. Ikhtisar — ringkasan 4 langkah alur
Diagram alur (ASCII) — request/response, kapan konfirmasi muncul, kapan antrian jalan
2. Tabel 7 API yang dipakai + di UI mana masing-masing dipanggil
Detail tiap endpoint — request/response contoh nyata (dari hasil pengujian sesi ini, semua blok JSON sudah saya validasi bisa di-parse tanpa error)
3. 5 poin logika penting — kenapa verifikasi dua kali, kenapa pencocokan kata per-kata bukan frasa utuh, kenapa antrian bukan sinkron, kenapa berurutan bukan paralel, kapan proses background berhenti
4. Pemetaan langsung ke UI Anda — persis screen "Master Data Topik" dan dropdown "Pilih topik..." dari screenshot sebelumnya, tombol mana manggil endpoint yang mana
5. Peta isi folder — file mana yang perlu disalin ke project asli (lib/, TopicSearchBox.tsx, dokumentasi ini) vs file mana yang cuma scaffold sementara demo dan aman dihapus