# Trend Timeline — referensi frontend Next.js

Contoh integrasi **satu endpoint** `GET /trend-discovery/timeline` (lihat
[docs/trend-discovery-api.md](../../docs/trend-discovery-api.md)) menjadi
2 panel: **Word count** (ranking) + **Timeline** (grafik garis per hari).
Tidak ada endpoint lain yang dipanggil — satu response API dipakai untuk
kedua panel sekaligus.

**Kata-katanya OTOMATIS** — `keywords` sengaja TIDAK dikirim dari frontend.
Kosongkan param itu = API sendiri yang scan `posts.content`, hitung
frekuensi kata, buang stopword/noise, lalu pilih `top_n` kata paling sering
disebut di rentang tanggal yang diminta. Tidak ada daftar kata yang
di-hardcode di kode manapun di bawah ini.

---

## Struktur

```
lib/trend-api.ts              tipe TypeScript + fungsi fetch ke API
app/trend-timeline/
  page.tsx                    Server Component -- panggil fetchTrendTimeline()
  WordCountPanel.tsx           panel kiri (ranking, server-rendered)
  TimelineChart.tsx            panel kanan (Canvas, client component)
  styles.css                   styling kedua panel
```

## Setup

1. Copy folder `lib/` dan `app/trend-timeline/` ke project Next.js App
   Router kamu (Next.js 13+).

2. Tambahkan ke `.env.local`:
   ```
   TREND_API_BASE_URL=https://api.dismi.xyz
   TREND_API_TOKEN=<access token dari POST /auth/login>
   ```

   **Catatan penting soal token:** JWT access token ini PUNYA masa berlaku
   (expired), tidak selamanya valid. Untuk demo/internal dashboard, cukup
   perbarui `TREND_API_TOKEN` manual tiap kali expired. Untuk produksi
   jangka panjang, ganti `fetchTrendTimeline()` supaya login otomatis lewat
   `POST /auth/login` (email+password disimpan di env, bukan token
   statis) dan cache token-nya di memory server — pola yang sama seperti
   `login()` di `scripts/word_count_trending.py`.

3. Buka `/trend-timeline` di app kamu.

## Kenapa fetch dilakukan di Server Component

`page.tsx` adalah **Server Component** (bukan `"use client"`) — fetch ke
API (termasuk `TREND_API_TOKEN`) terjadi di server Next.js, TIDAK pernah
sampai ke browser user. Kalau nanti butuh filter interaktif (ganti tanggal
dari UI tanpa reload halaman), baru bagian filter itu jadi Client Component
yang panggil sebuah Route Handler (`app/api/trend-timeline/route.ts`) yang
menyimpan token di server, bukan fetch langsung dari browser ke
`api.dismi.xyz` (supaya token tidak pernah terekspos ke client).

## Alur data

```
posts.content (semua platform)
  -> tokenisasi + hitung frekuensi kata (SQL, di dalam API)
  -> top_n kata terbanyak
  -> bucket per hari x kata (masih di dalam API, satu response)
  -> fetchTrendTimeline() (Server Component, lib/trend-api.ts)
  -> WordCountPanel (pakai field total_mentions)
  -> TimelineChart   (pakai field total, array per hari)
```
