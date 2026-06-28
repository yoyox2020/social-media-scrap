# Dokumentasi Topic-Based Search API

Fitur pencarian berdasarkan topik — data dikelompokkan per topik, disimpan ke DB, dan bisa ditampilkan di dashboard.

**Base URL Lokal:** `http://localhost:8000`
**Base URL Server:** `http://187.77.125.10:8000`
**Swagger UI:** `http://187.77.125.10:8000/docs`

---

## Daftar Isi

1. [Struktur File dan Folder](#1-struktur-file-dan-folder)
2. [Struktur Database](#2-struktur-database)
3. [Alur Sistem (Flow)](#3-alur-sistem-flow)
4. [Endpoint API](#4-endpoint-api)
5. [Contoh Penggunaan — curl](#5-contoh-penggunaan--curl)
6. [Contoh Penggunaan — Frontend React](#6-contoh-penggunaan--frontend-react)
7. [Platform yang Didukung](#7-platform-yang-didukung)

---

## 1. Struktur File dan Folder

```
social-media-scrap/
│
├── app/
│   ├── api/
│   │   └── v1/
│   │       └── topic_search.py          ← Router semua endpoint topic search
│   │
│   ├── domain/
│   │   └── search_topics/
│   │       ├── __init__.py
│   │       └── models.py                ← SQLAlchemy model: SearchTopic, SearchTopicKeyword
│   │
│   └── main.py                          ← Register router + import model
│
├── migrations/
│   └── versions/
│       └── 007_search_topics.py         ← Alembic migration: buat tabel search_topics
│
└── docs/
    └── phase-done/
        └── api-topic.md                 ← File ini
```

### Penjelasan File Utama

**`app/api/v1/topic_search.py`**
Router utama. Berisi 4 endpoint:
- `POST /search/topics` — cari + simpan topik
- `GET /search/topics/list` — daftar topik untuk dashboard
- `GET /search/topics/{id}` — detail satu topik
- `DELETE /search/topics/{id}` — hapus topik

**`app/domain/search_topics/models.py`**
Dua tabel database:
- `SearchTopic` → menyimpan nama topik, platform, jadwal crawl
- `SearchTopicKeyword` → relasi many-to-many antara topik dan keyword

**`migrations/versions/007_search_topics.py`**
Migration Alembic untuk membuat kedua tabel di atas. Dijalankan sekali dengan:
```bash
docker exec social_intel_api alembic upgrade head
```

---

## 2. Struktur Database

### Tabel `search_topics`

| Kolom | Tipe | Keterangan |
|-------|------|------------|
| `id` | UUID | Primary key |
| `name` | VARCHAR(255) | Nama topik, contoh: "Tokoh Agama" |
| `description` | TEXT | Deskripsi opsional |
| `project_id` | UUID | Relasi ke tabel projects |
| `platforms` | TEXT[] | Array platform: ["youtube", "tiktok"] |
| `scheduled_hour` | INT | Jam crawl otomatis harian (0-23), null = tidak terjadwal |
| `auto_crawl` | BOOLEAN | Crawl otomatis jika data kosong |
| `is_active` | BOOLEAN | Soft delete — false = nonaktif |
| `created_at` | TIMESTAMPTZ | Waktu dibuat |
| `updated_at` | TIMESTAMPTZ | Waktu diupdate |

### Tabel `search_topic_keywords` (relasi many-to-many)

| Kolom | Tipe | Keterangan |
|-------|------|------------|
| `topic_id` | UUID | FK → search_topics.id |
| `keyword_id` | UUID | FK → keywords.id |
| `keyword_text` | VARCHAR(255) | Teks keyword (disimpan untuk kemudahan query) |

### Relasi Antar Tabel

```
search_topics
    │
    └── search_topic_keywords (many)
            │
            └── keywords (satu keyword bisa masuk banyak topik)
                    │
                    └── posts (video/konten per keyword)
                            │
                            └── comments
                                    │
                                    └── lexicon_analyses (sentimen)
```

---

## 3. Alur Sistem (Flow)

### Flow POST /search/topics (buat topik + cari data)

```
User kirim POST dengan daftar topik + keyword
│
├── Untuk setiap topik:
│   └── Untuk setiap keyword dalam topik:
│       │
│       ├── [1] Cari keyword di tabel keywords (3 lapis):
│       │       - Exact match: "pendakwah oki setiana dewi" == "pendakwah oki setiana dewi"
│       │       - LIKE match:  "pendakwah oki" LIKE "%pendakwah oki setiana dewi%"
│       │       - Word match:  semua kata dalam query ada di keyword tersimpan
│       │
│       ├── [2a] Keyword DITEMUKAN + video ada:
│       │       → Ambil posts dari DB
│       │       → Hitung sentimen (jika include_sentiment=true)
│       │       → Status: "found"
│       │
│       ├── [2b] Keyword DITEMUKAN + video 0 + auto_crawl=true:
│       │       → Pakai keyword_id yang existing (tidak duplikat)
│       │       → Queue Celery crawl langsung dengan ID yang sama
│       │       → Status: "crawling"
│       │
│       └── [2c] Keyword TIDAK DITEMUKAN + auto_crawl=true:
│               → Buat keyword baru di tabel keywords
│               → Queue Celery task crawl YouTube di background
│               → Status: "crawling"
│
├── [3] save_topic=true:
│       → Cek apakah nama topik sudah ada di search_topics
│       → Jika sudah ada → update, tidak buat duplikat
│       → Jika belum ada → INSERT ke search_topics
│       → Link keyword ke topik via search_topic_keywords
│
└── [4] Return hasil dikelompokkan per topik
```

### Flow Dashboard (GET list → klik → GET detail)

```
Dashboard Frontend
│
├── Load halaman → GET /search/topics/list
│       → Tampilkan kartu per topik (nama, jumlah video, sentimen)
│
└── User klik topik → GET /search/topics/{topic_id}
        → Tampilkan detail: semua keyword + video + sentimen per keyword
```

---

## 4. Endpoint API

### POST /api/v1/search/topics

Cari data berdasarkan topik dan keyword, sekaligus menyimpan konfigurasi topik ke DB.

**Request Body:**
```json
{
  "topics": [
    {
      "name": "tokoh agama",
      "description": "Konten dakwah dan tokoh agama Indonesia",
      "keywords": ["pendakwah oki setiana dewi", "ustadz adi hidayat"]
    },
    {
      "name": "artis indonesia",
      "keywords": ["tantri kotak", "dpr live"]
    }
  ],
  "platforms": ["youtube"],
  "limit_per_keyword": 10,
  "include_sentiment": true,
  "include_comments": false,
  "auto_crawl": true,
  "scheduled_hour": 7,
  "save_topic": true
}
```

| Parameter | Tipe | Default | Keterangan |
|-----------|------|---------|------------|
| `topics` | array | wajib | Daftar topik + keyword |
| `platforms` | array | `["youtube"]` | Platform yang dicari |
| `limit_per_keyword` | int | 10 | Maks video per keyword |
| `include_sentiment` | bool | true | Sertakan ringkasan sentimen |
| `include_comments` | bool | false | Sertakan sample komentar |
| `auto_crawl` | bool | true | Crawl otomatis jika data kosong |
| `scheduled_hour` | int | null | Jam crawl harian (WIB), 0-23 |
| `save_topic` | bool | true | Simpan topik ke DB |

**Response:**
```json
{
  "success": true,
  "data": {
    "status": "ready",
    "platforms": ["youtube"],
    "total_topics": 2,
    "crawling_keywords": [],
    "topics": [
      {
        "topic_id": "123da021-0f59-4d7c-8be2-1b4226e3a0fe",
        "topic": "Tokoh Agama",
        "keywords": ["pendakwah oki setiana dewi"],
        "total_posts": 18,
        "status_per_keyword": {
          "pendakwah oki setiana dewi": "found"
        },
        "sentiment_per_keyword": {
          "pendakwah oki setiana dewi": {
            "total_analyzed": 57,
            "positif": {"count": 32, "pct": 56.1},
            "negatif": {"count": 4,  "pct": 7.0},
            "netral":  {"count": 21, "pct": 36.8},
            "dominant": "positif"
          }
        },
        "results": [
          {
            "id": "uuid...",
            "platform": "youtube",
            "title": "ISTIQOMAH ITU TIDAK MUDAH | Dr. Oki Setiana Dewi",
            "author": "Oki Setiana Dewi Official",
            "url": "https://www.youtube.com/watch?v=cCM2IWA9T9o",
            "view_count": 2902,
            "published_at": "2026-05-29T06:47:09+00:00",
            "thumbnail_url": "https://i.ytimg.com/vi/..."
          }
        ],
        "crawling": []
      }
    ]
  }
}
```

**Status response:**
| Status | Artinya |
|--------|---------|
| `ready` | Semua keyword ada datanya |
| `partial` | Sebagian ada, sebagian crawling |
| `crawling` | Semua keyword sedang di-crawl |

---

### GET /api/v1/search/topics/list

Daftar semua topik tersimpan — untuk halaman utama dashboard.

**Query Parameters:**
| Parameter | Default | Keterangan |
|-----------|---------|------------|
| `is_active` | true | Filter topik aktif saja |
| `limit` | 50 | Jumlah topik |
| `offset` | 0 | Pagination |

**Response:**
```json
{
  "success": true,
  "data": {
    "total": 2,
    "offset": 0,
    "items": [
      {
        "topic_id": "123da021-...",
        "name": "Tokoh Agama",
        "description": "Konten dakwah Indonesia",
        "platforms": ["youtube"],
        "keywords": ["pendakwah oki setiana dewi"],
        "total_keywords": 1,
        "total_posts": 18,
        "total_comments": 57,
        "scheduled_hour": 7,
        "auto_crawl": true,
        "is_active": true,
        "created_at": "2026-06-28T13:00:00+00:00",
        "updated_at": "2026-06-28T13:00:00+00:00"
      }
    ]
  }
}
```

---

### GET /api/v1/search/topics/{topic_id}

Detail satu topik lengkap dengan posts dan sentimen — dipanggil saat user klik topik di dashboard.

**Query Parameters:**
| Parameter | Default | Keterangan |
|-----------|---------|------------|
| `limit_per_keyword` | 10 | Jumlah video per keyword |
| `include_sentiment` | true | Sertakan sentimen |

**Response:**
```json
{
  "success": true,
  "data": {
    "topic_id": "123da021-...",
    "name": "Tokoh Agama",
    "platforms": ["youtube"],
    "total_keywords": 1,
    "total_posts": 10,
    "keyword_details": [
      {
        "keyword": "pendakwah oki setiana dewi",
        "keyword_id": "3c151423-...",
        "total_posts": 10,
        "sentiment": {
          "dominant": "positif",
          "total_analyzed": 57,
          "positif": {"count": 32, "pct": 56.1},
          "negatif": {"count": 4,  "pct": 7.0},
          "netral":  {"count": 21, "pct": 36.8}
        },
        "posts": [...]
      }
    ]
  }
}
```

---

### DELETE /api/v1/search/topics/{topic_id}

Nonaktifkan topik (soft delete — data tidak dihapus dari DB).

**Response:**
```json
{
  "success": true,
  "data": {"message": "Topik 'Tokoh Agama' dinonaktifkan"}
}
```

---

## 5. Contoh Penggunaan — curl

### Login (satu kali, simpan TOKEN)

```bash
TOKEN=$(curl -s -X POST http://187.77.125.10:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"yahyamatoristmik@gmail.com","password":"admin123"}' \
  | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
```

### Buat satu topik

```bash
curl -X POST http://187.77.125.10:8000/api/v1/search/topics \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "topics": [
      {"name": "tokoh agama", "keywords": ["pendakwah oki setiana dewi"]}
    ],
    "platforms": ["youtube"],
    "include_sentiment": true,
    "save_topic": true
  }'
```

### Buat banyak topik sekaligus

```bash
curl -X POST http://187.77.125.10:8000/api/v1/search/topics \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "topics": [
      {
        "name": "jawa timur",
        "keywords": ["polisi ditembak preman", "bupati surabaya"]
      },
      {
        "name": "jawa tengah",
        "keywords": ["hamengkubuwono", "ojon jogja"]
      },
      {
        "name": "artis indonesia",
        "keywords": ["tantri kotak", "dpr live"]
      }
    ],
    "platforms": ["youtube"],
    "auto_crawl": true,
    "save_topic": true,
    "scheduled_hour": 7
  }'
```

### Lihat semua topik (dashboard)

```bash
curl "http://187.77.125.10:8000/api/v1/search/topics/list" \
  -H "Authorization: Bearer $TOKEN"
```

### Detail topik tertentu

```bash
# Ganti <topic_id> dengan UUID dari response POST atau GET list
curl "http://187.77.125.10:8000/api/v1/search/topics/<topic_id>" \
  -H "Authorization: Bearer $TOKEN"
```

### Hapus topik

```bash
curl -X DELETE "http://187.77.125.10:8000/api/v1/search/topics/<topic_id>" \
  -H "Authorization: Bearer $TOKEN"
```

### Auto Crawl — keyword belum ada, crawl otomatis

```bash
curl -X POST http://187.77.125.10:8000/api/v1/search/topics \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "topics": [
      {
        "name": "jawa timur",
        "keywords": ["banjir surabaya 2026", "demo buruh jatim"]
      }
    ],
    "platforms": ["youtube"],
    "auto_crawl": true,
    "save_topic": true
  }'
```

Response saat keyword belum ada (crawl berjalan di background):
```json
{
  "status": "crawling",
  "crawling_keywords": ["banjir surabaya 2026", "demo buruh jatim"],
  "note": "Keyword dengan status 'crawling' sedang diproses di background.",
  "topics": [
    {
      "topic": "Jawa Timur",
      "topic_id": "uuid-tersimpan...",
      "total_posts": 0,
      "crawling": ["banjir surabaya 2026", "demo buruh jatim"]
    }
  ]
}
```

Tunggu 1-3 menit, lalu panggil endpoint yang sama → data sudah muncul.

### Cek status crawl (opsional, sambil menunggu)

```bash
# keyword_id diambil dari response crawl di atas
curl "http://187.77.125.10:8000/api/v1/youtube/status?keyword_id=<keyword_id>" \
  -H "Authorization: Bearer $TOKEN"
```

### One-liner lengkap: login + buat topik + lihat list

```bash
TOKEN=$(curl -s -X POST http://187.77.125.10:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"yahyamatoristmik@gmail.com","password":"admin123"}' \
  | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4) && \
curl -X POST http://187.77.125.10:8000/api/v1/search/topics \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"topics":[{"name":"tokoh agama","keywords":["pendakwah oki setiana dewi"]}],"platforms":["youtube"],"include_sentiment":true,"save_topic":true}' && \
curl "http://187.77.125.10:8000/api/v1/search/topics/list" \
  -H "Authorization: Bearer $TOKEN"
```

---

## 6. Contoh Penggunaan — Frontend React

### Setup: helper login dan simpan token

```js
// utils/api.js
const BASE_URL = 'http://187.77.125.10:8000/api/v1'

export async function login(email, password) {
  const res = await fetch(`${BASE_URL}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password })
  })
  const { data } = await res.json()
  localStorage.setItem('token', data.access_token)
  return data.access_token
}

function authHeaders() {
  return {
    'Authorization': `Bearer ${localStorage.getItem('token')}`,
    'Content-Type': 'application/json'
  }
}
```

### Buat topik baru (dari form input user)

```js
// Dipanggil saat user submit form "Tambah Topik"
async function createTopic(topicName, keywords) {
  const res = await fetch(`${BASE_URL}/search/topics`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({
      topics: [
        { name: topicName, keywords: keywords }
      ],
      platforms: ['youtube'],
      include_sentiment: true,
      auto_crawl: true,
      save_topic: true
    })
  })
  const { data } = await res.json()
  return data // berisi topic_id, results, sentiment
}

// Contoh pemanggilan
createTopic('tokoh agama', ['pendakwah oki setiana dewi', 'ustadz adi hidayat'])
```

### Ambil daftar topik untuk halaman dashboard

```js
// Dipanggil saat halaman dashboard dimuat
async function getTopicList() {
  const res = await fetch(`${BASE_URL}/search/topics/list`, {
    headers: authHeaders()
  })
  const { data } = await res.json()
  return data.items  // array of topics
}

// Contoh render di React
function Dashboard() {
  const [topics, setTopics] = useState([])

  useEffect(() => {
    getTopicList().then(setTopics)
  }, [])

  return (
    <div>
      {topics.map(topic => (
        <TopicCard
          key={topic.topic_id}
          name={topic.name}
          totalPosts={topic.total_posts}
          totalComments={topic.total_comments}
          keywords={topic.keywords}
          onClick={() => openTopicDetail(topic.topic_id)}
        />
      ))}
    </div>
  )
}
```

### Ambil detail topik (saat user klik)

```js
async function getTopicDetail(topicId) {
  const res = await fetch(`${BASE_URL}/search/topics/${topicId}?include_sentiment=true`, {
    headers: authHeaders()
  })
  const { data } = await res.json()
  return data
}

// Contoh render detail
function TopicDetail({ topicId }) {
  const [detail, setDetail] = useState(null)

  useEffect(() => {
    getTopicDetail(topicId).then(setDetail)
  }, [topicId])

  if (!detail) return <Loading />

  return (
    <div>
      <h1>{detail.name}</h1>
      <p>Total Video: {detail.total_posts}</p>

      {detail.keyword_details.map(kw => (
        <div key={kw.keyword}>
          <h3>{kw.keyword}</h3>
          <SentimentBar data={kw.sentiment} />
          <VideoList videos={kw.posts} />
        </div>
      ))}
    </div>
  )
}
```

### Hapus topik

```js
async function deleteTopic(topicId) {
  const res = await fetch(`${BASE_URL}/search/topics/${topicId}`, {
    method: 'DELETE',
    headers: authHeaders()
  })
  const { data } = await res.json()
  return data.message
}
```

### Lengkap: semua fungsi dalam satu file service

```js
// services/topicService.js
const BASE_URL = 'http://187.77.125.10:8000/api/v1'

const headers = () => ({
  'Authorization': `Bearer ${localStorage.getItem('token')}`,
  'Content-Type': 'application/json'
})

export const topicService = {
  // Buat topik baru
  create: (topics, options = {}) =>
    fetch(`${BASE_URL}/search/topics`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({
        topics,
        platforms: options.platforms ?? ['youtube'],
        include_sentiment: options.sentiment ?? true,
        auto_crawl: options.autoCrawl ?? true,
        scheduled_hour: options.scheduledHour ?? null,
        save_topic: true
      })
    }).then(r => r.json()),

  // Daftar semua topik
  list: (params = {}) => {
    const q = new URLSearchParams({ is_active: true, limit: 50, ...params })
    return fetch(`${BASE_URL}/search/topics/list?${q}`, { headers: headers() }).then(r => r.json())
  },

  // Detail satu topik
  detail: (topicId, limitPerKeyword = 10) =>
    fetch(`${BASE_URL}/search/topics/${topicId}?limit_per_keyword=${limitPerKeyword}`, {
      headers: headers()
    }).then(r => r.json()),

  // Hapus topik
  delete: (topicId) =>
    fetch(`${BASE_URL}/search/topics/${topicId}`, {
      method: 'DELETE',
      headers: headers()
    }).then(r => r.json()),
}

// Contoh pemakaian di komponen
// const { data } = await topicService.create([{ name: 'Jawa Timur', keywords: ['banjir', 'gempa'] }])
// const { data } = await topicService.list()
// const { data } = await topicService.detail('123da021-...')
```

---

## 7. Platform yang Didukung

| Platform | Status | Keterangan |
|----------|--------|------------|
| `youtube` | ✅ Aktif | Connector via EnsembleData API |
| `tiktok` | 🔜 Belum | Connector belum dibuat |
| `instagram` | 🔜 Belum | Connector belum dibuat |
| `news` | 🔜 Belum | Connector belum dibuat |

Saat connector platform baru selesai, tidak perlu ubah endpoint topic search. Cukup tambahkan nama platform di field `platforms`:

```json
{
  "platforms": ["youtube", "tiktok", "instagram", "news"]
}
```

---

## Catatan Penting

| Kondisi | Perilaku |
|---------|----------|
| Topik nama sama di-POST ulang | Update (tidak duplikat) |
| Keyword tidak ada di DB + `auto_crawl: true` | Keyword baru dibuat → crawl berjalan di background |
| Keyword ada di DB tapi video 0 + `auto_crawl: true` | Pakai keyword_id existing → crawl ulang (tidak duplikat) |
| Keyword ada di DB + video sudah ada | Langsung kembalikan data, tidak crawl ulang |
| `scheduled_hour: 7` | Crawl otomatis setiap hari jam 07:00 WIB |
| Keyword dicari dengan LIKE | `"pendakwah oki"` akan cocok dengan `"pendakwah oki setiana dewi"` |
| `save_topic: false` | Cari saja, tidak simpan ke DB |
| `DELETE /topics/{id}` | Soft delete — topik hanya dinonaktifkan, data posts tidak hilang |

### Perilaku Auto Crawl

```
POST /search/topics
│
├── Keyword tidak ada di DB + auto_crawl: true
│   → Buat keyword baru → queue Celery crawl ✅
│
├── Keyword ada di DB, video 0 + auto_crawl: true
│   → Pakai keyword_id existing (tidak duplikat) → queue Celery crawl ✅
│
└── Keyword ada di DB, video sudah ada
    → Langsung kembalikan data, tidak crawl ulang ✅
```

Crawl berjalan di background. Tunggu 1-3 menit lalu panggil endpoint yang sama — data sudah muncul.