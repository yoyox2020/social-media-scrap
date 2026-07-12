# Smart Search — Contoh Alur Konfirmasi + Verifikasi (Next.js)

**Dokumentasi lengkap (diagram alur, semua API, logika, pemetaan ke UI
existing) ada di [`FLOW.md`](./FLOW.md) — baca itu dulu.** File ini cuma
ringkasan singkat + cara menjalankan ulang verifikasi kalau perlu.

Referensi implementasi frontend untuk `POST /search/topics` (lihat
`app/api/v1/topic_search.py`). Bukan project Next.js yang bisa langsung
`npm run dev` — cuma file untuk disalin ke project Next.js yang sudah
ada (`lib/smart-search-api.ts` + `components/TopicSearchBox.tsx`).

Sudah dibuktikan jalan lewat 2 jalur terpisah: (1) `TopicSearchBox.tsx`
sempat di-render lewat Next.js dev server sungguhan via proxy server-side
(compile 0 error, token TIDAK PERNAH masuk bundle browser -- di-scan
manual, 0 kecocokan), dan (2) seluruh alur
search→needs_confirmation→confirm→queued→polling dijalankan end-to-end
lewat `simulate-flow.ts` melawan API produksi -- video YouTube & artikel
berita asli ditemukan (8 detik s/d ~88 detik tergantung platform).
Ditemukan juga 1 bug nyata lewat pengujian manual di browser (polling
tidak bisa membedakan "masih dicari" dari "sudah selesai dicari, hasil
nol") -- sudah diperbaiki di `TopicSearchBox.tsx` (timeout 2 menit +
tombol "Hentikan pemantauan", lihat FLOW.md poin 5).

Scaffold Next.js-nya (package.json/node_modules/.next/app/) sudah
dihapus lagi setelah semua terbukti jalan -- cuma source reference yang
disisakan di folder ini.

## Cara verifikasi ulang sendiri (opsional)

```
npm install next react react-dom typescript @types/react @types/node --save-dev
# lengkapi app/layout.tsx + app/page.tsx yang import TopicSearchBox, lalu:
npm run dev
```

Atau tanpa browser sama sekali, langsung tes fungsi API-nya lewat terminal:
```
npm install --no-save tsx
DEMO_TOKEN=<token bearer asli> npx tsx simulate-flow.ts
```
**Jangan pernah taruh token di file `.env.local` dgn prefix `NEXT_PUBLIC_`**
-- itu bikin token ke-bundle ke JS yang dikirim ke browser (bisa dilihat
siapa pun yang inspect halaman). `simulate-flow.ts` sengaja baca token
dari env var biasa (`DEMO_TOKEN`), bukan `NEXT_PUBLIC_*`, dan dijalankan
di terminal/server, bukan di browser.

## Alur

1. **Cari** — `searchTopics(token, { topics, confirm_third_party: false })`.
   Backend verifikasi ke database dulu. Kalau ada keyword kosong, balas
   `needs_confirmation_keywords` — TIDAK ada panggilan Apify/Firecrawl
   sama sekali di tahap ini.
2. **Tampilkan dialog konfirmasi** — pakai daftar `needs_confirmation_keywords`
   dari response.
3. **User klik "Ya"** — kirim ULANG `searchTopics()` dengan payload
   `topics`/`keywords` **PERSIS SAMA**, cuma `confirm_third_party: true`.
   Backend verifikasi ULANG ke database (jaga-jaga kalau sudah ketemu dari
   proses lain) sebelum benar-benar mendaftarkan ke antrian pencarian.
4. **Status "queued"** — backend balas dalam hitungan milidetik, TIDAK
   menunggu hasil pencarian selesai (bisa 15–85+ detik per keyword×platform,
   diproses satu-per-satu di background).
5. **Polling** — panggil `getTopicDetail(token, topicId)` berkala (default
   tiap 8 detik) sampai semua keyword yang di-queue sudah punya
   `total_posts > 0`, atau timeout browser (2 menit) / user klik "Hentikan
   pemantauan". Timeout/stop TIDAK berarti pencarian di server gagal --
   bisa saja sudah selesai dgn hasil nol (genuinely tidak ketemu), lihat
   FLOW.md poin 5.

## Endpoint terkait

| Endpoint | Kegunaan |
|---|---|
| `POST /search/topics` | Cari topik baru ATAU topik dengan nama yang sudah ada (payload penuh: name+keywords) |
| `POST /search/topics/{id}/search` | Cari ulang topik yang SUDAH tersimpan, cukup `topic_id` (lihat `searchSavedTopic()` di lib) |
| `GET /search/topics/{id}` | Detail + polling progress |
| `GET /search/topics/list` | Daftar topik tersimpan (isi dropdown) |
| `GET /search/topics/keywords` | Semua keyword yang pernah dicari, lintas topik+platform, tanpa filter ID |
| `DELETE /search/topics/{id}` | Hapus topik (soft-delete, data post/keyword TETAP aman) |

## Kenapa verifikasi dua kali (bukan cuma percaya `confirm_third_party`)

Antara user melihat dialog konfirmasi sampai klik "Ya", bisa saja data itu
sudah keburu ada — dari user lain yang cari keyword sama, atau dari
pemindaian otomatis harian. Backend cek DB ULANG di request kedua sebelum
benar-benar keluar mencari, supaya tidak ada pencarian dobel yang
sia-sia/boros biaya.
