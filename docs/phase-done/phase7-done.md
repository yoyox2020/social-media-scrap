# Phase 7 — Production Hardening ✅

**Status:** SELESAI  
**Tanggal:** 2026-06-23  

---

## Ringkasan

Phase 7 menjadikan platform **production-ready** dengan lima komponen utama:

| Komponen | Manfaat |
|----------|---------|
| Request ID Middleware | Setiap request punya ID unik yang muncul di semua log — mudah debug & trace |
| Redis Cache | Endpoint mahal (sentiment summary, trends) tidak query PostgreSQL setiap request |
| Rate Limiting | Endpoint `/agents/ask` dibatasi per user — cegah abuse |
| Detailed Health Check | `GET /health` cek DB, Redis, Ollama, Elasticsearch sekaligus |
| Celery Beat | Laporan harian & mingguan di-generate otomatis tanpa trigger manual |

---

## Arsitektur Phase 7

```
Client Request
      │
      ▼
RequestIDMiddleware          ← inject/generate X-Request-ID, bind ke structlog
      │
      ▼
RateLimiter (jika endpoint mahal)
      │
      ├── HIT REDIS CACHE?  ──YES──► return cached response (< 1ms)
      │
      NO
      │
      ▼
  Route Handler
      │
      ├── Query PostgreSQL
      ├── Simpan ke Redis Cache (TTL 5 menit)
      └── Return response + X-Request-ID header


Celery Beat (background)
      │
      ├── Setiap hari  08:00 WIB ──► generate_scheduled_reports(period="day")
      └── Setiap Senin 09:00 WIB ──► generate_scheduled_reports(period="week")
```

---

## File Baru

| File | Fungsi |
|------|--------|
| `app/infrastructure/middleware/__init__.py` | Package init |
| `app/infrastructure/middleware/request_id.py` | `RequestIDMiddleware` — inject `X-Request-ID`, bind structlog context |
| `app/infrastructure/cache/__init__.py` | Package init |
| `app/infrastructure/cache/redis_cache.py` | `cache_get`, `cache_set`, `cache_delete`, `cache_delete_pattern` |
| `app/infrastructure/rate_limit/__init__.py` | Package init |
| `app/infrastructure/rate_limit/limiter.py` | `RateLimiter` — FastAPI dependency berbasis Redis counter |
| `app/workers/scheduled_tasks.py` | `generate_scheduled_reports_task` — Celery task untuk auto-generate laporan |

---

## File Diubah

| File | Perubahan |
|------|-----------|
| `app/main.py` | Tambah `RequestIDMiddleware`, health check detail (DB/Redis/Ollama/ES), logging startup/shutdown |
| `app/api/v1/sentiment.py` | Cache 5 menit pada `GET /sentiment/summary/{keyword_id}` |
| `app/api/v1/trends.py` | Cache 5 menit pada `GET /trends/keyword/` dan `GET /trends/sentiment/`; import pindah ke top-level |
| `app/api/v1/agents.py` | Rate limit 10 req/60s per user via `RateLimiter` dependency |
| `app/workers/celery_app.py` | Tambah `beat_schedule` (daily 08:00 + weekly Senin 09:00), timezone `Asia/Jakarta`, include `scheduled_tasks` |
| `app/shared/config.py` | Tambah `rate_limit_agents_max_requests` dan `rate_limit_agents_window_seconds` |
| `app/repositories/keyword_repository.py` | Tambah method `list_all_active()` |
| `docker-compose.yml` | Tambah service `worker-beat` |
| `.env.example` | Tambah `RATE_LIMIT_AGENTS_MAX_REQUESTS` dan `RATE_LIMIT_AGENTS_WINDOW_SECONDS` |

---

## Penjelasan Komponen

### 1. Request ID Middleware

```python
# app/infrastructure/middleware/request_id.py

class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
```

Setiap log entry di dalam satu request otomatis menyertakan `request_id`. Untuk trace satu request, cukup filter log berdasarkan `request_id`.

---

### 2. Redis Cache

```python
from app.infrastructure.cache.redis_cache import cache_get, cache_set, cache_delete

# Read-through pattern
cached = await cache_get("sentiment:summary:uuid")
if cached is not None:
    return cached              # HIT — tidak ke PostgreSQL

data = await repo.query(...)
await cache_set("sentiment:summary:uuid", data, ex=300)   # 5 menit
return data
```

**Key pattern yang dipakai:**

| Endpoint | Cache Key | TTL |
|----------|-----------|-----|
| `GET /sentiment/summary/{keyword_id}` | `sentiment:summary:{keyword_id}` | 300s |
| `GET /trends/keyword/{keyword_id}` | `trends:keyword:{keyword_id}:{period}:{platform}` | 300s |
| `GET /trends/sentiment/{keyword_id}` | `trends:sentiment:{keyword_id}:{period}` | 300s |

---

### 3. Rate Limiter

```python
# Inisialisasi di agents.py
_ask_limiter = RateLimiter(
    max_requests=settings.rate_limit_agents_max_requests,   # default: 10
    window_seconds=settings.rate_limit_agents_window_seconds,  # default: 60
)

@router.post("/ask")
async def ask_agent(
    body: AskRequest,
    current_user: User = Depends(_ask_limiter),  # ← rate check disini
):
    ...
```

Saat limit tercapai:
- HTTP **429** dengan header `Retry-After: <detik>`
- Body: `{"detail": "Rate limit exceeded (10 req/60s). Retry after 45s."}`

---

### 4. Detailed Health Check

`GET /health` mengecek semua infrastruktur secara berurutan dengan timeout 5 detik per check.

| Check | Method |
|-------|--------|
| `database` | `SELECT 1` via SQLAlchemy |
| `redis` | `PING` via redis.asyncio |
| `ollama` | `GET /api/tags` HTTP |
| `elasticsearch` | `GET /_cluster/health` HTTP |

- **HTTP 200** — semua `ok`
- **HTTP 207** — salah satu `error` (partial degraded)

---

### 5. Celery Beat

Beat scheduler berjalan di container terpisah (`worker-beat`). Schedule default:

```python
beat_schedule={
    "daily-reports-08:00": {
        "task": "workers.generate_scheduled_reports",
        "schedule": crontab(hour=8, minute=0),          # setiap hari 08:00 WIB
        "options": {"queue": "reports"},
    },
    "weekly-reports-monday-09:00": {
        "task": "workers.generate_scheduled_reports",
        "schedule": crontab(hour=9, minute=0, day_of_week=1),  # Senin 09:00 WIB
        "kwargs": {"period": "week"},
        "options": {"queue": "reports"},
    },
}
```

Task `generate_scheduled_reports_task`:
1. Query semua keyword dengan `is_active=True` dari semua project
2. Buat record report `status=pending` per keyword
3. Dispatch `generate_report_task` ke Celery queue `reports`

---

## API — Perubahan Phase 7

### GET /health — diperluas

**Response 200 (semua ok):**
```json
{
  "success": true,
  "data": {
    "status": "ok",
    "version": "1.0.0",
    "checks": {
      "database":      { "status": "ok" },
      "redis":         { "status": "ok" },
      "ollama":        { "status": "ok" },
      "elasticsearch": { "status": "ok" }
    }
  }
}
```

**Response 207 (degraded):**
```json
{
  "success": true,
  "data": {
    "status": "degraded",
    "checks": {
      "database":  { "status": "ok" },
      "ollama":    { "status": "error", "detail": "connection refused" }
    }
  }
}
```

### POST /agents/ask & /agents/ask-sync — rate limited

Response jika limit tercapai:
```
HTTP 429
Retry-After: 45

{"detail": "Rate limit exceeded (10 req/60s). Retry after 45s."}
```

---

## Cara Menjalankan

### Docker (Recommended)

#### Start semua service termasuk Beat

```bash
docker compose up -d
```

Pastikan semua container berjalan:
```bash
docker compose ps
```

| Container | Status yang diharapkan |
|-----------|----------------------|
| `social_intel_api` | Up |
| `social_intel_worker` | Up |
| `social_intel_worker_ai` | Up |
| `social_intel_worker_beat` | Up ← baru di Phase 7 |
| `social_intel_postgres` | Up (healthy) |
| `social_intel_redis` | Up (healthy) |
| `social_intel_elasticsearch` | Up (healthy) |
| `social_intel_ollama` | Up |

#### Verifikasi health check

```bash
curl http://localhost:8000/health
```

Response harus `"status": "ok"` untuk semua checks.

#### Verifikasi Request ID

```bash
curl -v http://localhost:8000/health 2>&1 | grep X-Request-ID
# < X-Request-ID: 550e8400-e29b-41d4-a716-446655440000
```

#### Verifikasi Rate Limiting

```bash
# Loop 12x — request ke-11 dan ke-12 harus dapat 429
for i in $(seq 1 12); do
  curl -s -o /dev/null -w "Request $i: %{http_code}\n" \
    -X POST http://localhost:8000/api/v1/agents/ask \
    -H "Authorization: Bearer <token>" \
    -H "Content-Type: application/json" \
    -d '{"question":"test","keyword_id":"uuid"}'
done
```

#### Verifikasi Redis Cache

```bash
# Request pertama — MISS, query PostgreSQL
curl http://localhost:8000/api/v1/sentiment/summary/<keyword_id> \
  -H "Authorization: Bearer <token>"

# Cek key di Redis
docker compose exec redis redis-cli GET "sentiment:summary:<keyword_id>"

# Request kedua — HIT dari cache (jauh lebih cepat)
curl http://localhost:8000/api/v1/sentiment/summary/<keyword_id> \
  -H "Authorization: Bearer <token>"
```

#### Lihat log Beat scheduler

```bash
docker compose logs -f worker-beat
```

#### Trigger scheduled report manual

```bash
docker compose exec worker \
  celery -A app.workers.celery_app call workers.generate_scheduled_reports
```

---

### Development (tanpa Docker)

#### 1. Jalankan infrastruktur via Docker

```bash
docker compose up -d postgres redis elasticsearch ollama
```

#### 2. Set environment variables

```bash
cp .env.example .env
# Edit .env sesuai kebutuhan lokal
```

#### 3. Jalankan API

```bash
uvicorn app.main:app --reload --port 8000
```

#### 4. Jalankan Celery worker

```bash
celery -A app.workers.celery_app worker \
  --loglevel=info \
  --queues=collector,processing,reports,celery \
  --concurrency=4
```

#### 5. Jalankan Celery Beat (opsional)

```bash
celery -A app.workers.celery_app beat --loglevel=info
```

---

## Konfigurasi Rate Limit

Ubah via `.env` — tidak perlu redeploy kode:

```env
RATE_LIMIT_AGENTS_MAX_REQUESTS=10    # request per window
RATE_LIMIT_AGENTS_WINDOW_SECONDS=60  # window dalam detik
```

Contoh — lebih longgar untuk internal tool:
```env
RATE_LIMIT_AGENTS_MAX_REQUESTS=50
RATE_LIMIT_AGENTS_WINDOW_SECONDS=60
```

---

## Konfigurasi Cache

Cache TTL saat ini hardcoded 300 detik (5 menit) di level endpoint. Untuk invalidasi manual setelah analisis AI selesai:

```python
from app.infrastructure.cache.redis_cache import cache_delete, cache_delete_pattern

# Invalidasi setelah AI worker selesai analisis
await cache_delete(f"sentiment:summary:{keyword_id}")
await cache_delete_pattern(f"trends:*:{keyword_id}:*")
```

---

## Log Monitoring

Semua log output JSON. Contoh cara filter per request:

```bash
# Docker logs — filter satu request_id
docker compose logs api | grep "550e8400-e29b-41d4-a716-446655440000"

# Atau dengan jq
docker compose logs api 2>&1 | \
  grep -v "^$" | \
  python3 -c "import sys,json; [print(json.dumps(json.loads(l), indent=2)) for l in sys.stdin if '550e8400' in l]"
```

---

## Semua Phase Selesai 🎉

| Phase | Nama | Tanggal |
|-------|------|---------|
| 0 | Setup & struktur project | 2026-06-21 |
| 1 | Foundation (DB, Redis, ES) | 2026-06-21 |
| 2 | Auth Service (JWT, API Key, RBAC) | 2026-06-21 |
| 3 | Collector Service (EnsembleData, 5 platform) | 2026-06-21 |
| 4 | Processing Service (cleaner, deduplicator, normalizer) | 2026-06-21 |
| 5 | AI Service (IndoBERT, BGE-M3, GLiNER, Qwen3) | 2026-06-22 |
| 6 | Agent Service (6 agent + orchestrator) | 2026-06-22 |
| 7 (docs: 7) | Report Service (JSON, PDF, DOCX) | 2026-06-22 |
| 8 (docs: 8) | Production Hardening | 2026-06-23 |

Platform Social Intelligence siap digunakan di production.

---

## Langkah Berikutnya (Opsional)

| Fitur | Keterangan |
|-------|------------|
| **Prometheus + Grafana** | Metrics request count, latency, error rate, queue depth |
| **OpenTelemetry** | Distributed tracing dari API ke worker ke DB |
| **Cache invalidation otomatis** | Celery signal `task_success` untuk invalidasi cache setelah AI job |
| **Frontend Dashboard** | Visualisasi sentimen, tren, dan laporan |
| **Multi-provider Collector** | Tambah provider selain EnsembleData jika sudah ditemukan |
