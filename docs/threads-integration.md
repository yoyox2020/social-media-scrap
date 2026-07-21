# Integrasi Threads (via EnsembleData)

**Status: Fase 1 (scrape dasar) — SELESAI, dibangun 2026-07-19.**

## Ringkasan

Threads (Meta) diintegrasikan pakai provider **EnsembleData** (sama seperti
YouTube/Instagram/TikTok/Reddit di project ini) — TERPISAH TOTAL dari
platform lain, tidak mengimpor/mengubah kode YouTube/TikTok/Facebook/
Instagram/Twitter/News sama sekali.

Berbeda dari TikTok/Facebook/Instagram (yang scrape per-AKUN dari
`related_accounts`), Threads search-nya berbasis **keyword/topik teks
langsung** — jadi pola integrasinya lebih dekat ke News (search topik ->
simpan hasil) daripada ke TikTok (baca akun dari trend_recommendations ->
scrape akun itu).

## Kenapa dibangun ulang dari nol (bukan tinggal pakai)

Kode `ThreadsConnector`/`ThreadsNormalizer` yang ada di repo **SEBELUM**
sesi ini ternyata **tidak pernah diuji ke API asli** — parameter, path
endpoint, dan struktur respons yang diasumsikan semuanya salah. Live-test
ke EnsembleData 2026-07-19 menemukan:

| Yang diasumsikan sebelumnya | Kenyataan (live-verified) |
|---|---|
| Param search: `keyword` | Param asli: **`name`** (`keyword` -> HTTP 422) |
| Path replies: `/threads/post/info-replies` | Path asli: **`/threads/post/replies`** (yang lama -> HTTP 404) |
| Param replies: `pk`/`code`/`post_id` | Param asli: **`id`** (isi = `pk`, bukan `code`) |
| Respons search: `data.threads`/`data.posts` (flat) | Nyata: `data[].node.thread.thread_items[].post` |
| Respons replies: sama dgn search | Nyata BEDA: `data[].node.thread_items[].post` (TANPA lapisan `.thread`) |
| Post fields flat (`item.id`, `item.caption` string, `item.permalink`) | Nyata dalam (`post.pk`, `post.caption.text`, URL dibentuk manual dari `user.username`+`post.code`) |

## Keterbatasan penting API (bukan bug kita)

1. **Search TIDAK terbukti mendukung pagination** — field `cursor` di
   setiap hasil SELALU kosong pada semua sample yang dites. 1x panggilan =
   1 batch tetap dari EnsembleData.
2. **Replies cuma balikin SEBAGIAN balasan** pada post dengan reply banyak
   — contoh nyata: post dengan `direct_reply_count=58` cuma dapat ~28
   balasan (48%) dari 1 panggilan, tanpa cursor untuk lanjut. Parameter
   `depth` (dipakai endpoint lain EnsembleData) **belum sempat diuji
   tuntas** (kuota API habis di tengah pengujian) — kirim best-effort, tapi
   JANGAN asumsikan semua balasan pasti didapat.
3. **EnsembleData berbayar & kuota harian TERBUKTI kecil** — habis hanya
   dari ~10 panggilan uji coba dalam 1 sesi. Karena itu:
   - Balasan/komentar HANYA diambil untuk `comments_top_n` post
     ter-interaksi tinggi per topik (default 3), BUKAN semua post.
   - `threads_trend_daily_budget` default kecil (3 topik/hari).

## Arsitektur pipeline

```
Celery Beat (12:00 WIB harian)
  -> workers.threads_trend_recommendation.daily
  -> run_daily_trend_scrape_threads()          [app/services/threads/trend_scrape_service.py]
       baca topik trend_recommendations (status='pending', READ-ONLY thd tabel itu)
  -> search_threads_posts(keyword=topic.topic) [app/services/threads/pipeline_service.py]
       -> ThreadsConnector.search_by_keyword()  [app/integrations/threads/connector.py]
       -> ThreadsNormalizer.normalize()         [app/services/processing/normalizer.py]
       -> simpan ke posts (platform='threads')
       -> utk N post ter-interaksi tinggi: collect_replies_for_post()
            -> ThreadsConnector.get_post_replies()
            -> simpan ke comments (post_id FK)
            -> analisis lexicon (app/ai/lexicon/service.py, LINTAS PLATFORM)
```

**Catatan Sentiment Agent (LLM validasi kedua)**: saat ini
(`app/services/sentiment_agent/agent.py`) baru mencakup
`platform='youtube'`. Komentar Threads dianalisis leksikon (rule-based)
saja untuk saat ini — `sentiment_source` di response API akan selalu
`"lexicon_only"` sampai Sentiment Agent diperluas ke platform lain.

## File yang dibuat/diubah

| File | Keterangan |
|---|---|
| `app/integrations/threads/connector.py` | DIPERBAIKI TOTAL — param/path/parsing sesuai API asli |
| `app/services/processing/normalizer.py` | `ThreadsNormalizer` diperbaiki total (field mapping asli) |
| `app/services/threads/pipeline_service.py` | BARU — search+simpan post+balasan+lexicon |
| `app/services/threads/trend_scrape_service.py` | BARU — baca trend_recommendations, orkestrasi harian |
| `app/workers/threads_trending_worker.py` | BARU — Celery task (jadwal + on-demand) |
| `app/workers/celery_app.py` | Ditambah (additive) — registrasi task + beat schedule |
| `app/shared/config.py` | Ditambah (additive) — `threads_trend_*` settings |
| `app/api/v1/threads/router.py` | BARU — endpoint API |
| `app/main.py` | Ditambah (additive) — registrasi router |
| `tests/integration/test_threads_pipeline_manual.py` | BARU — 25 cek, pakai struktur data REAL hasil live-test |

Tidak ada file YouTube/TikTok/Facebook/Instagram/Twitter/News yang disentuh.

---

## Cara Pakai API

Base URL: `https://api.dismi.xyz/api/v1/threads`

Semua endpoint butuh header `Authorization: Bearer <access_token>` (lihat
`POST /api/v1/auth/login`).

### 1. Cari post dari data yang sudah tersimpan

```
GET /threads/search?q=jokowi&limit_posts=20&limit_comments=20
```

- `limit_posts`: 1–100 (default 20)
- `limit_comments`: **TANPA batas atas** (default 20) — kirim angka besar
  (mis. `999999`) untuk ambil semua balasan yang tersimpan.

Balasan **nested langsung di dalam post-nya** (`posts[i].comments`), tidak
perlu join manual — setiap balasan tetap punya `post_id` eksplisit.

Contoh response:
```json
{
  "success": true,
  "data": {
    "status": "ready",
    "query": "jokowi",
    "total_posts": 1,
    "posts": [
      {
        "id": "6beb8095-d7ab-47b4-9828-2bc9f51a7875",
        "external_id": "3938988891525331248",
        "url": "https://www.threads.net/@broreza_fs/post/DaqGYb1k7kw",
        "author": "broreza_fs",
        "content": "Jokowi adalah Kita\nKita adalah Jokowi...",
        "likes": 57,
        "replies": 58,
        "reposts": 0,
        "quotes": 0,
        "media": [{"type": "image", "url": "https://..."}],
        "tags": [],
        "published_at": "2026-07-10T12:34:51+00:00",
        "collected_at": "2026-07-19T03:55:23Z",
        "comment_count": 4,
        "comments": [
          {
            "id": "f90da1df-5654-4952-b95c-79d534fbe6d7",
            "post_id": "6beb8095-d7ab-47b4-9828-2bc9f51a7875",
            "content": "Kejar terus ijazah palsu...",
            "author": "youdikubands",
            "reply_to": "broreza_fs",
            "like_count": 1,
            "sentiment": "negatif",
            "sentiment_source": "lexicon_only",
            "score": -1.0,
            "published_at": "2026-07-10T12:40:00+00:00"
          }
        ]
      }
    ]
  }
}
```

### 2. Trigger pencarian BARU (belum ada di DB)

```
POST /threads/search?q=jokowi&max_posts=10&comments_top_n=3
```

Berjalan di **background** (Celery, bukan sinkron) karena EnsembleData
berbayar — response langsung berisi `job_id`, hasilnya baru bisa ditarik
lewat `GET /threads/search` setelah beberapa saat.

```json
{
  "success": true,
  "data": {
    "status": "queued",
    "query": "jokowi",
    "job_id": "a1b2c3d4-...",
    "message": "Pencarian berjalan di background. Cek hasil via GET /threads/search setelah beberapa saat."
  }
}
```

### 3. Detail 1 post + semua balasannya

```
GET /threads/posts/{post_id}?limit_comments=999999
```

`{post_id}` bisa UUID internal ATAU `external_id`/`pk` Threads asli.

```json
{
  "success": true,
  "data": {
    "id": "6beb8095-...",
    "external_id": "3938988891525331248",
    "url": "https://www.threads.net/@broreza_fs/post/DaqGYb1k7kw",
    "total_comments_in_db": 4,
    "comments": [ ... ]
  }
}
```

### 4. Trigger manual batch scrape trend_recommendations

```
POST /threads/trend-scrape/run
```
Biasanya jalan otomatis via Celery Beat (12:00 WIB harian) — endpoint ini
untuk testing/trigger manual.

### 5. Status pipeline

```
GET /threads/trend-scrape/status?recent_limit=10
```
```json
{
  "success": true,
  "data": {
    "daily_budget": 3,
    "posts_per_topic": 10,
    "comments_top_n": 3,
    "schedule": "12:00 WIB",
    "topics_pending": 812,
    "topics_used": 45,
    "topics_failed_permanent": 2,
    "recent_runs": [
      {"keyword": "jokowi", "status": "success", "posts_fetched": 2, "posts_new": 2, "started_at": "..."}
    ]
  }
}
```

### 6. Monitoring menyeluruh (BARU) — scraping sampai sentiment

```
GET /threads/monitor?recent_limit=10
```
Beda dari #5 (cuma scraping) — endpoint ini menggabungkan status scraping
+ jumlah data tersimpan + distribusi sentiment (dan berapa persen yang
sudah tervalidasi LLM Sentiment Agent, `llm_reviewed_pct` — SELALU 0.0%
saat ini karena Sentiment Agent belum mencakup Threads, lihat bagian
"Rekomendasi lanjutan" poin 2).

```json
{
  "success": true,
  "data": {
    "scraping": { "daily_budget": 3, "schedule": "12:00 WIB", "topics_pending": 102, "recent_runs": [ ... ] },
    "data": { "total_posts": 42, "total_comments": 187 },
    "sentiment": {
      "positif": {"count": 45, "percentage": 24.1},
      "negatif": {"count": 67, "percentage": 35.8},
      "netral": {"count": 75, "percentage": 40.1},
      "dominant": "netral",
      "total_analyzed": 187,
      "llm_reviewed_count": 0,
      "llm_reviewed_pct": 0.0,
      "note": "Sentiment Agent (validasi LLM kedua) BARU mencakup platform='youtube' -- semua sentiment Threads di atas MASIH murni lexicon rule-based, belum tervalidasi LLM."
    }
  }
}
```

**Catatan kualitas sentiment (dites live 2026-07-19 ke komentar Threads
asli)**: dari 8 sampel komentar, 5 (62,5%) berpotensi salah label —
komentar yang jelas menghina ("sarang MALING", "sarang PENJILAT",
"laknat", "koruptor") dianggap `"netral"` karena kata-kata itu belum ada
di kamus leksikon (`app/ai/lexicon/data/negative.txt`). BUKAN bug khusus
Threads — kamus yang sama dipakai lintas SEMUA platform. Aman untuk
gambaran kasar/tren, jangan dijadikan angka final tanpa spot-check manual.

## Rekomendasi lanjutan (belum dikerjakan)

1. **Verifikasi tuntas parameter `depth`** di endpoint replies begitu
   kuota EnsembleData tersedia lagi — kalau terbukti menambah cakupan
   balasan, update `ThreadsConnector.get_post_replies()` default-nya.
2. **Perluas Sentiment Agent** (`app/services/sentiment_agent/agent.py`)
   untuk mencakup platform selain YouTube (query-nya saat ini hardcode
   `WHERE p.platform = 'youtube'`), supaya komentar Threads (dan
   FB/IG/TikTok/Twitter) juga dapat validasi LLM, bukan lexicon-only.
3. **`ThreadsConnector.get_user_posts()`** belum diverifikasi live sama
   sekali (beda endpoint dari yang sudah dites) — jangan dipakai produksi
   sebelum ditest.
