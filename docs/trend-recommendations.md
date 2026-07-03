# Trend Recommendations — Rekomendasi Topik Viral dari AI Eksternal

Fitur ini menyediakan tabel + API untuk **menerima** rekomendasi topik viral
dari AI eksternal (bukan spesifik satu platform — bisa isu apapun: DPR, TNI,
tokoh publik, program pemerintah, dll), lengkap dengan akun media sosial yang
terkait. Data ini menjadi **patokan untuk tahap pencarian/scraping berikutnya**
per keyword & username yang sedang viral.

Model: [app/domain/trend_recommendations/models.py](../app/domain/trend_recommendations/models.py)
Service: [app/services/trend_recommendations/service.py](../app/services/trend_recommendations/service.py)
Router: [app/api/v1/trend_recommendations.py](../app/api/v1/trend_recommendations.py)
Migration: `011_trend_recommendations`

---

## Tabel `trend_recommendations`

| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | UUID | primary key |
| `topic` | varchar(255) | nama isu/keyword viral, misal "Bahlil Lahadalia" |
| `score` | float | skor viralitas, 0.0–1.0 |
| `related_accounts` | JSONB | list `{"platform": ..., "username": ...}` lintas media sosial |
| `source` | varchar(50) | asal data, default `external_ai` |
| `recommendation_date` | date | tanggal rekomendasi (unique bareng `topic`) |
| `status` | varchar(20) | `pending` → diubah `used` setelah dipakai pipeline pencarian |
| `raw_payload` | JSONB | payload asli submission (audit) |
| `created_at` / `updated_at` | timestamptz | dari `TimestampMixin` |

**Constraint:** `UNIQUE(topic, recommendation_date)` — topik sama di hari sama = update, bukan duplikat.

**Index:** `topic`, `recommendation_date`, `status`, gabungan `(recommendation_date, score)`.

---

## Aturan bisnis: maksimal 20 topik per hari

Logic ada di [service.py](../app/services/trend_recommendations/service.py) fungsi `submit_recommendations`:

1. **Topik sudah ada di hari itu** → update `score` + `related_accounts` (bukan insert baru).
2. **Topik baru, slot < 20** → insert langsung.
3. **Topik baru, slot sudah 20** →
   - Cari baris dengan `score` terendah di hari itu.
   - Kalau `score` topik baru **lebih tinggi** → hapus baris terendah, insert topik baru (tercatat sebagai `evicted` + `created`).
   - Kalau tidak → topik baru **ditolak** (tercatat sebagai `rejected`).
4. Kalau dalam satu payload ada topik yang sama berulang → yang dipakai cuma skor tertinggi (dedupe in-memory sebelum diproses).

---

## API

### `POST /api/v1/trend-recommendations` — publik, **tanpa autentikasi**

Sengaja tanpa auth supaya sistem AI eksternal bisa langsung submit tanpa perlu urus token/API key.

**Body:**
```json
{
  "source": "nama_ai_kamu",
  "recommendation_date": "2026-07-03",
  "items": [
    {
      "topic": "Bahlil Lahadalia",
      "score": 0.9,
      "related_accounts": [
        {"platform": "instagram", "username": "bahlillahadalia"},
        {"platform": "twitter", "username": "bahlillahadalia"}
      ]
    }
  ]
}
```
`recommendation_date` opsional — default hari ini kalau tidak diisi.

**Response:**
```json
{
  "success": true,
  "data": {
    "created": ["Bahlil Lahadalia"],
    "updated": [],
    "evicted": [],
    "rejected": []
  }
}
```

### `GET /api/v1/trend-recommendations` — butuh login (JWT/API key)

Dipakai tahap pencarian/scraping berikutnya untuk baca topik + akun yang sedang viral.

**Query params (semua opsional):**
| Param | Default | Keterangan |
|---|---|---|
| `recommendation_date` | hari ini | format `YYYY-MM-DD` |
| `platform` | — | filter topik yang punya `related_accounts` di platform ini (pakai JSONB containment `@>`) |
| `topic` | — | cari topik (partial match, `ILIKE`) |
| `limit` | 20 | maks 20 |

Contoh:
```bash
GET /api/v1/trend-recommendations?recommendation_date=2026-07-03&platform=instagram
```

---

## Contoh pakai lengkap (curl)

```bash
# Submit — publik, tanpa token
curl -X POST http://187.77.125.10:8000/api/v1/trend-recommendations \
  -H "Content-Type: application/json" \
  -d '{
    "source": "my_ai_system",
    "items": [
      {"topic": "DPR", "score": 0.95, "related_accounts": [
        {"platform": "twitter", "username": "akun_a"}
      ]}
    ]
  }'

# Baca — butuh token
TOKEN=$(curl -s -X POST http://187.77.125.10:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"EMAIL","password":"PASSWORD"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['access_token'])")

curl -s "http://187.77.125.10:8000/api/v1/trend-recommendations" \
  -H "Authorization: Bearer $TOKEN"
```

---

## Cek langsung di database (tanpa lewat API)

```bash
ssh root@187.77.125.10
docker exec -it social_intel_postgres psql -U social_intelligence -d social_intelligence_db
```
```sql
SELECT topic, score, related_accounts, status, recommendation_date
FROM trend_recommendations
ORDER BY score DESC;
```

---

## Riwayat uji coba

**2026-07-03** — Uji coba pertama pakai 5 topik viral nyata hasil web search
(Google Trends Indonesia + konfirmasi berita): Bahlil Lahadalia, Gibran
Rakabuming Raka, Pemadaman Listrik PLN, Koperasi Merah Putih, Piala Dunia 2026.
`related_accounts` diambil dari akun resmi yang ditemukan via web search
(bukan hasil crawl live cross-platform — web search tidak punya akses
real-time ke feed trending Twitter/TikTok). Semua 5 berhasil `created`.

**Catatan keterbatasan:** untuk dapat data "benar-benar viral di semua
platform" secara akurat & otomatis (bukan manual via web search), butuh salah
satu dari:
- EnsembleData aktif kembali (dipakai `instagram_trending` discovery via hashtag, lihat [instagram scrapping method.md](instagram%20scrapping%20method.md))
- API resmi/trending endpoint tiap platform (Twitter/X, TikTok, dll)
- Apify (lihat [apify-instagram-method.md](apify-instagram-method.md)) — tapi actor yang sudah diuji sifatnya scrape akun yang **sudah diketahui usernamenya**, bukan discovery/pencarian trending

---

## Deployment

Server production (`187.77.125.10`) tidak pakai bind-mount untuk folder
`migrations/` (cuma `./app:/app/app` di `docker-compose.yml`), jadi migration
baru harus di-`docker cp` manual ke container sebelum `alembic upgrade head`:

```bash
docker cp migrations/versions/011_trend_recommendations.py social_intel_api:/app/migrations/versions/
docker exec social_intel_api alembic upgrade head
docker restart social_intel_api   # supaya router baru ke-load
```
