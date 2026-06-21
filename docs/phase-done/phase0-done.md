# Phase 0 — Foundation & Scaffolding: DONE

**Tanggal selesai:** 2026-06-21

---

## Deliverable

Project boots successfully — seluruh struktur folder, boilerplate, Docker Compose, dan migration siap.

---

## Yang Dihasilkan

### Root Config
| File | Keterangan |
|------|-----------|
| `pyproject.toml` | Dependencies: FastAPI, SQLAlchemy async, Alembic, Celery, pgvector, dll |
| `docker-compose.yml` | Services: postgres (pgvector), redis, elasticsearch, api, worker |
| `.env.example` | Template env vars lengkap |
| `Makefile` | Shortcut: `make up`, `make migrate`, `make test`, dll |
| `alembic.ini` | Konfigurasi Alembic |

### FastAPI Boilerplate
| File | Keterangan |
|------|-----------|
| `app/main.py` | App entry point: 10 routers, CORS, error handlers, health check |
| `app/api/v1/*.py` | 10 router stubs (auth, keywords, collectors, sentiment, topics, entities, trends, search, agents, reports) |

### Infrastructure
| File | Keterangan |
|------|-----------|
| `app/infrastructure/database/connection.py` | Async SQLAlchemy engine + session factory |
| `app/infrastructure/database/base.py` | Base ORM class + UUIDMixin + TimestampMixin |
| `app/infrastructure/redis/connection.py` | Async Redis client |
| `app/infrastructure/logging/logger.py` | structlog JSON logger |
| `app/infrastructure/security/jwt.py` | JWT encode/decode helpers |
| `app/infrastructure/security/api_key.py` | API key generate + hash helpers |

### Domain Models (SQLAlchemy ORM)
| Model | Tabel |
|-------|-------|
| `User` | users |
| `Project` | projects |
| `Keyword` | keywords |
| `Post` | posts (+ embedding VECTOR 1024) |
| `Comment` | comments (+ embedding VECTOR 1024) |
| `Sentiment` | sentiments |
| `Entity` | entities |
| `Topic` | topics |
| `Trend` | trends |
| `Report` | reports |

### EnsembleData Integration (Dynamic)
| File | Keterangan |
|------|-----------|
| `app/integrations/ensemble_data/client.py` | Generic HTTP client — inject token otomatis, retry, async context manager |
| `app/integrations/ensemble_data/endpoints.py` | Registry endpoint TikTok/YouTube/Instagram/Twitter — tambah endpoint baru di sini saja |
| `app/integrations/tiktok/connector.py` | TikTok connector stub |
| `app/integrations/youtube/connector.py` | YouTube connector stub |
| `app/integrations/instagram/connector.py` | Instagram connector stub |

### Database Migration
| File | Keterangan |
|------|-----------|
| `migrations/env.py` | Alembic async env, auto-import semua models |
| `migrations/versions/001_initial_schema.py` | Schema lengkap: 12 tabel + pgvector extension |

### Docker Services
| Service | Image | Port |
|---------|-------|------|
| `postgres` | pgvector/pgvector:pg17 | 5432 |
| `redis` | redis:7-alpine | 6379 |
| `elasticsearch` | elasticsearch:8.11.0 | 9200 |
| `api` | Dockerfile.api | 8000 |
| `worker` | Dockerfile.worker | — |

---

## API Endpoint (Boilerplate)

```
GET  /health
POST /api/v1/auth/register
POST /api/v1/auth/login
GET  /api/v1/keywords/
POST /api/v1/collectors/collect
POST /api/v1/sentiment/analyze
... (semua masih stub)
```

---

## Cara Jalankan

```bash
cp .env.example .env
# isi ENSEMBLE_DATA_API_TOKEN di .env
make build
make up
# cek: http://localhost:8000/health
# cek: http://localhost:8000/docs
```

---

## Keputusan Arsitektur

- **pgvector** dipilih untuk vector search (bukan Qdrant) — sesuai TDD.md yang override architecture.md
- **EnsembleData client bersifat dinamis** — endpoint ditambah di `endpoints.py` tanpa mengubah `client.py`
- **Modular Monolith** — satu service, bukan microservice
- **Async everywhere** — SQLAlchemy async, Redis async, httpx async
