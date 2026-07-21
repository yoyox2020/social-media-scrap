# Metrics Drill-down API — Panduan Frontend

Endpoint drill-down: setelah kartu summary tampil (lihat
[metrics-cards-api.md](metrics-cards-api.md)), user klik salah satu angka
→ endpoint di dokumen ini balikin daftar data MENTAH di baliknya.

Router: [app/api/v1/metrics.py](../app/api/v1/metrics.py)
Logic: [app/services/metrics/calculator.py](../app/services/metrics/calculator.py)

Semua endpoint butuh login (`Authorization: Bearer <token>`).

---

## Drill-down: user klik salah satu angka

```
GET /api/v1/metrics/keyword/{keyword_id}/detail?metric={metric}&platform={platform}&page=1&limit=20
```

| Param | Wajib? | Keterangan |
|---|---|---|
| `metric` | **ya** | salah satu: `mentions`, `exposure`, `engagement`, `reach`, `sentiment` |
| `platform` | tidak | 1 platform (mis. `youtube`). Kosong = semua platform digabung jadi satu daftar |
| `sentiment_label` | tidak | HANYA berlaku kalau `metric=sentiment` — filter `positif`/`negatif`/`netral` |
| `sort_by` | tidak | HANYA berlaku `metric=mentions/exposure/engagement` — kosong = urut tanggal terbaru dulu. Isi salah satu `views`/`likes`/`comments`/`shares`/`saves`/`replies`/`clicks` utk urut PALING BANYAK dulu (lihat bagian **"Grafik Komposisi Engagement"** di bawah) |
| `date_from`, `date_to` | tidak | default 30 hari terakhir, SAMA dgn default endpoint summary |
| `page`, `limit` | tidak | default `page=1&limit=20`, maks `limit=100` |

**Total di response `pagination.total` DIJAMIN sama persis dengan angka
summary-nya** (mis. kalau kartu YouTube bilang Mentions=4862, drill-down
`metric=mentions&platform=youtube` pasti `total: 4862`) — karena keduanya
pakai filter query yang sama persis di baliknya.

### a. `metric=mentions` / `exposure` / `engagement` → daftar POST

```
GET /metrics/keyword/3237c49d-.../detail?metric=mentions&platform=youtube&page=1&limit=20
```

```json
{
  "success": true,
  "data": {
    "scope": "keyword_detail",
    "keyword": { "id": "3237c49d-...", "text": "anies" },
    "metric": "mentions",
    "platforms": ["youtube"],
    "period": { "from": "...", "to": "..." },
    "pagination": { "page": 1, "limit": 20, "total": 4862, "total_pages": 244 },
    "items": [
      {
        "id": "8f2a1c3e-...",
        "external_id": "dQw4w9WgXcQ",
        "platform": "youtube",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "title": "Anies Baswedan Bicara soal ...",
        "author": "Kompas TV",
        "published_at": "2026-07-17T10:00:00+00:00",
        "views": 45000,
        "engagement": 1830,
        "engagement_breakdown": { "likes": 1200, "comments": 340, "shares": 0, "saves": 0, "replies": 0, "clicks": 0, "unavailable_fields": ["shares","saves","replies","clicks"] }
      }
    ]
  }
}
```

**Cara pakai di frontend**: tiap `item.url` sudah link langsung ke post
aslinya di YouTube/TikTok/dll — tinggal `<a href="{item.url}" target="_blank">`.
Kalau nanti ada halaman detail post internal, pakai `item.id` sebagai key-nya.

### b. `metric=reach` → daftar AKUN (bukan post)

Reach dihitung per-akun-unik, jadi bentuk datanya beda dari post biasa:

```json
{
  "data": {
    "metric": "reach",
    "pagination": { "page": 1, "limit": 20, "total": 2669, "total_pages": 134 },
    "items": [
      { "author": "Kompas TV", "platform": "youtube", "post_count": 87 },
      { "author": "CNN Indonesia", "platform": "youtube", "post_count": 62 }
    ]
  }
}
```

Diurut dari akun paling aktif (post terbanyak) dulu.

### c. `metric=sentiment` → daftar KOMENTAR

```
GET /metrics/keyword/3237c49d-.../detail?metric=sentiment&platform=youtube&sentiment_label=negatif&page=1&limit=20
```

```json
{
  "data": {
    "metric": "sentiment",
    "pagination": { "page": 1, "limit": 20, "total": 300 },
    "items": [
      {
        "comment_id": "c1a2b3-...",
        "content": "kecewa banget sama kebijakan ini",
        "author": "@user123",
        "label": "negatif",
        "sentiment_source": "llm_reviewed",
        "post_id": "8f2a1c3e-...",
        "post_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "post_title": "Anies Baswedan Bicara soal ...",
        "platform": "youtube",
        "published_at": "2026-07-17T11:20:00+00:00"
      }
    ]
  }
}
```

- `label` sudah final (hasil `COALESCE(final_label, label)` — pakai
  koreksi Sentiment Agent kalau sudah direview, lexicon asli kalau belum).
- `sentiment_source` = `"llm_reviewed"` atau `"lexicon_only"`, buat kasih
  tanda visual (mis. badge kecil) kalau mau transparan ke user data mana
  yang sudah dicek ulang AI.
- Klik komentar → bisa arahkan ke `post_url` (buka post aslinya) sambil
  tampilkan `content` komentarnya sebagai konteks.

---

## Grafik "Komposisi Engagement per Platform" — klik segmen bar

Untuk grafik horizontal-bar (Likes/Komentar/Share/Save per platform, lihat
gambar dashboard): tiap SEGMEN warna itu bagian dari `engagement.breakdown`
di endpoint summary. Saat user klik salah satu segmen (mis. segmen "Likes"
warna ungu di bar YouTube), panggil endpoint drill-down dengan
`sort_by={komponen}` supaya post PALING BANYAK di komponen itu tampil duluan
— bukan cuma daftar biasa urut tanggal.

```
GET /metrics/keyword/{id}/detail?metric=engagement&platform=youtube&sort_by=likes&page=1&limit=20
```

```json
{
  "data": {
    "metric": "engagement",
    "sort_by": "likes",
    "pagination": { "page": 1, "limit": 20, "total": 4862, "total_pages": 244 },
    "items": [
      { "id": "8f2a1c3e-...", "url": "https://youtube.com/watch?v=...", "title": "Video paling banyak di-like",
        "engagement_breakdown": { "likes": 89000, "comments": 3200, "shares": 0, "saves": 0 } },
      { "id": "9a3b2d4f-...", "url": "https://youtube.com/watch?v=...", "title": "Video terbanyak ke-2",
        "engagement_breakdown": { "likes": 71000, "comments": 1800, "shares": 0, "saves": 0 } }
    ]
  }
}
```

Mapping klik segmen → nilai `sort_by`:

| Segmen di legenda grafik | `sort_by=` |
|---|---|
| Likes | `likes` |
| Komentar | `comments` |
| Share | `shares` |
| Save | `saves` (TikTok) — utk platform lain biasanya `0`, item tetap muncul cuma `saves` semua 0 |

Contoh kode klik segmen:

```js
async function onSegmentClick(keywordId, platform, component) {
  // component = "likes" | "comments" | "shares" | "saves"
  const res = await fetch(
    `/api/v1/metrics/keyword/${keywordId}/detail?metric=engagement&platform=${platform}&sort_by=${component}&page=1&limit=20`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
  const json = await res.json();
  // json.data.items[0] = post PALING BANYAK di komponen yg diklik
  // tiap item.url bisa langsung dibuka (link ke post asli)
  renderDrilldownList(json.data.items, json.data.pagination);
}
```

---

## Grafik "Tren Mention Harian" — klik satu titik di garis

Panel per-platform di [app/api/v1/metrics.py `/metrics/trend`](../app/api/v1/metrics.py)
balikin `{period, mentions}` per hari (atau minggu/bulan kalau
`granularity` diubah). Saat user klik satu TITIK di garis (mis. tooltip
"4 Jul, Mentions: 1" pada panel TikTok) — **reuse endpoint drill-down yang
sama** (`metric=mentions`), cukup set `date_from`/`date_to` supaya
rentangnya PERSIS satu hari itu:

```
GET /metrics/keyword/{id}/detail?metric=mentions&platform=tiktok&date_from=2026-07-04T00:00:00&date_to=2026-07-04T23:59:59&page=1&limit=20
```

```js
async function onTrendPointClick(keywordId, platform, periodISO) {
  // periodISO = "2026-07-04T00:00:00+00:00" (field `period` dari /metrics/trend, granularity=day)
  const day = periodISO.slice(0, 10); // "2026-07-04"
  const res = await fetch(
    `/api/v1/metrics/keyword/${keywordId}/detail` +
    `?metric=mentions&platform=${platform}` +
    `&date_from=${day}T00:00:00&date_to=${day}T23:59:59` +
    `&page=1&limit=20`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
  const json = await res.json();
  // json.data.pagination.total HARUS SAMA dgn angka tooltip trend chart
  renderDrilldownList(json.data.items, json.data.pagination);
}
```

> **Penting kalau `granularity=week` atau `granularity=month`** (bukan
> `day`): titik yang diklik mewakili SATU RENTANG (minggu/bulan), bukan
> satu hari. Set `date_from` = awal periode itu, `date_to` = SEHARI
> SEBELUM periode berikutnya dimulai (mis. minggu yang di-`date_trunc`
> Postgres mulai hari Senin — `date_from` = Senin itu, `date_to` = Minggu
> berikutnya 23:59:59).

**Kenapa ini akurat**: filter tanggal drill-down memakai
`COALESCE(published_at, collected_at)` — PERSIS sama dengan cara
`/metrics/trend` mengelompokkan post per hari (bukan cuma `published_at`
saja). Jadi kalau chart bilang "4 Jul: 1 mention", drill-down hari itu
DIJAMIN juga `total: 1` — termasuk post yang kebetulan tidak punya tanggal
publish asli (beberapa post YouTube/News lama), yang sebelumnya bisa
"hilang" diam-diam kalau drill-down cuma cek `published_at`.

---

## Contoh alur lengkap (frontend)

1. Kartu summary sudah tampil (lihat [metrics-cards-api.md](metrics-cards-api.md)).
2. User klik angka **"Mentions: 4,862"** di kartu YouTube → panggil
   `/metrics/keyword/{id}/detail?metric=mentions&platform=youtube&page=1&limit=20`
   → tampilkan modal/halaman list, tiap baris link ke `item.url`.
3. Scroll ke bawah / klik "halaman berikutnya" → panggil ulang dengan
   `page=2`, dst — pakai `pagination.total_pages` buat tahu kapan berhenti.
4. User klik **"Sentimen"** → panggil dgn `metric=sentiment`. Kalau mau
   filter cuma yang negatif, tambahkan `&sentiment_label=negatif`.
5. User klik **"Reach"** → panggil dgn `metric=reach`, tampilkan daftar
   akun (bukan post) — beda bentuk kartu/list dari yang lain.
6. User klik segmen di grafik **"Komposisi Engagement per Platform"** →
   panggil dgn `metric=engagement&sort_by={komponen}` (lihat bagian di atas).
7. User klik satu titik di grafik **"Tren Mention Harian"** → panggil dgn
   `metric=mentions&date_from={hari itu 00:00}&date_to={hari itu 23:59:59}`
   (lihat bagian di atas).

---

## Error yang mungkin muncul

| Kasus | Response |
|---|---|
| `metric` bukan salah satu dari 5 pilihan | `400` — `"metric harus salah satu dari ['engagement', 'exposure', 'mentions', 'reach', 'sentiment']"` |
| `sort_by` bukan salah satu dari 7 komponen valid | `400` — `"sort_by harus salah satu dari [...]"` |
| `keyword_id` tidak ada di DB | `404` — `"Keyword {id} tidak ditemukan"` |
| Tanpa token / token invalid | `401` |
