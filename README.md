# Social Intelligence Platform

Platform backend berbasis AI untuk monitoring dan analisis media sosial, berita, dan forum publik. Menghasilkan insight sentimen, tren, entitas, dan laporan eksekutif secara otomatis.

---

## Daftar Isi

- [Fitur Utama](#fitur-utama)
- [Tech Stack](#tech-stack)
- [Arsitektur](#arsitektur)
- [Quick Start](#quick-start)
- [Konfigurasi](#konfigurasi)
- [API Endpoints](#api-endpoints)
- [Struktur Project](#struktur-project)
- [Development](#development)
- [Dokumentasi](#dokumentasi)

---

## Fitur Utama

| Fitur | Deskripsi |
|-------|-----------|
| **Collector** | Ambil data dari TikTok, YouTube, Instagram, News, Forum via EnsembleData API |
| **Processing** | Cleaning, deduplication, normalization teks |
| **Sentiment Analysis** | IndoBERT — label Positive / Negative / Neutral |
| **NER** | GLiNER — ekstraksi Person, Org, Location, Product |
| **Embedding & Search** | BGE-M3 + pgvector — semantic search bahasa Indonesia |
| **Topic Modeling** | Pengelompokan topik otomatis dari post |
| **Multi-Agent AI** | Planner → Search → Sentiment → Entity → Trend → Summary Agent |
| **Report Generator** | Output JSON, PDF, DOCX |
| **Production Hardening** | Redis cache, rate limiting, structured logging, health check, Celery Beat |

---

## Tech Stack

| Layer | Teknologi |
|-------|-----------|
| API | FastAPI 0.115 + Uvicorn |
| Database | PostgreSQL 17 + pgvector |
| ORM | SQLAlchemy 2.x async |
| Migration | Alembic |
| Cache & Queue | Redis 7 |
| Task Queue | Celery 5 + Celery Beat |
| Search | Elasticsearch 8.11 |
| AI — Sentiment | IndoBERT (`mdhugol/indonesia-bert-sentiment-classification`) |
| AI — NER | GLiNER (`urchade/gliner_multi-v2.1`) |
| AI — Embedding | BGE-M3 (`BAAI/bge-m3`) |
| AI — LLM | Qwen3 8B via Ollama |
| Validation | Pydantic v2 |
| Logging | structlog (JSON) |
| Container | Docker + Docker Compose |

---

## Arsitektur

```
Client
  │
  ▼
FastAPI (api/)          ← request ID middleware, auth, rate limiting
  │
  ▼
Service Layer           ← business logic
  │
  ├─► Repository        ← database queries (PostgreSQL)
  ├─► AI Layer          ← IndoBERT / GLiNER / BGE-M3
  ├─► Agents            ← multi-agent pipeline (Qwen3 via Ollama)
  └─► Celery Workers    ← async tasks (collector, AI, reports)
         │
         ▼
       Redis (broker/result/cache)
```

**Data flow lengkap:**

```
Source (TikTok/YouTube/News/Forum)
  → Collector Worker  (Celery queue: collector)
  → Processing Worker (Celery queue: processing)
  → AI Worker         (Celery queue: ai — torch/transformers)
  → PostgreSQL + Elasticsearch
  → Agent Service     (query-time via Qwen3)
  → Report Generator  (JSON / PDF / DOCX)
```

---

## Quick Start

### Prasyarat

- Docker & Docker Compose
- EnsembleData API token ([ensembledata.com](https://ensembledata.com/))
- Minimum 8 GB RAM (untuk AI worker dengan torch)

### 1. Clone & konfigurasi

```bash
git clone <repo-url>
cd social-intelligence
cp .env.example .env
# Edit .env — isi ENSEMBLE_DATA_API_TOKEN, JWT_SECRET_KEY, APP_SECRET_KEY
```

### 2. Jalankan semua service

```bash
docker compose up -d
```

Service yang berjalan:

| Container | Port | Keterangan |
|-----------|------|------------|
| `social_intel_api` | 8000 | FastAPI app |
| `social_intel_worker` | — | Celery worker (collector/processing/reports) |
| `social_intel_worker_ai` | — | Celery AI worker (sentiment/NER/embedding) |
| `social_intel_worker_beat` | — | Celery Beat scheduler |
| `social_intel_postgres` | 5432 | PostgreSQL 17 |
| `social_intel_redis` | 6379 | Redis 7 |
| `social_intel_elasticsearch` | 9200 | Elasticsearch 8.11 |
| `social_intel_ollama` | 11434 | Ollama (Qwen3 8B) |

### 3. Jalankan migrasi database

```bash
docker compose exec api alembic upgrade head
```

### 4. Pull model Qwen3

```bash
docker compose exec ollama ollama pull qwen3:8b
```

### 5. Verifikasi

```bash
curl http://localhost:8000/health
```

Response normal (`status: ok`):
```json
{
  "success": true,
  "data": {
    "status": "ok",
    "version": "1.0.0",
    "checks": {
      "database": {"status": "ok"},
      "redis": {"status": "ok"},
      "ollama": {"status": "ok"},
      "elasticsearch": {"status": "ok"}
    }
  }
}
```

Swagger UI tersedia di: `http://localhost:8000/docs`

---

## Konfigurasi

Semua konfigurasi via file `.env`. Salin dari `.env.example` dan sesuaikan.

| Variable | Default | Keterangan |
|----------|---------|------------|
| `APP_SECRET_KEY` | *(wajib diubah)* | Secret key aplikasi |
| `JWT_SECRET_KEY` | *(wajib diubah)* | Secret key JWT |
| `ENSEMBLE_DATA_API_TOKEN` | *(wajib diisi)* | API token EnsembleData |
| `DATABASE_URL` | `postgresql+asyncpg://...` | PostgreSQL connection string |
| `REDIS_URL` | `redis://localhost:6379` | Redis URL |
| `OLLAMA_BASE_URL` | `http://ollama:11434` | Ollama endpoint |
| `OLLAMA_MODEL_NAME` | `qwen3:8b` | Model LLM yang dipakai |
| `RATE_LIMIT_AGENTS_MAX_REQUESTS` | `10` | Max request `/agents/ask` per window |
| `RATE_LIMIT_AGENTS_WINDOW_SECONDS` | `60` | Window rate limit (detik) |
| `LOG_LEVEL` | `INFO` | Level log: DEBUG / INFO / WARNING |
| `LOG_FORMAT` | `json` | Format log: `json` atau `console` |

---

## API Endpoints

Dokumentasi lengkap: [`docs/06.API-SPECIFICATION.md`](docs/06.API-SPECIFICATION.md)

| Method | Endpoint | Keterangan |
|--------|----------|------------|
| `GET` | `/health` | Health check (DB, Redis, Ollama, ES) |
| `POST` | `/api/v1/auth/register` | Registrasi user |
| `POST` | `/api/v1/auth/login` | Login, dapat JWT |
| `GET/POST` | `/api/v1/keywords/` | CRUD keyword monitoring |
| `POST` | `/api/v1/collectors/collect` | Trigger koleksi data |
| `GET` | `/api/v1/collectors/jobs/{job_id}` | Cek status job |
| `POST` | `/api/v1/processing/process` | Trigger processing post |
| `GET` | `/api/v1/sentiment/summary/{keyword_id}` | Distribusi sentimen (cached 5m) |
| `GET` | `/api/v1/trends/keyword/{keyword_id}` | Volume trend (cached 5m) |
| `GET` | `/api/v1/trends/sentiment/{keyword_id}` | Tren sentimen (cached 5m) |
| `GET` | `/api/v1/search/` | Semantic search |
| `POST` | `/api/v1/agents/ask` | Tanya ke multi-agent (async, rate limited) |
| `POST` | `/api/v1/agents/ask-sync` | Tanya ke multi-agent (sync, rate limited) |
| `POST` | `/api/v1/reports/generate` | Generate laporan async |
| `GET` | `/api/v1/reports/{report_id}/download` | Download PDF/DOCX/JSON |

---

## Struktur Project

```
social-intelligence/
├── app/
│   ├── api/v1/              # FastAPI routers
│   ├── domain/              # Models & schemas per domain
│   │   ├── users/
│   │   ├── projects/
│   │   ├── keywords/
│   │   ├── posts/
│   │   ├── comments/
│   │   ├── sentiments/
│   │   ├── entities/
│   │   ├── topics/
│   │   ├── trends/
│   │   └── reports/
│   ├── repositories/        # Database queries
│   ├── services/            # Business logic
│   │   ├── auth/
│   │   ├── agents/
│   │   └── reports/
│   ├── ai/                  # AI model wrappers
│   │   ├── sentiment/       # IndoBERT
│   │   ├── ner/             # GLiNER
│   │   ├── embedding/       # BGE-M3
│   │   ├── topic/
│   │   └── llm/             # Qwen3 via Ollama
│   ├── agents/              # Multi-agent pipeline
│   │   ├── orchestrator.py
│   │   ├── planner_agent.py
│   │   ├── search_agent.py
│   │   ├── sentiment_agent.py
│   │   ├── entity_agent.py
│   │   ├── trend_agent.py
│   │   └── summary_agent.py
│   ├── workers/             # Celery tasks
│   │   ├── celery_app.py    # App + Beat schedule
│   │   ├── collector_worker.py
│   │   ├── ai_worker.py
│   │   ├── report_worker.py
│   │   └── scheduled_tasks.py
│   ├── integrations/        # EnsembleData connectors
│   │   ├── ensemble_data/
│   │   ├── news/
│   │   └── forum/
│   ├── infrastructure/
│   │   ├── database/
│   │   ├── redis/
│   │   ├── cache/           # Redis cache helpers
│   │   ├── rate_limit/      # Rate limiter dependency
│   │   ├── middleware/      # Request ID middleware
│   │   ├── logging/
│   │   └── security/
│   └── shared/              # Config, exceptions, utils
├── migrations/              # Alembic versions
├── tests/
│   ├── unit/
│   └── integration/
├── deployment/
│   └── docker/
│       ├── Dockerfile.api
│       ├── Dockerfile.worker
│       └── Dockerfile.worker-ai
├── docs/
└── docker-compose.yml
```

---

## Development

### Install dependencies

```bash
pip install poetry
poetry install
```

### Jalankan tanpa Docker

```bash
# Pastikan PostgreSQL, Redis, Elasticsearch, Ollama berjalan lokal
uvicorn app.main:app --reload --port 8000
```

### Jalankan tests

```bash
pytest tests/ -v
pytest tests/unit/ -v --cov=app
```

### Linting & type check

```bash
ruff check app/
black app/
mypy app/
```

### Buat migrasi baru

```bash
alembic revision --autogenerate -m "nama_migrasi"
alembic upgrade head
```

---

## Dokumentasi

| File | Isi |
|------|-----|
| [`docs/01.PRD.md`](docs/01.PRD.md) | Product Requirements Document |
| [`docs/02.TDD.md`](docs/02.TDD.md) | Technical Design Document |
| [`docs/03.architecture.md`](docs/03.architecture.md) | System Architecture |
| [`docs/04.PROJECT-STRUCTURE.md`](docs/04.PROJECT-STRUCTURE.md) | Struktur folder |
| [`docs/05.IMPLEMENTASI PLAN.md`](docs/05.IMPLEMENTASI%20PLAN.md) | Rencana implementasi per phase |
| [`docs/06.API-SPECIFICATION.md`](docs/06.API-SPECIFICATION.md) | API reference lengkap |
| [`docs/07.PHASE7-PRODUCTION.md`](docs/07.PHASE7-PRODUCTION.md) | Production hardening guide |
