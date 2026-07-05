# Fix & Update: GET Instagram Post by Keyword/Hashtag

Dokumentasi fitur baru yang dibangun 05 Juli 2026: `GET /instagram/posts/search`
— endpoint untuk cari post Instagram yang sudah di-scrape berdasarkan
**keyword atau hashtag** (bukan username — untuk username sudah ada
`GET /instagram/posts`).

Lihat juga: `docs/dok-instagram-data-pipeline.md` (skema tabel post/comment/
hashtag/sentimen), `docs/dok-ai-viral-discovery-otomatis.md` (alur
`trend_recommendations` → scrape Instagram).

---

## Latar belakang & desain yang dikonfirmasi

User minta fitur cari post berdasarkan keyword/hashtag, mirip data yang
ditampilkan YouTube (post, nama user, likes, komentar, sentimen). Desain
final dikonfirmasi lewat tanya-jawab sebelum dibangun:

| Pertanyaan | Keputusan |
|---|---|
| Sentimen post & komentar dipisah atau digabung? | **Digabung** jadi satu ringkasan per post |
| Kalau kuota scraping habis saat mau auto-scrape? | **Jadi/tetap `pending`** di `trend_recommendations`, otomatis kepilih batch berikutnya — muncul di monitor tanpa kode tambahan |
| Kalau keyword tidak ketemu di post DAN tidak ada topik cocok sama sekali? | **Pesan "tidak ditemukan" saja** — TIDAK auto-submit topik kosong tanpa akun (itu cuma akan menumpuk sia-sia karena tidak akan pernah kepilih pipeline scrape yang butuh akun instagram) |
| Cari berdasarkan apa? | **Isi POST (caption) dan HASHTAG** — bukan username (username sudah ada endpoint sendiri) |

---

## Alur lengkap

```
GET /instagram/posts/search?q=<keyword>
        │
        ▼
[1] Cari di posts.content (ILIKE) ATAU entities (entity_type='HASHTAG')
    platform='instagram'
        │
        ├─ KETEMU → build_search_items() → return source:"database"
        │           (post + author + likes + comments_count + sentimen
        │           gabungan post+komentar + daftar komentar)
        │
        └─ TIDAK KETEMU
                │
                ▼
        [2] Cari topik cocok di trend_recommendations (ILIKE topic, urut
            score tertinggi) yang punya related_account platform instagram
            — DIBACA SAJA, tidak mengubah logika submit/consume aslinya
                │
                ├─ TIDAK KETEMU topik juga
                │       → return source:"not_found" + pesan jelas
                │         (Apify tidak bisa cari-by-keyword sendiri)
                │
                └─ KETEMU topik + akun instagram-nya
                        │
                        ▼
                [3] Cek kuota (quota_service.enforce_quota, kuota ad-hoc
                    yang sama dengan POST /instagram/scrape & GET /instagram/posts)
                        │
                        ├─ KUOTA HABIS
                        │     → return source:"pending"
                        │     → topik TIDAK dipaksa berubah, tetap/sudah
                        │       pending di trend_recommendations
                        │     → otomatis kepilih run_daily_trend_scrape()
                        │       (jadwal harian) atau trigger manual
                        │       berikutnya — kelihatan di
                        │       GET /instagram/trend-scrape/status
                        │
                        └─ KUOTA ADA
                              → scrape SEKARANG (scrape_instagram_posts(),
                                sama seperti GET /instagram/posts)
                              → catat ScrapeRun (status='running' di-commit
                                segera, lihat bagian monitor di bawah)
                              → berhasil (>=1 post) → topic.status="used"
                              → return source:"scraped_now"
```

---

## Tabel yang terlibat (baca, bukan tabel baru)

| Tabel | Peran di endpoint ini |
|---|---|
| `posts` | Sumber pencarian utama (`content` ILIKE), filter `platform='instagram'` |
| `entities` | Pencarian hashtag (`entity_type='HASHTAG'`), JOIN ke `posts.id` |
| `comments` | Daftar komentar per post yang ditemukan |
| `sentiments` | Sentimen POST (IndoBERT), JOIN by `post_id` |
| `lexicon_analyses` | Sentimen KOMENTAR (lexicon), JOIN by `comment_id` → digabung ke ringkasan per post |
| `trend_recommendations` | **Dibaca** untuk cari topik cocok; **ditulis** `status` (pending→used) kalau berhasil scrape — lihat catatan transparansi di bawah |
| `scrape_runs` | Bukti/log 1 kali percobaan scrape (`triggered_by='manual_api'`, `keyword_text='search:{username}'`) |

Tidak ada tabel/kolom baru sama sekali — semua reuse skema yang sudah ada.

---

## Catatan transparansi — endpoint ini MENULIS ke `trend_recommendations`

`trend_recommendations` adalah tabel yang **dibekukan** (frozen) atas
permintaan eksplisit user — perubahan padanya harus selalu dikonfirmasi
dulu. Endpoint ini **tidak memanggil** fungsi frozen `run_daily_trend_scrape()`
atau `submit_recommendations()`, tapi punya kode terpisah
(`app/api/v1/instagram/router.py`, baris tempat `matched_topic.status = "used"`)
yang meniru perilaku yang SAMA PERSIS: kalau topik yang cocok berhasil
discrape (>=1 post), status-nya diubah dari `pending` ke `used` — identik
dengan yang dilakukan `run_daily_trend_scrape()` untuk topik-topik miliknya
sendiri.

Ini **sudah dikonfirmasi & disetujui user** sebagai bagian dari desain fitur
ini (poin "kalau kuota tidak ada, jadikan pending" secara implisit berarti
kalau kuota ADA, ya discrape dan ditandai selesai). Dicatat di sini supaya
jelas: sekarang ada **2 code path** yang bisa mengubah `trend_recommendations.status`
menjadi `used` — fungsi frozen (batch harian terjadwal) dan endpoint search
ini (ad-hoc, dipicu manual/frontend). Keduanya idempotent dan tidak saling
merusak (cuma UPDATE status berdasarkan hasil scrape masing-masing), tapi
penting diingat kalau ada audit/perubahan lebih lanjut ke tabel ini nanti.

---

## Cara pakai

### 1. Login, ambil token
```bash
TOKEN=$(curl -s -X POST http://187.77.125.10:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"testing@redteam.id","password":"Testing123!"}' \
  | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
```

### 2. Cari berdasarkan keyword (caption)
```bash
curl -s "http://187.77.125.10:8000/api/v1/instagram/posts/search?q=starlink" \
  -H "Authorization: Bearer $TOKEN"
```

### 3. Cari berdasarkan hashtag (boleh pakai `#` atau tidak)
```bash
curl -s "http://187.77.125.10:8000/api/v1/instagram/posts/search?q=%23BestOfTheWorld" \
  -H "Authorization: Bearer $TOKEN"
```

### Parameter

| Param | Wajib? | Default | Keterangan |
|---|---|---|---|
| `q` | Ya | — | Keyword atau hashtag |
| `limit` | Tidak | 20 | Maks jumlah post (1-100) |

### Cara baca field `source` di response

| `source` | Artinya |
|---|---|
| `database` | Ketemu langsung dari post yang sudah pernah discrape |
| `scraped_now` | Tidak ketemu di DB, ketemu topik cocok → langsung discrape saat itu juga |
| `pending` | Ketemu topik cocok, tapi kuota harian habis — menunggu giliran batch berikutnya |
| `not_found` | Tidak ketemu post maupun topik manapun — tidak ada akun Instagram yang diketahui |

### Contoh 1 item di `items[]`
```json
{
  "post_id": "d3ca9bff-213c-4333-bb98-7aa5423f5094",
  "shortcode": "DaaTsqyHElY",
  "author": "spacex",
  "caption": "Falcon 9 launches 29 Starlink satellites...",
  "url": "https://www.instagram.com/p/DaaTsqyHElY/",
  "likes": 1583,
  "comments_count": 33,
  "photo_url": null,
  "published_at": "2026-07-05T12:23:19+00:00",
  "sentiment": {
    "post": {"label": "neutral", "score": 0.9932},
    "comments_summary": {"positif": 0, "negatif": 0, "netral": 2}
  },
  "comments": [
    {"content": "10❤️🚀", "author": "paul.oquintao1932", "sentiment": "netral"}
  ]
}
```

**Catatan**: kalau `source: "scraped_now"`, `sentiment.post` bisa masih
`null` sesaat — analisis sentimen post jalan async (Celery task
`workers.analyze_post`, container `worker-ai`). Panggil ulang endpoint yang
sama beberapa detik kemudian untuk melihat sentimen yang sudah terisi.
`views`/jumlah tayangan **tidak tersedia** — Apify (provider scraping yang
dipakai) tidak menyediakan field ini sama sekali (sudah diverifikasi live).

---

## Verifikasi live (05 Juli 2026)

Diuji 4 skenario nyata di server production:

| Skenario | Query | Hasil |
|---|---|---|
| Hit caption | `q=Starlink` | `source:"database"`, 1 post (`@spacex`), sentimen post & komentar terisi |
| Hit hashtag | `q=%23BestOfTheWorld` | `source:"database"`, 1 post (`@natgeo`) ditemukan lewat JOIN `entities` |
| Tidak ketemu sama sekali | `q=xyzqwertyabsurd12345` | `source:"not_found"`, pesan jelas |
| Ketemu topik → coba scrape | `q=GBK` | `source:"scraped_now"`, topik "Konser Coldplay Jakarta di Stadion GBK...", akun `@coldplay` — selesai 0.44 detik karena kena dedup akun-per-hari (akun sudah discrape hari itu sebelumnya) |

## File yang diubah

| File | Perubahan |
|---|---|
| `app/api/v1/instagram/router.py` | Endpoint baru `GET /instagram/posts/search` + helper `_build_search_items()`; docstring modul diperbarui (endpoint mati `/instagram/search` juga sudah dihapus sebelumnya) |

Sudah di-commit (`19c8b34`), di-push ke GitHub, dan di-deploy + diverifikasi
live di server production (187.77.125.10).
