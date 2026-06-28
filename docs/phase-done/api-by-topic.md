# Contoh Penggunaan API Berdasarkan Topik

Panduan praktis: bagaimana menggunakan API sesuai kebutuhan nyata.
Semua contoh menggunakan `curl`. Ganti `<TOKEN>` dengan token dari login.

---

## Cara Login Satu Kali (Token Berlaku 30 Hari)

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "yahyamatoristmik@gmail.com", "password": "admin123"}'
```

Simpan token:
```bash
TOKEN="eyJhbGci..."   # paste access_token dari response
```

> **Swagger UI:** Buka `http://localhost:8000/docs` → klik tombol **Authorize** (kanan atas)
> → isi `Bearer <token>` → klik Authorize.
> Token **tidak akan hilang** meski halaman di-refresh (tersimpan di localStorage browser).

---

## TOPIK 1 — Pantau Isu Sosial / Politik

**Contoh:** Demo DPRD, banjir Jakarta, kemacetan tol

### Langkah 1: Cari & Crawl Otomatis
```bash
curl -X POST http://localhost:8000/api/v1/youtube/smart-search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"q": "demo mahasiswa jakarta", "max_pages": 2, "max_comments_per_video": 100}'
```

### Langkah 2: Lihat Sentimen Publik
```bash
# Ganti <keyword_id> dengan uuid dari response langkah 1
curl "http://localhost:8000/api/v1/youtube/sentiment/distribution?keyword_id=<keyword_id>" \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**
```json
{
  "keyword_text": "demo mahasiswa jakarta",
  "total_comments": 156,
  "distribution": [
    {"label": "positif", "count": 89, "percentage": 57.1},
    {"label": "netral",  "count": 45, "percentage": 28.8},
    {"label": "negatif", "count": 22, "percentage": 14.1}
  ]
}
```

### Langkah 3: Lihat Komentar Negatif (kritik publik)
```bash
curl "http://localhost:8000/api/v1/youtube/sentiment/table?keyword_id=<keyword_id>&limit=20" \
  -H "Authorization: Bearer $TOKEN"
```

### Langkah 4: Kata yang Sering Muncul di Komentar Negatif
```bash
curl "http://localhost:8000/api/v1/youtube/wordcloud?keyword_id=<keyword_id>&sentiment=negatif" \
  -H "Authorization: Bearer $TOKEN"
```

### Langkah 5: Filter Komentar Berdasarkan Tanggal Kejadian
```bash
# Komentar yang dibuat antara 1-7 Juni 2026
curl "http://localhost:8000/api/v1/youtube/comments?keyword_id=<keyword_id>&date_from=2026-06-01&date_to=2026-06-07" \
  -H "Authorization: Bearer $TOKEN"
```

---

## TOPIK 2 — Analisis Brand / Artis / Public Figure

**Contoh:** Tantri Kotak, band Indonesia, penyanyi

### Cari Data
```bash
curl -X POST http://localhost:8000/api/v1/youtube/smart-search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"q": "tantri kotak", "max_pages": 3, "max_comments_per_video": 200}'
```

### Ambil Data yang Sudah Ada (tanpa crawl ulang)
```bash
curl "http://localhost:8000/api/v1/youtube/smart-search?q=tantri+kotak" \
  -H "Authorization: Bearer $TOKEN"
```

### Lihat Video Populer (sort by views)
```bash
curl "http://localhost:8000/api/v1/youtube/videos?keyword_id=<keyword_id>&limit=10" \
  -H "Authorization: Bearer $TOKEN"
```

### Kata yang Sering Disebut Penggemar (komentar positif)
```bash
curl "http://localhost:8000/api/v1/youtube/wordcloud?keyword_id=<keyword_id>&sentiment=positif" \
  -H "Authorization: Bearer $TOKEN"
```

**Contoh response word cloud positif:**
```json
{
  "words": [
    {"word": "keren",   "count": 45},
    {"word": "bagus",   "count": 38},
    {"word": "merinding","count": 31},
    {"word": "mantap",  "count": 28},
    {"word": "vocalis", "count": 19}
  ]
}
```

### Perbandingan Sentimen Antar Keyword (manual loop)
```bash
for keyword in "tantri kotak" "sheila on 7" "noah band"; do
  echo "=== $keyword ==="
  curl -s "http://localhost:8000/api/v1/youtube/smart-search?q=$(echo $keyword | sed 's/ /+/g')" \
    -H "Authorization: Bearer $TOKEN" | python3 -c "
import sys, json
d = json.load(sys.stdin)
if d.get('status') == 'found':
    s = d['sentiment_summary']
    total = sum(s.values()) or 1
    print(f'Positif: {s.get(\"positif\",0)} ({s.get(\"positif\",0)*100//total}%)')
    print(f'Negatif: {s.get(\"negatif\",0)} ({s.get(\"negatif\",0)*100//total}%)')
else:
    print('Belum ada data, gunakan POST untuk crawl')
"
done
```

---

## TOPIK 3 — Kuliner / Lifestyle / Produk

**Contoh:** Nasi goreng spesial, tongseng ayam, resep viral

### Cari Konten Kuliner
```bash
curl -X POST http://localhost:8000/api/v1/youtube/smart-search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"q": "nasi goreng spesial", "max_pages": 2, "max_comments_per_video": 50}'
```

### Komentar Paling Recent (orang nanya resep, dll)
```bash
# 20 komentar terbaru
curl "http://localhost:8000/api/v1/youtube/comments?keyword_id=<keyword_id>&limit=20" \
  -H "Authorization: Bearer $TOKEN"
```

### Crawl Ulang (update data terbaru)
```bash
curl -X POST http://localhost:8000/api/v1/youtube/smart-search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"q": "tongseng ayam", "force_refresh": true}'
```
> `force_refresh: true` → kembalikan data lama langsung, crawl baru berjalan di background.

---

## TOPIK 4 — Monitor Trending Real-time

### Fetch Trending dari Google Trends Indonesia
```bash
curl -X POST http://localhost:8000/api/v1/youtube/trending/fetch \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "geo": "ID",
    "period": "24h",
    "limit": 10,
    "project_id": "<project_uuid>",
    "auto_collect": true,
    "max_pages_per_keyword": 1
  }'
```

**Response:**
```json
{
  "geo": "ID",
  "period": "24h",
  "fetched_at": "2026-06-28T04:30:00+00:00",
  "items": [
    {"rank": 1, "title": "Banjir Jakarta", "traffic": "500K+", "published_at": "2026-06-28T03:00:00+00:00"},
    {"rank": 2, "title": "Demo Mahasiswa",  "traffic": "200K+", "published_at": "2026-06-28T02:00:00+00:00"}
  ],
  "keywords_created": 10,
  "jobs_queued": 10
}
```

### Lihat Semua Trending yang Tersimpan
```bash
curl "http://localhost:8000/api/v1/youtube/trending?limit=20" \
  -H "Authorization: Bearer $TOKEN"
```

### Setelah Trending Dikumpulkan → Cek Sentimen Tiap Topik
```bash
# Ambil keyword_id topik trending dari DB dulu
curl "http://localhost:8000/api/v1/youtube/smart-search?q=banjir+jakarta" \
  -H "Authorization: Bearer $TOKEN"

# Lalu cek sentimennya
curl "http://localhost:8000/api/v1/youtube/sentiment/distribution?keyword_id=<keyword_id>" \
  -H "Authorization: Bearer $TOKEN"
```

---

## TOPIK 5 — Dashboard & Laporan

### Ringkasan Semua Data Sekaligus
```bash
curl "http://localhost:8000/api/v1/youtube/dashboard" \
  -H "Authorization: Bearer $TOKEN"
```

**Response ringkas:**
```json
{
  "summary": {
    "total_keywords": 20,
    "total_videos": 166,
    "total_comments": 783,
    "total_analyzed": 783,
    "total_trending_today": 26
  },
  "sentiment_overview": [
    {"label": "positif", "count": 621, "percentage": 79.3},
    {"label": "netral",  "count": 120, "percentage": 15.3},
    {"label": "negatif", "count": 42,  "percentage": 5.4}
  ],
  "keyword_summaries": [...]
}
```

### Status Detail per Keyword
```bash
curl "http://localhost:8000/api/v1/youtube/status?keyword_id=<keyword_id>" \
  -H "Authorization: Bearer $TOKEN"
```

---

## TOPIK 6 — Filter Komentar Berdasarkan Waktu

Berguna untuk: analisis komentar sebelum/sesudah suatu event.

```bash
# Komentar yang ditulis pada video lama (2022-2023)
curl "http://localhost:8000/api/v1/youtube/comments?keyword_id=<keyword_id>&date_from=2022-01-01&date_to=2023-12-31" \
  -H "Authorization: Bearer $TOKEN"

# Komentar yang ditulis pukul 20.00-21.00 (prime time)
curl "http://localhost:8000/api/v1/youtube/comments?keyword_id=<keyword_id>&hour=20" \
  -H "Authorization: Bearer $TOKEN"

# Video yang diterbitkan tahun 2024
curl "http://localhost:8000/api/v1/youtube/videos?keyword_id=<keyword_id>&date_from=2024-01-01&date_to=2024-12-31" \
  -H "Authorization: Bearer $TOKEN"
```

---

## Di Swagger UI (tanpa curl)

1. Buka `http://localhost:8000/docs`
2. Klik **Authorize** (kanan atas) → isi `Bearer eyJhbGci...` → **Authorize**
3. Token tersimpan otomatis di browser (tidak hilang meski di-refresh)
4. Klik endpoint → **Try it out** → isi parameter → **Execute**

### Urutan endpoint yang disarankan di Swagger:
1. `POST /auth/login` → copy `access_token` → klik Authorize
2. `POST /youtube/smart-search` → isi keyword → Execute
3. `GET /youtube/sentiment/distribution` → paste `keyword_id` dari step 2
4. `GET /youtube/wordcloud` → filter sentiment positif/negatif
5. `GET /youtube/dashboard` → lihat ringkasan semua

---

## Catatan Token & Keamanan

| Hal | Keterangan |
|---|---|
| Masa berlaku token | **30 hari** (expire 2026-07-28) |
| Swagger persist | Ya — tersimpan di localStorage browser |
| Ganti device/browser | Perlu login ulang sekali |
| Token expired | Login ulang → dapat token baru 30 hari |
| Alternatif JWT | Gunakan **API Key** (`POST /auth/api-keys`) — tidak expired |

### Buat API Key Permanen (alternatif JWT)
```bash
curl -X POST http://localhost:8000/api/v1/auth/api-keys \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "MyApp Key", "description": "Key untuk akses API permanen"}'
```

**Response:**
```json
{
  "key": "sk-xxxxxxxxxxxxxxxxxxxxxxxx",
  "name": "MyApp Key"
}
```

Gunakan API Key di header:
```bash
curl "http://localhost:8000/api/v1/youtube/dashboard" \
  -H "X-API-Key: sk-xxxxxxxxxxxxxxxxxxxxxxxx"
```

> API Key tidak punya masa berlaku. Cocok untuk integrasi dengan sistem lain.
