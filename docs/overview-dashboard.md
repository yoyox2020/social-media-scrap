# Overview Dashboard — Trend Discovery (Word Count, Timeline, Cluster, Map, Visuals, Feed)

Dokumen ini merangkum SEMUA panel dashboard trending yang dibangun 2026-07-10,
semuanya di bawah satu API module `app/api/v1/trend_discovery/router.py`
(prefix `/api/v1/trend-discovery`). Untuk dokumentasi 5-sumber triangulasi
(Twitter native Trends/TikTok/Instagram sweep/Google Trends/YouTube) yang
dibangun lebih dulu, lihat [trend-discovery-api.md](trend-discovery-api.md) —
dokumen INI fokus ke panel yang lebih baru: Word count, Timeline, Topic
clusters, Map, Visuals, Feed.

**Base URL production:** `https://api.dismi.xyz/api/v1/trend-discovery`
**Auth:** semua butuh `Authorization: Bearer <token>` (dari `POST /auth/login`).

---

## Konsep dasar: semua panel berbagi SATU sumber kata

Alur intinya:

```
posts.content (+ comments.content utk sebagian panel)
  → auto-discover: tokenisasi + hitung frekuensi kata (Word count)
  → user pilih/lihat 1 kata yang lagi ramai (mis. "world", "danantara")
  → kata itu dipakai LAGI di 4 panel drill-down: Timeline, Map, Visuals, Feed
```

Tidak perlu tahu kata apa yang lagi trending SEBELUM buka dashboard — semua
otomatis (`keywords` param SELALU opsional, kosongkan supaya API yang cari
sendiri). Kalau sudah tahu kata spesifik (dari klik salah satu bar Word
count, atau dari luar), tinggal isi param `keyword`/`keywords` di
endpoint terkait — mekanisme pencariannya (ILIKE ke `content`) SAMA PERSIS
di semua panel, jadi angkanya konsisten dan bisa dicocokkan satu sama lain.

---

## 1. Word count + Timeline

**`GET /trend-discovery/timeline`**

Panel gabungan — satu response, dua tampilan:
- **Word count**: field `total_mentions` per kata → bar ranking.
- **Timeline**: field `total` (array per jam/hari) per kata → chart garis.

### Parameter

| Param | Default | Keterangan |
|---|---|---|
| `keywords` | kosong | KOSONGKAN utk auto-discover (rekomendasi). Isi `kata1,kata2` utk override manual. |
| `top_n` | 6 | jumlah kata auto-discover (maks 15) |
| `date_from` / `date_to` | — | rentang tanggal (YYYY-MM-DD), prioritas utama |
| `hours` | 24 | fallback kalau date_from/date_to kosong |
| `interval` | `hour` | `hour` atau `day` |
| `platform` | kosong (gabungan) | filter satu platform |
| `include_platform_breakdown` | `false` | breakdown per platform per kata (respons ~6x lebih besar) |
| `include_topic_clusters` | `false` | lihat bagian [Topic clusters](#2-topic-clusters) |

### Contoh (live, real data)

```bash
curl 'https://api.dismi.xyz/api/v1/trend-discovery/timeline?date_from=2026-06-01&date_to=2026-07-10&top_n=8&interval=day' \
  -H "Authorization: Bearer $TOKEN"
```

```json
{
  "data": {
    "mode": "auto_discover",
    "date_from": "2026-06-01", "date_to": "2026-07-10",
    "interval": "day", "platform": "all",
    "keywords": ["2026", "world", "cup", "fifa", "usa", "belgium", "dunia", "france"],
    "series": {
      "2026": {
        "total_mentions": 333,
        "total": [{"bucket": "2026-06-01T00:00:00+00:00", "count": 1}, "...39 hari lagi..."]
      }
    }
  }
}
```

Hasil nyata: mention "2026" naik dari ~1/hari (awal Juni) jadi 63/hari
(7 Juli) — burst pattern nyata terekam, bukan data rata.

### Catatan penting

- **Auto-discover berbasis WORD COUNT**, bukan entitas NER — NER penuh
  (nama orang/organisasi) sengaja cuma jalan utk News (hemat compute),
  jadi kalau pakai entitas hasilnya cuma hashtag. Word count jalan sama
  rata di semua platform.
- Stopword (kata umum + hashtag generik `fyp`/`capcut`/dst + sisa HTML
  entity `amp`/`nbsp`) sudah difilter — daftar di `_STOPWORDS`, gampang
  ditambah.
- Dibucket dari `published_at` (waktu ASLI post, bukan waktu kita scrape).
- **Volume data saat ini masih tipis** di rentang pendek (topik ter-ramai
  bisa cuma 2-3 mention/10 hari) — banyak bucket `count: 0` itu REALITA
  data, bukan bug.

---

## 2. Topic clusters

Bagian dari endpoint yang sama, `include_topic_clusters=true`:

```bash
curl 'https://api.dismi.xyz/api/v1/trend-discovery/timeline?date_from=2026-06-01&date_to=2026-07-10&top_n=15&include_topic_clusters=true' \
  -H "Authorization: Bearer $TOKEN"
```

Kata-kata yang SERING MUNCUL BARENGAN di post yang sama (co-occurrence,
union-find sederhana, ambang rasio 0.3) otomatis digabung jadi 1 "topik
gabungan" — mengganti konsep "search/filter manual" (definisi filter oleh
analis) dengan cara yang tetap otomatis.

**Hasil nyata** (top_n=15, rentang sama):
```json
"topic_clusters": [
  {
    "label": "2026 world cup fifa usa belgium dunia france morocco emas piala harga highlights",
    "words": ["2026", "world", "cup", "..."],
    "total_mentions": 582
  },
  {"label": "indonesia", "words": ["indonesia"], "total_mentions": 48},
  {"label": "prabowo", "words": ["prabowo"], "total_mentions": 37}
]
```
13 kata soal Piala Dunia otomatis tergabung (memang sering muncul bareng),
sementara "indonesia" dan "prabowo" TETAP terpisah (tidak cukup sering
muncul bareng kata lain) — bukti clustering bekerja benar, bukan asal
gabung semua.

**Catatan:** union-find bersifat TRANSITIF (chaining) — kalau A-B dan B-C
lolos ambang, A/B/C jadi 1 cluster walau A-C sendiri tidak pernah dicek
langsung. Ini perilaku NORMAL, bukan bug — kalau semua kata kegabung jadi
1 cluster besar, itu tandanya topik-topik itu memang saling nyambung erat
di data hari itu.

---

## 3. Map (geo-distribution)

**`GET /trend-discovery/geo-distribution`**

Distribusi NAMA TEMPAT (negara/kota) yang DISEBUT di post + komentar.

**PENTING soal makna data ini:** ini BUKAN geotag/lokasi asli si poster —
data itu **TIDAK ADA** di platform manapun yang kita scrape (sudah
diverifikasi langsung ke database, semua `metadata`/`raw_data` dicek
kosong dari field lokasi). Yang dihitung di sini adalah "tempat mana yang
JADI SUBJEK pembicaraan" — cocok utk topik ekonomi/politik/berita yang
menyebut negara/kota spesifik.

### Parameter

| Param | Default | Keterangan |
|---|---|---|
| `date_from` / `date_to` / `hours` | sama seperti di atas | rentang tanggal |
| `platform` | kosong (gabungan) | filter satu platform |
| `min_mentions` | 1 | buang tempat dengan mention di bawah angka ini |

### Contoh (live, real data)

```bash
curl 'https://api.dismi.xyz/api/v1/trend-discovery/geo-distribution?date_from=2026-06-01&date_to=2026-07-10' \
  -H "Authorization: Bearer $TOKEN"
```

```json
{
  "data": {
    "total_places_checked": 49, "total_places_matched": 36,
    "places": [
      {"place": "Indonesia", "lat": -2.5, "lng": 118.0, "total_mentions": 60, "from_posts": 48, "from_comments": 12},
      {"place": "Bali", "lat": -8.4095, "lng": 115.1889, "total_mentions": 36, "from_posts": 33, "from_comments": 3},
      {"place": "Prancis", "lat": 46.6034, "lng": 1.8883, "total_mentions": 34, "from_posts": 34, "from_comments": 0}
    ]
  }
}
```

**Konsistensi terverifikasi:** `from_posts` field bisa dicocokkan LANGSUNG
ke angka Word count — "Indonesia" `from_posts=48` PERSIS SAMA dengan
"indonesia" `total_mentions=48` di Word count (rentang tanggal sama).

Daftar 49 tempat (kota/provinsi Indonesia + negara dunia relevan) ada di
konstanta `_GEO_GAZETTEER`, gampang ditambah/dikurangi kapan saja. Sengaja
exclude nama kota yang juga kata umum Indonesia ("Malang"=sial,
"Medan"=arena/lapangan — diganti "Surakarta" bukan "Solo") supaya tidak
banyak false-positive.

---

## 4. Visuals

**`GET /trend-discovery/visuals?keyword=...`**

Post ASLI (bukan agregat) yang mengandung `keyword`, lengkap dengan
thumbnail siap tampil — drill-down dari kata yang sudah dilihat di Word
count/Timeline/Topic clusters.

### Parameter

| Param | Wajib? | Keterangan |
|---|---|---|
| `keyword` | ya | kata/frasa yang dicari |
| `date_from`/`date_to`/`hours` | tidak | rentang tanggal, sama seperti panel lain |
| `platform` | tidak | `youtube` / `instagram` / `news` saja |
| `limit` | tidak | default 12, maks 50 |

### ⚠️ HANYA 3 platform: youtube, instagram, news

Facebook/TikTok/Twitter **TIDAK menyimpan field gambar post sama sekali** —
ini gap di kode kita (`pipeline_service.py` masing-masing platform), BUKAN
keterbatasan Apify (kemungkinan besar datanya ADA di respons mentah actor,
cuma belum pernah diprogram utk diambil). SENGAJA belum dikerjakan karena
itu perlu ubah kode scraping yang sudah jalan — dicatat sebagai future task
di memory, tunggu konfirmasi eksplisit.

### Contoh (live, real data)

```bash
curl 'https://api.dismi.xyz/api/v1/trend-discovery/visuals?keyword=world&date_from=2026-06-01&date_to=2026-07-10&limit=5' \
  -H "Authorization: Bearer $TOKEN"
```

```json
{
  "data": {
    "keyword": "world", "total": 5,
    "items": [
      {
        "platform": "youtube", "author": "FIFA",
        "title": "Kylian Mbappe Goal | France 2-0 Morocco | FIFA World Cup 2026™",
        "thumbnail": "https://i.ytimg.com/vi/3LrNmNWyAn0/hqdefault.jpg",
        "url": "https://www.youtube.com/watch?v=3LrNmNWyAn0",
        "published_at": "2026-07-10T00:50:34+00:00"
      }
    ]
  }
}
```

---

## 5. Feed

**`GET /trend-discovery/feed?keyword=...`**

Feed mention individual (BUKAN agregat) — gabungan `posts` DAN `comments`
(dihubungkan `comments.post_id = posts.id`), diurutkan waktu terbaru
duluan, tiap item ditandai `source_type` (`post` atau `comment`).

Beda dari Visuals: teks murni (tidak butuh thumbnail), jadi jalan di
**SEMUA platform** termasuk Facebook/TikTok/Twitter.

### Parameter

Sama seperti Visuals (`keyword` wajib, `date_from`/`date_to`/`hours`,
`platform` — tapi di sini SEMUA platform valid, bukan cuma 3), plus
`limit` (default 20, maks 100).

### Contoh (live, real data)

```bash
curl 'https://api.dismi.xyz/api/v1/trend-discovery/feed?keyword=world&date_from=2026-06-01&date_to=2026-07-10&limit=5' \
  -H "Authorization: Bearer $TOKEN"
```

```json
{
  "data": {
    "keyword": "world", "total": 5,
    "items": [
      {
        "source_type": "post", "platform": "twitter", "author": "narasitv",
        "content": "Justin Bieber dan Coldplay join jadi co-headliner yang tampil di first ever FIFA World Cup Halftime...",
        "url": "https://x.com/narasitv/status/2075074559204901310",
        "published_at": "2026-07-09T04:28:22+00:00"
      }
    ]
  }
}
```

---

## Alur pemakaian disarankan (dashboard)

1. **Load awal** (tanpa perlu tahu topik apa pun): panggil
   `/timeline` (auto-discover) dan `/geo-distribution` sekaligus,
   paralel — dua-duanya independen, tidak butuh `keyword`.
2. User klik salah satu bar/garis di Word count/Timeline (mis. "world").
3. Panggil `/visuals?keyword=world` dan `/feed?keyword=world` paralel —
   ini yang isi panel detail/drill-down di sisi lain layar.
4. (Opsional) kalau mau lihat "topik gabungan" bukan kata satuan, panggil
   ulang `/timeline` dengan `include_topic_clusters=true`, pakai
   `label` dari cluster sebagai `keyword` di langkah 3.

Semua endpoint di atas READ-ONLY (SELECT saja), aman dipanggil sesering
apa pun, tidak ada efek samping ke data lain.

---

## Referensi terkait

- [trend-discovery-api.md](trend-discovery-api.md) — dokumentasi 5-sumber
  triangulasi (Twitter native Trends/TikTok/Instagram sweep/Google
  Trends/YouTube), fitur yang dibangun SEBELUM panel-panel di atas.
- `scripts/word_count_trending.py` — CLI Python sederhana, panggil
  `/timeline` mode auto-discover, cetak ranking Word count ke terminal.
- `scripts/nextjs-trend-timeline/` — referensi Next.js App Router (Server
  Component fetch + Client Component Canvas), sudah pernah benar-benar
  dijalankan (npm run dev) dan terbukti kerja dengan data production asli.

## Keterbatasan yang perlu diketahui

- **Volume data masih tipis** utk rentang tanggal pendek — hasil akan
  lebih kaya kalau `date_from`/`date_to` diperlebar (mis. 1 bulan).
- **Visuals cuma youtube/instagram/news** (lihat bagian 4).
- **Geo-distribution mengukur "tempat yang disebut", bukan lokasi
  poster** (lihat bagian 3) — jangan disalahartikan sebagai geolocation
  asli pengguna.
- **Topic clusters bisa "chaining"** kalau data punya 1 cerita dominan
  (lihat bagian 2) — perilaku normal union-find, bukan bug.
