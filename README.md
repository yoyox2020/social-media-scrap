# Social Intelligence Platform

Platform backend berbasis AI untuk monitoring dan analisis media sosial — mengambil trending Google Trends setiap jam, scraping video & komentar YouTube secara otomatis, lalu menganalisis sentimen menggunakan leksikon Bahasa Indonesia.

---

## Daftar Isi

- [Fitur Utama](#fitur-utama)
- [Tech Stack](#tech-stack)
- [Arsitektur](#arsitektur)
- [Alur Pipeline Otomatis](#alur-pipeline-otomatis)
- [Quick Start](#quick-start)
- [Urutan Menjalankan Docker](#urutan-menjalankan-docker)
- [Konfigurasi .env](#konfigurasi-env)
- [Struktur Project](#struktur-project)
- [Dokumentasi](#dokumentasi)

---

## Fitur Utama

| Fitur | Deskripsi |
|---|---|
| **YouTube Intelligence** | Fetch Google Trends setiap 1 jam → scrape video → komentar → sentimen otomatis |
| **Lexicon Sentiment** | Analisis sentimen Bahasa Indonesia rule-based (319 kata positif, 431 negatif, negasi-aware) |
| **Collector Multi-Platform** | TikTok, YouTube, Instagram, Reddit, Threads via EnsembleData API |
| **Processing Pipeline** | Cleaning, deduplication, normalisasi teks |
| **AI Sentiment (IndoBERT)** | Label Positif / Negatif / Netral dengan model transformer |
| **NER (GLiNER)** | Ekstraksi Person, Org, Location, Product dari teks |
| **Semantic Search** | BGE-M3 + pgvector — semantic search Bahasa Indonesia |
| **Multi-Agent AI** | Planner → Search → Sentiment → Entity → Trend → Summary (Qwen3 via Ollama) |
| **Report Generator** | Output JSON, PDF, DOCX |
| **Celery Beat Scheduler** | Cron otomatis setiap jam untuk fetch trending + pipeline YouTube |

---

## Tech Stack

| Layer | Teknologi |
|---|---|
| API | FastAPI 0.115 + Uvicorn |
| Database | PostgreSQL 17 + pgvector 0.8.3 (tipe `vector` untuk embedding) |
| ORM | SQLAlchemy 2.x async + asyncpg |
| Migration | Alembic (006 versi migration) |
| Cache & Broker | Redis 7 |
| Task Queue | Celery 5 + Celery Beat |
| Search | Elasticsearch 8.11 |
| Trending | Google Trends RSS via feedparser (tanpa API key) |
| Data Source | EnsembleData API (YouTube, TikTok, Instagram, Reddit, Threads) |
| AI — Sentiment | IndoBERT (`mdhugol/indonesia-bert-sentiment-classification`) |
| AI — NER | GLiNER (`urchade/gliner_multi-v2.1`) |
| AI — Embedding | BGE-M3 (`BAAI/bge-m3`) |
| AI — LLM | Qwen3 8B via Ollama |
| Lexicon | Rule-based Bahasa Indonesia (file `.txt` lokal, `@lru_cache`) |
| Validasi | Pydantic v2 + email-validator |
| Logging | structlog (JSON structured logging) |
| Auth | JWT (python-jose) + bcrypt (passlib, pin `<4.0.0`) |
| Container | Docker + Docker Compose |

---

## Arsitektur

```
Client / Frontend
      │
      ▼
FastAPI :8000          ← JWT auth, request ID middleware, rate limiting
      │
      ├── YouTube Intelligence (/api/v1/youtube/*)
      │     └── pipeline_service.py
      │           ├── Google Trends RSS → trending_topics (DB)
      │           ├── Keywords → keywords (DB)
      │           ├── EnsembleData → posts/videos (DB)
      │           ├── EnsembleData → comments (DB)
      │           └── Lexicon → lexicon_analyses (DB)
      │
      ├── Collector (/api/v1/collectors/*)
      │     └── Celery queue: collector
      │
      ├── AI Pipeline (/api/v1/sentiment/, /api/v1/topics/, ...)
      │     └── Celery queue: ai (worker-ai container)
      │
      ├── Multi-Agent (/api/v1/agents/*)
      │     └── Qwen3 via Ollama
      │
      └── Reports (/api/v1/reports/*)
            └── PDF / DOCX / JSON

Redis :6379    ← Celery broker + result backend + cache
PostgreSQL :5432  ← semua data (pgvector untuk embedding)
Elasticsearch :9200  ← full-text & semantic index
Ollama :11434  ← Qwen3 LLM inference
```

---

## Alur Pipeline Otomatis

Pipeline YouTube berjalan **setiap 1 jam** via Celery Beat (`crontab(minute=0)`):

```
[Celery Beat — tiap jam :00]
        │
        ▼
fetch_trending_youtube_task
  → Google Trends RSS (geo=ID, period=24h)
  → INSERT ke trending_topics (histori tersimpan, tidak dihapus)
  → Cek keyword sudah ada? TIDAK → INSERT ke keywords
  → Queue collect_youtube_pipeline_task per keyword
        │
        ▼
collect_youtube_pipeline_task(keyword_id)
  → EnsembleData /youtube/search?keyword=...
  → YouTubeNormalizer → Post {url, title, channel, views, thumbnail, ...}
  → bulk INSERT ke posts (ON CONFLICT DO NOTHING — deduplication otomatis)
  → Queue collect_youtube_comments_task per video
        │
        ▼
collect_youtube_comments_task(post_id, keyword_id)
  → EnsembleData /youtube/video/comments?videoId=...
  → Loop cursor pagination (max 3 halaman, ~20 komentar/halaman)
  → INSERT ke comments
  → _analyze_comments_lexicon()
        │
        ▼
Lexicon Sentiment (app/ai/lexicon/service.py)
  → tokenize → deteksi negasi → cocokkan leksikon
  → score = positif_count - negatif_count
  → label: positif / negatif / netral
  → INSERT ke lexicon_analyses
```

> Video yang tersimpan adalah **URL YouTube** (`https://youtube.com/watch?v=VIDEO_ID`) beserta metadata (judul, channel, views, thumbnail). **File video tidak disimpan.**

---

## Quick Start

### Prasyarat

- Docker & Docker Compose
- EnsembleData API token — [ensembledata.com](https://ensembledata.com)
- Minimum 4 GB RAM (tanpa AI worker), 8 GB RAM (dengan worker-ai)

### 1. Clone & konfigurasi

```bash
git clone <repo-url>
cd social-media-scrap
cp .env.example .env
# Edit .env — wajib isi: ENSEMBLE_DATA_API_TOKEN, JWT_SECRET_KEY, APP_SECRET_KEY
```

### 2. Jalankan infrastruktur

```bash
# Infrastructure dulu (tunggu sampai healthy)
docker compose up -d postgres redis

# Lalu API dan worker
docker compose up -d api worker worker-beat

# Opsional: AI worker (butuh RAM besar, download model ~2GB pertama kali)
docker compose up -d worker-ai
```

### 3. Buat akun & project

```bash
# Register
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"Admin1234!","full_name":"Admin"}'

# Login
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"Admin1234!"}'
```

### 4. Trigger fetch trending (manual pertama kali)

```bash
curl -X POST http://localhost:8000/api/v1/youtube/trending/fetch \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"geo":"ID","period":"24h","limit":10,"auto_collect":true,"project_id":"<PROJECT_UUID>"}'
```

Selanjutnya Celery Beat akan otomatis fetch setiap jam.

### 5. Verifikasi

```bash
curl http://localhost:8000/health
curl -H "Authorization: Bearer <TOKEN>" http://localhost:8000/api/v1/youtube/dashboard
```

Swagger UI: **`http://localhost:8000/docs`**

---

## Urutan Menjalankan Docker

```
postgres & redis   ← harus pertama (dependency service lain)
       ↓
api                ← FastAPI, konek ke postgres:5432 dan redis:6379
worker             ← Celery, konek ke redis:6379 (broker) dan postgres:5432
worker-beat        ← Celery Beat, kirim cron task ke redis tiap jam
worker-ai          ← (opsional) IndoBERT/GLiNER/BGE-M3, butuh RAM besar
```

Semua container dalam satu jaringan `social_intel_net` — **postgres dan redis tidak perlu dibuat ulang** saat menambah worker baru. Mereka otomatis restart (`restart: unless-stopped`).

| Container | Image | Port | Fungsi |
|---|---|---|---|
| `social_intel_postgres` | pgvector/pgvector:pg17 | 5432 | Database utama + vector store |
| `social_intel_redis` | redis:7-alpine | 6379 | Celery broker + cache |
| `social_intel_api` | Dockerfile.api | 8000 | FastAPI REST API |
| `social_intel_worker` | Dockerfile.worker | — | Collector + processing + reports |
| `social_intel_worker_beat` | Dockerfile.worker | — | Cron scheduler (trending tiap jam) |
| `social_intel_worker_ai` | Dockerfile.worker-ai | — | AI inference (IndoBERT/GLiNER/BGE-M3) |
| `social_intel_elasticsearch` | elasticsearch:8.11.0 | 9200 | Full-text & semantic index |
| `social_intel_ollama` | ollama/ollama | 11434 | Qwen3 8B LLM |

---

## Konfigurasi .env

| Variable | Keterangan |
|---|---|
| `APP_SECRET_KEY` | Secret key aplikasi (wajib diubah dari default) |
| `JWT_SECRET_KEY` | Secret key untuk JWT token |
| `ENSEMBLE_DATA_API_TOKEN` | Token EnsembleData API — **jangan hardcode di kode** |
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis URL |
| `CELERY_BROKER_URL` | Redis URL untuk Celery broker |
| `CELERY_RESULT_BACKEND` | Redis URL untuk result backend |
| `YOUTUBE_DEFAULT_PROJECT_ID` | UUID project default untuk pipeline YouTube |
| `YOUTUBE_TRENDING_GEO` | Kode negara Google Trends (default: `ID`) |
| `YOUTUBE_TRENDING_PERIOD` | Periode trending: `4h`, `24h`, `48h`, `7d` |
| `YOUTUBE_TRENDING_LIMIT` | Jumlah trending per fetch (default: `10`) |
| `YOUTUBE_MAX_COMMENT_PAGES` | Maks halaman komentar per video (default: `3`) |
| `YOUTUBE_MAX_COMMENTS_PER_VIDEO` | Maks komentar per video (default: `100`) |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` |

---

## Struktur Project

```
social-media-scrap/
│
├── app/                              # Aplikasi utama
│   ├── main.py                       # Entry point FastAPI, import semua model domain
│   ├── api/
│   │   └── v1/                       # Semua router API versi 1
│   │       ├── auth.py               # Register, login, JWT, API key
│   │       ├── keywords.py           # CRUD keyword monitoring
│   │       ├── collectors.py         # Trigger collector multi-platform
│   │       ├── processing.py         # Trigger text processing
│   │       ├── sentiment.py          # Analisis sentimen (IndoBERT)
│   │       ├── topics.py             # Topic modeling
│   │       ├── entities.py           # Named entity recognition
│   │       ├── trends.py             # Tren volume & sentimen over time
│   │       ├── search.py             # Semantic & full-text search
│   │       ├── agents.py             # Multi-agent AI (Qwen3)
│   │       ├── reports.py            # Generate & download laporan
│   │       └── youtube/
│   │           └── router.py         # Pipeline YouTube Intelligence (10 endpoint)
│   │
│   ├── domain/                       # Model SQLAlchemy + Pydantic schemas
│   │   ├── users/                    # User, auth
│   │   ├── projects/                 # Project monitoring
│   │   ├── keywords/                 # Keyword yang dipantau
│   │   ├── posts/                    # Video/post yang di-scrape (url, metadata)
│   │   ├── comments/                 # Komentar dari posts
│   │   ├── sentiments/               # Hasil IndoBERT sentiment
│   │   ├── entities/                 # Hasil NER (Person, Org, Location)
│   │   ├── topics/                   # Hasil topic modeling
│   │   ├── trends/                   # Data tren historis
│   │   ├── reports/                  # Metadata laporan
│   │   ├── trending/                 # Google Trends topics (trending_topics table)
│   │   └── youtube_analysis/         # Hasil lexicon sentiment (lexicon_analyses table)
│   │
│   ├── services/                     # Business logic layer
│   │   ├── auth/                     # Autentikasi, JWT, API key
│   │   ├── collector/                # Orkestrasi collector multi-platform
│   │   ├── processing/               # Text cleaning, normalizer, deduplicator
│   │   ├── sentiment/                # Wrapper IndoBERT
│   │   ├── reports/                  # Generator PDF, DOCX, JSON
│   │   └── youtube/
│   │       ├── pipeline_service.py   # Inti pipeline: trending→keyword→video→komentar→sentiment
│   │       └── schemas.py            # Pydantic schemas untuk YouTube pipeline
│   │
│   ├── ai/                           # Model AI (dimuat oleh worker-ai)
│   │   ├── lexicon/
│   │   │   ├── service.py            # Analisis sentimen rule-based Bahasa Indonesia
│   │   │   └── data/
│   │   │       ├── positive.txt      # 319 kata positif
│   │   │       ├── negative.txt      # 431 kata negatif
│   │   │       └── stopwords.txt     # 273 stopword
│   │   ├── sentiment/                # IndoBERT (torch, transformers)
│   │   ├── ner/                      # GLiNER
│   │   ├── embedding/                # BGE-M3 untuk pgvector
│   │   ├── topic/                    # Topic modeling
│   │   └── llm/                      # Qwen3 via Ollama API
│   │
│   ├── agents/                       # Multi-agent pipeline
│   │   ├── orchestrator.py           # Koordinasi semua agent
│   │   ├── planner_agent.py          # Buat rencana analisis
│   │   ├── search_agent.py           # Cari data relevan
│   │   ├── sentiment_agent.py        # Analisis sentimen kontekstual
│   │   ├── entity_agent.py           # Ekstrak entitas penting
│   │   ├── trend_agent.py            # Analisis tren
│   │   └── summary_agent.py          # Rangkum semua hasil
│   │
│   ├── workers/                      # Celery tasks
│   │   ├── celery_app.py             # Konfigurasi Celery + Beat schedule (crontab tiap jam)
│   │   ├── youtube_worker.py         # 3 task: fetch_trending, collect_pipeline, collect_comments
│   │   ├── collector_worker.py       # Task koleksi multi-platform
│   │   ├── processing_worker.py      # Task text processing
│   │   ├── ai_worker.py              # Task AI inference (IndoBERT, GLiNER)
│   │   ├── embedding_worker.py       # Task generate & simpan embedding pgvector
│   │   ├── sentiment_worker.py       # Task sentiment batch
│   │   ├── topic_worker.py           # Task topic modeling
│   │   ├── report_worker.py          # Task generate laporan async
│   │   └── scheduled_tasks.py        # Task laporan terjadwal (daily/weekly)
│   │
│   ├── integrations/                 # Connector ke sumber data eksternal
│   │   ├── ensemble_data/
│   │   │   ├── client.py             # HTTP client EnsembleData (async, retry)
│   │   │   └── endpoints.py          # Definisi endpoint per platform
│   │   ├── google_trends/
│   │   │   └── connector.py          # Fetch RSS Google Trends + fix encoding latin-1→utf8
│   │   ├── youtube/
│   │   │   └── connector.py          # Search video + ambil komentar (cursor pagination)
│   │   ├── tiktok/                   # TikTok connector
│   │   ├── instagram/                # Instagram connector
│   │   ├── reddit/                   # Reddit connector
│   │   ├── threads/                  # Threads connector
│   │   ├── news/                     # News connector
│   │   └── forum/                    # Forum connector
│   │
│   ├── repositories/                 # Query database (SQLAlchemy async)
│   │   ├── post_repository.py        # CRUD posts + bulk_create (ON CONFLICT DO NOTHING)
│   │   ├── keyword_repository.py
│   │   ├── sentiment_repository.py
│   │   ├── trend_repository.py
│   │   └── entity_repository.py
│   │
│   ├── infrastructure/               # Layer infrastruktur
│   │   ├── database/
│   │   │   ├── base.py               # Base class SQLAlchemy + UUIDMixin + TimestampMixin
│   │   │   └── connection.py         # AsyncEngine, AsyncSessionLocal, get_db()
│   │   ├── redis/
│   │   │   └── connection.py         # get_redis(), close_redis()
│   │   ├── cache/
│   │   │   └── redis_cache.py        # Helper cache decorator
│   │   ├── logging/
│   │   │   └── logger.py             # Setup structlog (stdlib.LoggerFactory)
│   │   ├── middleware/
│   │   │   └── request_id.py         # Inject X-Request-ID ke setiap request
│   │   ├── rate_limit/
│   │   │   └── limiter.py            # FastAPI Depends rate limiter via Redis
│   │   └── security/
│   │       ├── jwt.py                # Create/verify JWT token
│   │       ├── password.py           # Bcrypt hash/verify (pin bcrypt <4.0.0)
│   │       └── api_key.py            # API key management
│   │
│   └── shared/                       # Kode yang dipakai lintas domain
│       ├── config.py                 # Settings dari .env (Pydantic BaseSettings)
│       ├── constants.py              # EMBEDDING_DIMENSION = 1024
│       ├── exceptions.py             # AppException, NotFoundError, ValidationError
│       └── utils.py                  # build_success_response(), build_error_response()
│
├── migrations/                       # Alembic database migrations
│   ├── versions/
│   │   ├── 001_initial_schema.py     # Tabel dasar: users, projects, keywords, posts, comments
│   │   ├── 002_posts_unique_constraint.py   # UNIQUE(external_id, platform) di posts
│   │   ├── 003_posts_processing_columns.py  # is_processed, cleaned_content, language
│   │   ├── 004_pgvector_hnsw_index.py       # HNSW index untuk vector search
│   │   ├── 005_reports_keyword_status.py    # Tabel reports, keyword status
│   │   └── 006_youtube_pipeline_tables.py   # trending_topics, lexicon_analyses
│   └── env.py
│
├── tests/
│   ├── unit/                         # Unit test (pytest)
│   │   ├── test_auth_service.py
│   │   ├── test_normalizer.py
│   │   ├── test_cleaner.py
│   │   ├── test_sentiment_analyzer.py
│   │   └── ... (16 file test)
│   ├── integration/
│   └── e2e/
│
├── deployment/
│   └── docker/
│       ├── Dockerfile.api            # FastAPI + dependencies ringan
│       ├── Dockerfile.worker         # Celery worker (tanpa torch)
│       └── Dockerfile.worker-ai      # Celery AI worker (dengan torch ~4GB)
│
├── docs/                             # Dokumentasi teknis
│   ├── 01.PRD.md                     # Product Requirements
│   ├── 02.TDD.md                     # Technical Design
│   ├── 03.architecture.md            # System Architecture
│   ├── 04.PROJECT-STRUCTURE.md       # Struktur folder detail
│   ├── 05.IMPLEMENTASI PLAN.md       # Rencana implementasi per phase
│   ├── 06.API-SPECIFICATION.md       # Spesifikasi API lama
│   └── 07.PHASE7-PRODUCTION.md       # Production hardening guide
│
├── docker-compose.yml                # Semua service dalam satu file
├── pyproject.toml                    # Dependencies + tool config (Poetry)
├── alembic.ini                       # Konfigurasi Alembic
├── .env                              # Konfigurasi lokal (jangan di-commit)
├── .env.example                      # Template .env
├── Makefile                          # Shortcut perintah umum
├── README.md                         # File ini
└── docs/API.md                       # Dokumentasi API lengkap
```

---

## Dokumentasi

| File | Isi |
|---|---|
| [`docs/API.md`](docs/API.md) | Referensi lengkap semua endpoint + contoh request/response |
| [`docs/01.PRD.md`](docs/01.PRD.md) | Product Requirements Document |
| [`docs/02.TDD.md`](docs/02.TDD.md) | Technical Design Document |
| [`docs/03.architecture.md`](docs/03.architecture.md) | System Architecture |
| [`docs/06.API-SPECIFICATION.md`](docs/06.API-SPECIFICATION.md) | Spesifikasi API (versi awal) |
| [`docs/07.PHASE7-PRODUCTION.md`](docs/07.PHASE7-PRODUCTION.md) | Production hardening guide |

Swagger UI interaktif: **`http://localhost:8000/docs`**
