# Dokumentasi Dashboard Metrics API

API khusus untuk menampilkan 7 metrik utama di dashboard analitik sosial media.
Dirancang extensible — platform baru (TikTok, Instagram, News) cukup tambah adapter tanpa ubah endpoint.

**Base URL Lokal:** `http://localhost:8000`
**Base URL Server:** `http://187.77.125.10:8000`
**Swagger UI:** `http://187.77.125.10:8000/docs#/metrics`

---

## Daftar Isi

1. [Struktur File dan Folder](#1-struktur-file-dan-folder)
2. [Metodologi 7 Metrik](#2-metodologi-7-metrik)
3. [Kondisi Data Saat Ini](#3-kondisi-data-saat-ini)
4. [Endpoint API](#4-endpoint-api)
5. [Contoh Penggunaan — Server (bash)](#5-contoh-penggunaan--server-bash)
6. [Contoh Penggunaan — Lokal (PowerShell)](#6-contoh-penggunaan--lokal-powershell)
7. [Contoh Penggunaan — Frontend React](#7-contoh-penggunaan--frontend-react)
8. [Cara Tambah Platform Baru](#8-cara-tambah-platform-baru)

---

## 1. Struktur File dan Folder

```
social-media-scrap/
│
├── app/
│   ├── api/
│   │   └── v1/
│   │       └── metrics.py                    ← 5 endpoint metrics
│   │
│   └── services/
│       └── metrics/
│           ├── __init__.py
│           ├── calculator.py                 ← semua 7 rumus metrik
│           └── adapters/
│               ├── __init__.py
│               ├── base.py                   ← kontrak platform (abstract)
│               └── youtube.py                ← YouTube field mapping
│
└── docs/
    └── phase-done/
        └── api-metrics.md                    ← file ini
```

### Penjelasan File

**`app/api/v1/metrics.py`**
Router dengan 5 endpoint. Menerima filter `platform`, `date_from`, `date_to`.
Tidak ada logika perhitungan di sini — semua didelegasikan ke `calculator.py`.

**`app/services/metrics/calculator.py`**
Pusat semua rumus. Berisi:
- `compute_metrics()` — hitung semua 7 metrik sekaligus
- `calc_exposure()`, `calc_reach()`, `calc_engagement()`, dll — satu fungsi per metrik
- `fetch_post_stats()`, `fetch_sentiment_counts()` — query DB helper

**`app/services/metrics/adapters/base.py`**
Abstract base class `PlatformAdapter`. Mendefinisikan kontrak:
- `extract_views(metadata)` → int
- `extract_likes(metadata)` → int
- `extract_shares(metadata)` → int
- `engagement_breakdown(metadata, comment_count_db)` → dict

**`app/services/metrics/adapters/youtube.py`**
Implementasi YouTube: mapping nama field dari `metadata` JSON ke field standar.
Mendaftarkan field yang belum tersedia (`shares`, `saves`, `replies`, `clicks`).

---

## 2. Metodologi 7 Metrik

### 1. Exposure
```
Exposure = SUM(views seluruh postingan)
```
- Sumber data: `posts.metadata->>'views'`
- Untuk YouTube: diambil dari `viewCountText` saat video di-scrape
- Merepresentasikan total tayangan / impressi konten yang membahas keyword

### 2. Reach
```
Reach = COUNT DISTINCT author/channel
```
- Sumber data: `COUNT(DISTINCT posts.author)`
- Merepresentasikan berapa banyak akun unik (channel YouTube, akun TikTok, dll)
  yang mempublikasikan konten tentang keyword ini
- **Catatan:** Ini adalah "creator reach" — jumlah pembuat konten unik, bukan jumlah viewer unik

### 3. Engagement
```
Engagement = Likes + Komentar + Shares + Saves + Replies + Clicks
```
- Sumber data per komponen:
  - `likes` → `posts.metadata->>'likes'`
  - `comments` → `COUNT(comments)` dari tabel comments (data nyata di DB)
  - `shares`, `saves`, `replies`, `clicks` → `posts.metadata` (jika tersedia platform)
- Response menyertakan `breakdown` per komponen untuk transparansi

### 4. Engagement Rate
```
Engagement Rate = (Engagement ÷ Reach) × 100%
```
- Mengukur seberapa aktif interaksi dibanding jumlah creator yang membahas

### 5. Sentiment Score
```
Sentiment Score = ((Jumlah Positif − Jumlah Negatif) ÷ Total Percakapan) × 100
```
- Sumber data: tabel `lexicon_analyses` (hasil analisis komentar)
- Nilai: -100 (sangat negatif) hingga +100 (sangat positif)
- Nilai 0 = netral seimbang

### 6. Share of Voice (SOV)
```
SOV = (Mention Keyword Ini ÷ Total Mention Semua Keyword) × 100%
```
- Sumber data: `COUNT(posts)` per keyword vs total semua keyword
- Menunjukkan seberapa besar porsi perbincangan keyword ini dibanding keyword lain
- Berguna untuk chart pie di dashboard

### 7. Mention Growth
```
Mention Growth = ((Mention Periode Ini − Mention Periode Sebelumnya) ÷ Periode Sebelumnya) × 100%
```
- Periode sebelumnya dihitung otomatis: durasi yang sama sebelum `date_from`
- Contoh: filter 30 hari → bandingkan dengan 30 hari sebelumnya
- Nilai positif = tumbuh, negatif = menurun

---

## 3. Kondisi Data Saat Ini

| Field | Sumber | Status | Keterangan |
|-------|--------|--------|------------|
| Views (Exposure) | `metadata->>'views'` | ✅ Ada | Diambil saat crawl pertama — snapshot statis |
| Likes | `metadata->>'likes'` | ⚠️ 0 | YouTube search API tidak return likes. Perlu `/video/details` API |
| Comments | Tabel `comments` | ✅ Ada | 900 komentar tersimpan di DB |
| Shares | `metadata->>'shares'` | ❌ Tidak ada | Belum di-scrape dari YouTube |
| Saves / Replies / Clicks | `metadata` | ❌ Tidak ada | Tidak tersedia dari YouTube search |
| Sentiment | Tabel `lexicon_analyses` | ✅ Ada | 900 komentar dianalisis |

**Akibatnya:**
- `Engagement` saat ini praktis = jumlah komentar di DB (likes = 0, shares = 0)
- `Exposure` sudah benar tapi statis (tidak update harian)

**Rencana perbaikan (belum diimplementasi):**
- Celery task harian: panggil `/youtube/video/details` per video → update `like_count`, `view_count` terbaru
- Ini akan membuat Exposure dan Likes akurat dan selalu up-to-date

---

## 4. Endpoint API

### GET /api/v1/metrics/summary

Metrik global seluruh keyword aktif — untuk widget ringkasan halaman utama dashboard.

**Query Parameters:**

| Parameter | Default | Keterangan |
|-----------|---------|------------|
| `platforms` | `["youtube"]` | Filter platform |
| `date_from` | 30 hari lalu | ISO datetime |
| `date_to` | sekarang | ISO datetime |
| `include_growth` | `true` | Hitung Mention Growth vs periode sebelumnya |

**Response:**
```json
{
  "success": true,
  "data": {
    "scope": "global",
    "platforms": ["youtube"],
    "period": {
      "from": "2026-05-29T14:31:32+00:00",
      "to": "2026-06-28T14:31:32+00:00"
    },
    "metrics": {
      "exposure": {
        "value": 154550465,
        "label": "Total Impression",
        "description": "Total tayangan seluruh postingan"
      },
      "reach": {
        "value": 23,
        "label": "Reach",
        "description": "Total akun unik (channel/creator) yang membahas topik ini"
      },
      "engagement": {
        "value": 22,
        "label": "Engagement",
        "description": "Total Like + Komentar + Share + Save + Reply + Klik",
        "breakdown": {
          "likes": 0,
          "comments": 22,
          "shares": 0,
          "saves": 0,
          "replies": 0,
          "clicks": 0
        }
      },
      "engagement_rate": {
        "value": 95.65,
        "label": "Engagement Rate",
        "unit": "%",
        "description": "(Engagement ÷ Reach) × 100%"
      },
      "sentiment_score": {
        "value": 50.0,
        "label": "Sentiment Score",
        "unit": "%",
        "description": "((Positif − Negatif) ÷ Total Percakapan) × 100",
        "detail": {
          "positif": 13,
          "negatif": 2,
          "netral": 7,
          "total": 22
        }
      },
      "sov": {
        "value": null,
        "label": "Share of Voice",
        "unit": "%",
        "available": false
      },
      "mention_growth": {
        "value": 1225.0,
        "label": "Mention Growth",
        "unit": "%",
        "available": true
      },
      "mentions": {
        "value": 53,
        "label": "Total Mentions",
        "description": "Total postingan yang membahas keyword ini"
      }
    }
  }
}
```

---

### GET /api/v1/metrics/keyword/{keyword_id}

Metrik untuk satu keyword — termasuk SOV dibandingkan semua keyword lain.

**Query Parameters:** sama seperti `/summary` + `include_growth`

**Response tambahan:**
```json
{
  "scope": "keyword",
  "keyword": {
    "id": "3c151423-...",
    "text": "pendakwah oki setiana dewi"
  },
  "metrics": {
    "sov": {
      "value": 1.89,
      "label": "Share of Voice",
      "unit": "%",
      "available": true
    }
  }
}
```

---

### GET /api/v1/metrics/topic/{topic_id}

Metrik agregat satu topik. Semua keyword dalam topik dijumlah.
Gunakan `breakdown_per_keyword=true` untuk melihat kontribusi tiap keyword.

**Query Parameters:**

| Parameter | Default | Keterangan |
|-----------|---------|------------|
| `platforms` | `["youtube"]` | Filter platform |
| `date_from` | 30 hari lalu | ISO datetime |
| `date_to` | sekarang | ISO datetime |
| `include_growth` | `true` | Hitung growth |
| `breakdown_per_keyword` | `false` | Tampilkan metrik per keyword |

**Response dengan `breakdown_per_keyword=true`:**
```json
{
  "scope": "topic",
  "topic": {"id": "uuid...", "name": "Tokoh Agama"},
  "total_keywords": 2,
  "metrics": { ... },
  "keyword_breakdown": [
    {
      "keyword": "pendakwah oki setiana dewi",
      "keyword_id": "uuid...",
      "metrics": { ... }
    }
  ]
}
```

---

### GET /api/v1/metrics/sov

Perbandingan Share of Voice antar keyword — untuk chart pie/bar di dashboard.

**Query Parameters:**

| Parameter | Default | Keterangan |
|-----------|---------|------------|
| `keyword_ids` | semua keyword aktif | UUID list, kosong = semua |
| `platforms` | `["youtube"]` | Filter platform |
| `date_from` | 30 hari lalu | ISO datetime |
| `date_to` | sekarang | ISO datetime |

**Response:**
```json
{
  "scope": "sov_comparison",
  "total_mentions": 53,
  "items": [
    {"keyword": "fifa world cup games", "mentions": 38, "sov_pct": 71.7},
    {"keyword": "demo dprd",            "mentions": 9,  "sov_pct": 16.98},
    {"keyword": "tantri kotak",         "mentions": 3,  "sov_pct": 5.66},
    {"keyword": "pendakwah oki setiana dewi", "mentions": 1, "sov_pct": 1.89}
  ]
}
```

---

### GET /api/v1/metrics/trend

Time series jumlah mention per hari/minggu/bulan — untuk grafik tren di dashboard.
Bisa filter per keyword atau per topik.

**Query Parameters:**

| Parameter | Default | Keterangan |
|-----------|---------|------------|
| `keyword_ids` | kosong | Filter per keyword |
| `topic_id` | null | Filter per topik (otomatis resolve keyword) |
| `platforms` | `["youtube"]` | Filter platform |
| `date_from` | 30 hari lalu | ISO datetime |
| `date_to` | sekarang | ISO datetime |
| `granularity` | `day` | `day` / `week` / `month` |

**Response:**
```json
{
  "scope": "trend",
  "granularity": "week",
  "series": [
    {"period": "2026-06-01T00:00:00+00:00", "mentions": 1},
    {"period": "2026-06-08T00:00:00+00:00", "mentions": 2},
    {"period": "2026-06-15T00:00:00+00:00", "mentions": 13},
    {"period": "2026-06-22T00:00:00+00:00", "mentions": 40}
  ]
}
```

---

## 5. Contoh Penggunaan — Server (bash)

### Login sekali, simpan token

```bash
TOKEN=$(curl -s -X POST http://187.77.125.10:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"yahyamatoristmik@gmail.com","password":"admin123"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['access_token'])")

echo $TOKEN
```

### Summary global

```bash
curl -s "http://187.77.125.10:8000/api/v1/metrics/summary?platforms=youtube" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

### SOV — porsi keyword

```bash
curl -s "http://187.77.125.10:8000/api/v1/metrics/sov?platforms=youtube" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

### Tren per minggu

```bash
curl -s "http://187.77.125.10:8000/api/v1/metrics/trend?platforms=youtube&granularity=week" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

### Tren per hari (30 hari terakhir)

```bash
curl -s "http://187.77.125.10:8000/api/v1/metrics/trend?platforms=youtube&granularity=day" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

### Metrik per topik (ambil topic_id dulu)

```bash
# Langkah 1 — lihat daftar topik
curl -s "http://187.77.125.10:8000/api/v1/search/topics/list" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# Langkah 2 — metrik topik (ganti <topic_id>)
curl -s "http://187.77.125.10:8000/api/v1/metrics/topic/<topic_id>?breakdown_per_keyword=true" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

### Metrik per keyword (ambil keyword_id dulu)

```bash
# Langkah 1 — lihat semua keyword
curl -s "http://187.77.125.10:8000/api/v1/keywords/" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# Langkah 2 — metrik keyword (ganti <keyword_id>)
curl -s "http://187.77.125.10:8000/api/v1/metrics/keyword/<keyword_id>" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

### Filter rentang tanggal tertentu

```bash
curl -s "http://187.77.125.10:8000/api/v1/metrics/summary?platforms=youtube&date_from=2026-06-01T00:00:00Z&date_to=2026-06-28T23:59:59Z" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

---

## 6. Contoh Penggunaan — Lokal (PowerShell)

```powershell
# Login
$TOKEN = (curl -s -X POST http://localhost:8000/api/v1/auth/login `
  -H "Content-Type: application/json" `
  -d '{"email":"yahyamatoristmik@gmail.com","password":"admin123"}' | `
  ConvertFrom-Json).data.access_token

# Summary
curl -s "http://localhost:8000/api/v1/metrics/summary?platforms=youtube" `
  -H "Authorization: Bearer $TOKEN" | ConvertFrom-Json | ConvertTo-Json -Depth 10

# SOV
curl -s "http://localhost:8000/api/v1/metrics/sov?platforms=youtube" `
  -H "Authorization: Bearer $TOKEN" | ConvertFrom-Json | ConvertTo-Json -Depth 5

# Trend
curl -s "http://localhost:8000/api/v1/metrics/trend?platforms=youtube&granularity=week" `
  -H "Authorization: Bearer $TOKEN" | ConvertFrom-Json | ConvertTo-Json -Depth 5
```

---

## 7. Contoh Penggunaan — Frontend React

### Setup service

```js
// services/metricsService.js
const BASE_URL = 'http://187.77.125.10:8000/api/v1'

const headers = () => ({
  'Authorization': `Bearer ${localStorage.getItem('token')}`,
  'Content-Type': 'application/json'
})

const buildParams = (params) =>
  Object.entries(params)
    .filter(([, v]) => v !== null && v !== undefined)
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
    .join('&')

export const metricsService = {

  // Widget utama dashboard
  summary: (platforms = ['youtube'], dateFrom = null, dateTo = null) => {
    const p = buildParams({ platforms, date_from: dateFrom, date_to: dateTo })
    return fetch(`${BASE_URL}/metrics/summary?${p}`, { headers: headers() })
      .then(r => r.json())
  },

  // Metrik per keyword
  keyword: (keywordId, platforms = ['youtube']) => {
    return fetch(`${BASE_URL}/metrics/keyword/${keywordId}?platforms=${platforms}`, { headers: headers() })
      .then(r => r.json())
  },

  // Metrik per topik
  topic: (topicId, breakdownPerKeyword = false) => {
    return fetch(
      `${BASE_URL}/metrics/topic/${topicId}?breakdown_per_keyword=${breakdownPerKeyword}`,
      { headers: headers() }
    ).then(r => r.json())
  },

  // SOV untuk chart pie
  sov: (platforms = ['youtube']) => {
    return fetch(`${BASE_URL}/metrics/sov?platforms=${platforms}`, { headers: headers() })
      .then(r => r.json())
  },

  // Time series untuk grafik
  trend: (granularity = 'week', topicId = null, platforms = ['youtube']) => {
    const p = buildParams({ granularity, topic_id: topicId, platforms })
    return fetch(`${BASE_URL}/metrics/trend?${p}`, { headers: headers() })
      .then(r => r.json())
  },
}
```

### Komponen widget metrik

```jsx
// components/MetricCard.jsx
function MetricCard({ label, value, unit = '', description }) {
  return (
    <div className="metric-card">
      <p className="metric-label">{label}</p>
      <h2 className="metric-value">
        {value?.toLocaleString() ?? '-'}
        {unit && <span className="metric-unit">{unit}</span>}
      </h2>
      <p className="metric-desc">{description}</p>
    </div>
  )
}
```

### Halaman dashboard — semua widget sekaligus

```jsx
// pages/Dashboard.jsx
import { useEffect, useState } from 'react'
import { metricsService } from '../services/metricsService'

function Dashboard() {
  const [metrics, setMetrics] = useState(null)
  const [sov, setSov] = useState([])
  const [trend, setTrend] = useState([])

  useEffect(() => {
    metricsService.summary().then(res => setMetrics(res.data?.metrics))
    metricsService.sov().then(res => setSov(res.data?.items ?? []))
    metricsService.trend('week').then(res => setTrend(res.data?.series ?? []))
  }, [])

  if (!metrics) return <div>Loading...</div>

  return (
    <div>
      {/* Widget row */}
      <div className="metrics-row">
        <MetricCard
          label={metrics.exposure.label}
          value={metrics.exposure.value}
          description={metrics.exposure.description}
        />
        <MetricCard
          label={metrics.reach.label}
          value={metrics.reach.value}
          description={metrics.reach.description}
        />
        <MetricCard
          label={metrics.engagement.label}
          value={metrics.engagement.value}
          description={metrics.engagement.description}
        />
        <MetricCard
          label={metrics.engagement_rate.label}
          value={metrics.engagement_rate.value}
          unit="%"
          description={metrics.engagement_rate.description}
        />
        <MetricCard
          label={metrics.sentiment_score.label}
          value={metrics.sentiment_score.value}
          unit="%"
          description={metrics.sentiment_score.description}
        />
        <MetricCard
          label={metrics.mentions.label}
          value={metrics.mentions.value}
          description={metrics.mentions.description}
        />
        {metrics.mention_growth.available && (
          <MetricCard
            label={metrics.mention_growth.label}
            value={metrics.mention_growth.value}
            unit="%"
            description={metrics.mention_growth.description}
          />
        )}
      </div>

      {/* SOV Chart — gunakan library chart pilihan (Recharts, Chart.js, dll) */}
      <SovPieChart data={sov} />

      {/* Trend Chart */}
      <TrendLineChart data={trend} />
    </div>
  )
}
```

### Metrik per topik (saat klik topik di dashboard)

```jsx
async function loadTopicMetrics(topicId) {
  const res = await metricsService.topic(topicId, true) // breakdown_per_keyword=true
  const { metrics, keyword_breakdown } = res.data

  // metrics — angka agregat seluruh topik
  // keyword_breakdown — kontribusi tiap keyword
  return { metrics, keyword_breakdown }
}
```

---

## 8. Cara Tambah Platform Baru

Saat TikTok, Instagram, atau News connector selesai, tambahkan adapter-nya:

### Langkah 1 — Buat file adapter

```python
# app/services/metrics/adapters/tiktok.py
from .base import PlatformAdapter, PlatformFieldMap

class TikTokAdapter(PlatformAdapter):
    platform = "tiktok"
    field_map = PlatformFieldMap(
        views="views",         # play_count di TikTok normalizer
        likes="likes",         # digg_count
        shares="shares",       # share_count
        saves="saves",         # collect_count (bookmark)
        replies="replies",     # belum ada
        clicks="clicks",       # belum ada
        unavailable=["replies", "clicks"],
    )
```

### Langkah 2 — Daftarkan di registry

```python
# app/services/metrics/calculator.py — tambah 1 baris di ADAPTER_REGISTRY
from app.services.metrics.adapters.tiktok import TikTokAdapter

ADAPTER_REGISTRY: dict[str, PlatformAdapter] = {
    "youtube":   YouTubeAdapter(),
    "tiktok":    TikTokAdapter(),   # ← tambah ini
    # "instagram": InstagramAdapter(),
    # "news":      NewsAdapter(),
}
```

### Langkah 3 — Tidak perlu ubah endpoint

Endpoint langsung menerima `platforms=["youtube","tiktok"]` — kalkulasi otomatis pakai adapter yang sesuai.

---

## Catatan Penting

| Kondisi | Keterangan |
|---------|------------|
| Likes = 0 untuk YouTube | YouTube search API tidak return likes. Perlu refresh stats harian via `/youtube/video/details` |
| Exposure = snapshot statis | Views diambil saat crawl pertama. Tidak update otomatis — perlu Celery beat harian |
| SOV di `/summary` = null | SOV tidak relevan untuk summary global (tidak ada pembanding). Gunakan `/metrics/sov` |
| `date_from` / `date_to` tidak diisi | Default otomatis 30 hari terakhir |
| Mention Growth negatif | Berarti perbincangan menurun dibanding periode sebelumnya |
| Sentiment Score = 0 | Belum ada komentar yang dianalisis untuk keyword tersebut |
