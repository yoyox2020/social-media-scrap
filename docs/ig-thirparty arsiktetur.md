# Instagram Integration — Dokumentasi Session

## Status Per Session Ini

| Endpoint | Status | Catatan |
|----------|--------|---------|
| `GET /instagram/posts` | ✅ Aktif | EnsembleData, limit 5 post + 5 komentar/hari |
| `GET /instagram/profile` | ⚠️ Partial | Butuh `INSTAGRAM_SESSION_ID` di .env |
| `GET /instagram/trending` | ✅ Dibangun | Data dari Celery Beat 09:00 WIB |
| `GET /facebook/posts` | ⏳ Pending | Terblokir Workplace app limitation |
| `GET /facebook/search` | ⏳ Pending | Terblokir Workplace app limitation |

---

## 1. GET /instagram/posts

**File:** `app/api/v1/instagram/router.py`
**Pipeline:** `app/services/instagram/pipeline_service.py`
**Connector:** `app/integrations/instagram/connector.py`

### Batasan yang diterapkan
- **5 post per hari per username** — hard limit, tidak bisa diubah via parameter
- **5 komentar terpopuler per post** — sorting `popular` via EnsembleData
- **Daily scrape guard** — jika sudah di-scrape hari ini (`collected_at::date = CURRENT_DATE`), scrape dilewati otomatis
- `force_refresh=true` tetap dibatasi 1x per hari per username

### Kenapa dibatasi
EnsembleData menggunakan sistem token/kredit. Dengan 5 post + 5 komentar × N akun, konsumsi token bisa cepat habis. Limit ini menjaga agar token bertahan lebih lama.

### Response scrape info
```json
{
  "scrape": {
    "executed": true,
    "skipped_reason": null,
    "posts_scraped": 5,
    "posts_new": 3,
    "daily_limit": 5,
    "errors": []
  }
}
```

### Data masuk ke tabel
- `posts` (platform='instagram', author=username)
- `comments` (post_id → posts.id)
- `lexicon_analyses` (comment_id → comments.id)

---

## 2. GET /instagram/profile

**File:** `app/api/v1/instagram/router.py`

### Pendekatan
Menggunakan Instagram internal API (`www.instagram.com/api/v1/users/web_profile_info/`) dengan browser-style headers. **Bukan EnsembleData.**

### Masalah yang ditemukan
Instagram memblokir request dari IP datacenter. Solusi: gunakan `sessionid` cookie dari browser yang sudah login Instagram.

### Cara setup (sekali saja)
1. Buka instagram.com di browser, F12 → Application → Cookies
2. Copy nilai cookie `sessionid`
3. Set di server: `echo 'INSTAGRAM_SESSION_ID=<nilai>' >> /root/social-media-scrap/.env`
4. `docker compose restart api`

### Return data
- Profile lengkap: username, full_name, followers, following, post_count, bio, verified, is_private
- 12 recent posts: shortcode, url, thumbnail, caption, likes, comment_count

### Catatan
Session ID berlaku berbulan-bulan selama tidak logout manual. Jika expired, perbarui nilai di `.env` dan restart API.

---

## 3. GET /instagram/trending

**File:** `app/api/v1/instagram/router.py`

### Fungsi
Menampilkan top 5 akun Instagram trending beserta 2 post terbaru dan 5 komentar terpopuler per post, lengkap dengan analisis sentimen.

### Response
```json
{
  "platform": "instagram",
  "total_accounts": 5,
  "updated_daily": "09:00 WIB",
  "accounts": [
    {
      "rank": 1,
      "username": "tukang_jelajah",
      "followers": 15000,
      "trending_score": 14.15,
      "engagement_rate": 25.53,
      "virality_score": 2.77,
      "source": "ensembledata",
      "discovered_via": "#indonesia",
      "sentiment": { "positif": {...}, "negatif": {...}, "netral": {...} },
      "posts": [ { "caption": "...", "likes": 3200, "comments": [...] } ]
    }
  ]
}
```

---

## 4. Arsitektur Instagram Trending Discovery

```
┌─────────────────────────────────────────────────────┐
│              TrendingDiscoveryProvider              │
│                  (interface/base)                   │
│    app/services/instagram_trending/providers/base.py│
│         discover(hashtags, limit) → list            │
└──────────────┬──────────────────────────────────────┘
               │
       ┌───────┴────────────────┐
       │                        │          ← plug in kapan saja
EnsembleDataDiscovery    [ThirdParty]
(providers/ensemble_data.py)   Discovery
Search: #indonesia              (nanti: RapidAPI,
        #viral                   SocialBlade,
        #fyp                     Modash, dll)
        #trending
        #indonesiatrending
       │
       ▼
┌─────────────────────────────────────────┐
│           TrendingScorer                │
│   app/services/instagram_trending/      │
│   scorer.py                             │
│                                         │
│   engagement_rate = (avg_likes +        │
│     avg_comments×2) / followers × 100  │
│   virality_score  = avg_views /         │
│     followers                           │
│   trending_score  = engagement×0.5 +   │
│     virality×0.5                        │
└─────────┬───────────────────────────────┘
          │ top 5 accounts
          ▼
┌─────────────────────────────────────────┐
│         Auto-scrape (Service)           │
│   app/services/instagram_trending/      │
│   service.py → run_daily_trending()     │
│                                         │
│   2 post/akun × 5 akun = 10 post/hari  │
│   5 komentar/post × 10 = 50 kmt/hari   │
└─────────┬───────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────┐
│      Celery Beat — 09:00 WIB harian     │
│   workers.instagram_trending.daily      │
│   app/workers/instagram_trending_       │
│   worker.py                             │
└─────────┬───────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────┐
│         Sentiment Analysis              │
│   lexicon (app/ai/lexicon/service.py)   │
│   → label: positif / negatif / netral  │
│   → masuk tabel lexicon_analyses        │
└─────────────────────────────────────────┘
```

---

## 5. Tabel Database

### Tabel baru: `instagram_trending_accounts`
Menyimpan **daftar akun yang dipantau** — bukan konten post.

```sql
CREATE TABLE instagram_trending_accounts (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username         VARCHAR(100)  NOT NULL,
    display_name     VARCHAR(255)  NOT NULL DEFAULT '',
    source           VARCHAR(50)   NOT NULL DEFAULT 'ensembledata',
    discovered_via   VARCHAR(255),          -- hashtag asal: '#indonesia'
    rank             INTEGER,               -- 1–5
    trending_score   FLOAT         NOT NULL DEFAULT 0,
    engagement_rate  FLOAT         NOT NULL DEFAULT 0,
    virality_score   FLOAT         NOT NULL DEFAULT 0,
    followers        INTEGER       NOT NULL DEFAULT 0,
    posts_collected  INTEGER       NOT NULL DEFAULT 0,
    status           VARCHAR(20)   NOT NULL DEFAULT 'active',
    last_scraped_date DATE,
    scrape_logs      JSONB         NOT NULL DEFAULT '[]',
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
```

### Tabel lama yang tetap dipakai
| Tabel | Isi |
|-------|-----|
| `posts` | Konten post Instagram (platform='instagram') |
| `comments` | Komentar per post |
| `lexicon_analyses` | Hasil sentimen per komentar |

---

## 6. File Structure

```
app/
├── domain/instagram_trending/
│   ├── __init__.py
│   └── models.py                    ← InstagramTrendingAccount model
│
├── services/instagram_trending/
│   ├── __init__.py
│   ├── scorer.py                    ← hitung trending_score
│   ├── service.py                   ← orkestrasi discover→score→scrape
│   └── providers/
│       ├── __init__.py
│       ├── base.py                  ← BaseDiscoveryProvider (abstract)
│       └── ensemble_data.py         ← EnsembleData hashtag search
│
├── workers/
│   └── instagram_trending_worker.py ← Celery tasks
│
└── api/v1/instagram/
    └── router.py                    ← GET /profile, /posts, /trending

scripts/
├── create_instagram_trending_table.sql  ← DDL untuk buat tabel
├── seed_instagram_trending_test.sql     ← Data test manual
└── test_instagram_trending_scorer.py   ← Unit test scorer lokal
```

---

## 7. Cara Menambah Third-Party Provider Baru

Saat sudah ada RapidAPI, SocialBlade, atau provider lain:

**Step 1** — Buat file `app/services/instagram_trending/providers/rapidapi.py`:
```python
from app.services.instagram_trending.providers.base import BaseDiscoveryProvider

class RapidAPIDiscovery(BaseDiscoveryProvider):
    name = "rapidapi"

    async def discover(self, hashtags, limit=20):
        # implementasi call RapidAPI
        ...
```

**Step 2** — Daftarkan di `app/services/instagram_trending/service.py`:
```python
from app.services.instagram_trending.providers.rapidapi import RapidAPIDiscovery

PROVIDERS = {
    "ensembledata": EnsembleDataDiscovery,
    "rapidapi":     RapidAPIDiscovery,   # ← tambah di sini
}
```

**Step 3** — Pilih provider saat trigger task:
```bash
# Via Celery
celery call workers.instagram_trending.daily --kwargs='{"provider": "rapidapi"}'
```

Tidak ada perubahan lain yang diperlukan.

---

## 8. Cara Testing

### Tahap 1 — Scorer (lokal, tanpa server)
```bash
py scripts/test_instagram_trending_scorer.py
```

### Tahap 2 — API dengan data manual (butuh server)
```bash
# Insert data test
docker exec social_intel_db psql -U social_intelligence -d social_intelligence_db \
  -f scripts/seed_instagram_trending_test.sql

# Test endpoint
curl http://localhost:8000/api/v1/instagram/trending \
  -H 'Authorization: Bearer <jwt>'
```

### Tahap 3 — E2E full (butuh EnsembleData aktif)
```bash
# Trigger manual tanpa tunggu jam 09:00
docker exec social_intel_api python3 -c "
import asyncio
from app.infrastructure.database.connection import AsyncSessionLocal
from app.services.instagram_trending.service import run_daily_trending

async def main():
    async with AsyncSessionLocal() as db:
        result = await run_daily_trending(db)
        print(result)

asyncio.run(main())
"
```

---

## 9. Catatan Facebook (Pending)

Token Meta yang dimiliki adalah **Workplace app** — tidak bisa mengakses Facebook page publik manapun.

Endpoint `GET /facebook/posts` dan `GET /facebook/search` sudah dibangun di `app/api/v1/facebook/router.py` tetapi belum bisa digunakan sampai ada:
- App Facebook baru dengan tipe **Business** (bukan Workplace)
- Feature **Page Public Content Access** diapprove Meta
- Atau third-party seperti RapidAPI Facebook scraper

Token yang ada (`FACEBOOK_ACCESS_TOKEN` di .env) masih berguna untuk:
- Akses 3 akun Instagram bisnis yang terhubung ke Facebook Pages: `@cn_digital`, `@toraja.web.id`, `@celebestekno`
- Permissions aktif: `instagram_basic`, `instagram_manage_insights`, `pages_read_engagement`

---

## 10. Rencana Berikutnya

- [ ] Auto-scrape komentar lebih banyak dari akun trending yang sama (on-demand, bukan harian)
- [ ] Tambah endpoint monitoring trending di `/scraping-status`
- [ ] Test E2E setelah EnsembleData aktif kembali
- [ ] Integrasi third-party baru untuk discovery (RapidAPI / SocialBlade)
- [ ] Facebook endpoint aktif setelah dapat app non-Workplace
