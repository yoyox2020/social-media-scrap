# Phase 2 — Collector Service: DONE

**Tanggal selesai:** 2026-06-21

---

## Deliverable

Data collection working — post dari TikTok, YouTube, Instagram, Reddit, Threads berhasil dikumpulkan via EnsembleData API, dideduplikasi, dan disimpan ke PostgreSQL.

---

## Third-Party API

**EnsembleData** — https://ensembledata.com/apis

| Config | Value |
|--------|-------|
| Base URL | `https://ensembledata.com/apis` |
| Auth | `?token=<ENSEMBLE_DATA_API_TOKEN>` (query param, auto-inject) |
| Token env | `ENSEMBLE_DATA_API_TOKEN` di `.env` |
| Docs | https://ensembledata.com/apis/docs |

> **Penting:** Jangan hardcode token. Simpan di `.env` dan jangan commit ke git.

---

## Platform yang Didukung

| Platform | Endpoint utama | Cursor type |
|----------|---------------|-------------|
| TikTok | `/tt/keyword/posts` | `int` (0, 20, 40, ...) |
| YouTube | `/yt/keyword/search` | `string` (next_page_token) |
| Instagram | `/ig/search` | `string` |
| Reddit | `/reddit/keyword/search` | `string` (after) |
| Threads | `/threads/keyword/search` | `string` |

---

## Arsitektur Collector

```
POST /api/v1/collectors/collect
          │
          ▼
  CollectorService.trigger_collection()
          │
          ├── Celery task: collect_posts_task (tiktok)
          ├── Celery task: collect_posts_task (youtube)
          └── Celery task: collect_posts_task (instagram)
                    │
                    ▼
          CollectorService.collect_for_platform()
                    │
                    ├── EnsembleDataClient.get(endpoint, params)
                    │        ▲ auto-inject token, retry otomatis
                    │
                    ├── Connector.extract_posts(raw)
                    │
                    ├── Normalizer.normalize(items, keyword_id)
                    │
                    ├── PostRepository.get_existing_external_ids() ← deduplication
                    │
                    └── PostRepository.bulk_create(new_posts)
                              ▲ ON CONFLICT DO NOTHING
```

---

## File yang Diimplementasi

### Integrations

| File | Keterangan |
|------|-----------|
| `app/integrations/ensemble_data/endpoints.py` | Registry lengkap semua endpoint (70+ endpoint, 8 platform) |
| `app/integrations/tiktok/connector.py` | TikTok: keyword, hashtag, comments, user posts |
| `app/integrations/youtube/connector.py` | YouTube: keyword search, video comments, channel videos |
| `app/integrations/instagram/connector.py` | Instagram: search, user posts, comments |
| `app/integrations/reddit/connector.py` | Reddit: keyword search, subreddit posts, comments |
| `app/integrations/threads/connector.py` | Threads: keyword search, user posts |

### Processing / Normalizer

| File | Keterangan |
|------|-----------|
| `app/services/processing/normalizer.py` | 5 normalizer classes + registry |

**Field yang dinormalisasi ke model Post:**

| Post field | TikTok source | YouTube source | Instagram source |
|-----------|--------------|----------------|-----------------|
| `external_id` | `aweme_id` | `video_id` | `id` / `pk` |
| `content` | `desc` | `title` | `caption` |
| `author` | `author.unique_id` | `channel_name` | `owner.username` |
| `url` | generated | generated | `permalink` |
| `published_at` | `create_time` (unix) | `published_at` (ISO) | `taken_at` (unix) |
| `raw_data` | full item | full item | full item |

### Services

| File | Keterangan |
|------|-----------|
| `app/services/collector/schemas.py` | `CollectRequest`, `CollectionResult`, `JobStatusResponse` |
| `app/services/collector/service.py` | `trigger_collection()`, `collect_for_platform()`, pagination loop |

### Repositories

| File | Methods baru |
|------|-------------|
| `app/repositories/keyword_repository.py` | `get_by_id`, `list_by_project`, `list_active_by_project`, `create`, `delete` |
| `app/repositories/post_repository.py` | `get_by_external_id`, `get_existing_external_ids`, `list_by_keyword`, `count_by_keyword`, `bulk_create` |

### Worker

| File | Keterangan |
|------|-----------|
| `app/workers/collector_worker.py` | `collect_posts_task` Celery task, max_retries=3, asyncio.run() wrapper |

### API Endpoints

**Prefix:** `/api/v1/collectors`

| Method | Path | Auth | Keterangan |
|--------|------|------|-----------|
| `POST` | `/collect` | ✓ | Trigger koleksi, return job IDs |
| `GET` | `/jobs/{job_id}` | ✓ | Cek status Celery task |
| `GET` | `/platforms` | — | List platform yang didukung |

### Migration

| File | Keterangan |
|------|-----------|
| `migrations/versions/002_posts_unique_constraint.py` | Unique index `(external_id, platform)` untuk ON CONFLICT DO NOTHING |

---

## Fitur Utama

### 1. Dynamic API Client
Token di-inject otomatis — tidak perlu pasang manual di setiap call:
```python
async with EnsembleDataClient() as client:
    raw = await client.get("/tt/keyword/posts", params={"keyword": "python"})
```

### 2. Pagination Otomatis
Setiap connector punya `extract_cursor()` — collector loop terus sampai cursor None atau max_pages:
```python
for page in range(max_pages):
    raw = await fetch_page(connector, keyword, cursor)
    cursor = connector.extract_cursor(raw)
    if cursor is None:
        break
```

### 3. Deduplication
Sebelum insert, cek external_id yang sudah ada di DB:
```python
existing = await post_repo.get_existing_external_ids(ext_ids, platform)
new_posts = [p for p in posts if p.external_id not in existing]
```

### 4. Raw Data Preservation
Full API response selalu disimpan di `raw_data` — tidak ada data yang hilang meski normalizer belum sempurna.

---

## Cara Penggunaan

```bash
# 1. Set token di .env
echo "ENSEMBLE_DATA_API_TOKEN=your_token_here" >> .env

# 2. Start services
make up

# 3. Trigger koleksi (butuh keyword_id dari DB)
curl -X POST http://localhost:8000/api/v1/collectors/collect \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "keyword_id": "uuid-keyword-anda",
    "platforms": ["tiktok", "youtube"]
  }'
# → {"success":true,"data":{"keyword_text":"..","jobs":[{"platform":"tiktok","job_id":"...","status":"queued"}]}}

# 4. Cek status job
curl http://localhost:8000/api/v1/collectors/jobs/<job_id> \
  -H "Authorization: Bearer <token>"
# → {"success":true,"data":{"status":"SUCCESS","result":{"new_posts":47,"skipped_duplicates":3,...}}}
```

---

## Tests

| File | Test |
|------|------|
| `tests/unit/test_normalizer.py` | 12 tests: normalisasi per platform, missing fields, unknown platform |
| `tests/unit/test_collector_service.py` | 4 tests: dispatch tasks, keyword not found, inactive, invalid platform |

---

## Phase Berikutnya

**Phase 3 — Processing Service:** Cleaner (hapus HTML/emoji), deduplication lanjutan, normalizer teks untuk NLP.
