# Phase 4 — AI Service ✅

**Status:** SELESAI  
**Tanggal:** 2026-06-22  

---

## Ringkasan

Phase 4 mengimplementasikan 4 model AI yang bekerja sebagai pipeline inference:
1. **IndoBERT** (mdhugol/indonesia-bert-sentiment-classification) — analisis sentimen Indonesia
2. **BGE-M3** (BAAI/bge-m3) — embedding 1024 dimensi untuk pgvector semantic search
3. **GLiNER** (urchade/gliner_multi-v2.1) — Named Entity Recognition multilingual
4. **Qwen3 8B** via Ollama — LLM untuk reasoning, summarization, topic classification

Semua model di-load **secara lazy** (hanya saat pertama digunakan) dan di-cache per worker process.

---

## Arsitektur AI Pipeline

```
POST /sentiment/analyze  ─── Celery: analyze_keyword_task
                                      │
                         ┌────────────┼─────────────────────┐
                         ▼            ▼                      ▼
               [IndoBERT]        [GLiNER]             [BGE-M3]
               Sentiment         NER Extract          Embedding
                  │                  │                    │
                  ▼                  ▼                    ▼
            sentiments table    entities table    posts.embedding
                                                 (pgvector VECTOR(1024))
```

---

## Komponen Baru

### 1. SentimentAnalyzer (`app/services/ai/sentiment_analyzer.py`)

IndoBERT fine-tuned untuk klasifikasi sentimen Bahasa Indonesia.

| Label Model | Output |
|-------------|--------|
| `LABEL_0`   | `negative` |
| `LABEL_1`   | `neutral`  |
| `LABEL_2`   | `positive` |

```python
from app.services.ai.sentiment_analyzer import SentimentAnalyzer

analyzer = SentimentAnalyzer.get_instance()
result = analyzer.analyze("produk ini sangat bagus dan memuaskan!")
# SentimentResult(label='positive', score=0.94, model_version='mdhugol/...')

# Batch processing
results = analyzer.analyze_batch(["bagus", "buruk", "biasa"])
```

### 2. EmbeddingGenerator (`app/services/ai/embedding_generator.py`)

BGE-M3 menghasilkan embedding 1024-dim yang normalized. Compatible langsung dengan pgvector `<=>` cosine distance operator.

```python
from app.services.ai.embedding_generator import EmbeddingGenerator

gen = EmbeddingGenerator.get_instance()
embedding = gen.generate("teks konten post")
# list[float] dengan 1024 elemen, magnitude = 1.0 (normalized)

# Batch processing
embeddings = gen.generate_batch(texts, batch_size=32)
```

### 3. NERExtractor (`app/services/ai/ner_extractor.py`)

GLiNER multilingual NER — mendukung Bahasa Indonesia dan 20+ bahasa lainnya.

**Entity types yang diextract dari social media:**

| Type | Contoh |
|------|--------|
| `PERSON` | "Joko Widodo", "Elon Musk" |
| `ORGANIZATION` | "Telkom", "Gojek", "Bank BRI" |
| `LOCATION` | "Jakarta", "Surabaya" |
| `PRODUCT` | "iPhone 15", "Indomie" |
| `EVENT` | "Lebaran 2025", "G20" |
| `DATE` | "kemarin", "Senin depan" |
| `MONEY` | "Rp 500 ribu", "$100" |
| `LAW` | "UU Cipta Kerja" |

```python
from app.services.ai.ner_extractor import NERExtractor

extractor = NERExtractor.get_instance()
entities = extractor.extract("Joko Widodo berkunjung ke Surabaya bersama Telkom")
# [EntityResult(text='Joko Widodo', entity_type='PERSON', score=0.98, ...),
#  EntityResult(text='Surabaya', entity_type='LOCATION', score=0.96, ...),
#  EntityResult(text='Telkom', entity_type='ORGANIZATION', score=0.94, ...)]
```

### 4. OllamaClient (`app/services/ai/llm_client.py`)

Async HTTP client ke Ollama API untuk model Qwen3 8B.

```python
from app.services.ai.llm_client import OllamaClient

client = OllamaClient()

# Summarization
summary = await client.summarize("teks panjang...", max_words=50, lang="id")

# Topic classification
topic = await client.classify_topic("teks post...", topics=["Politik", "Ekonomi", "Olahraga"])

# Custom generation
result = await client.generate(
    "Apa sentimen dominan dari berita ini?",
    system_prompt="Kamu adalah analis media sosial.",
    temperature=0.3,
)

# Health check
ok = await client.health_check()  # True/False
```

### 5. AIService (`app/services/ai/service.py`)

Orchestrator yang menjalankan seluruh pipeline per post atau per keyword.

```python
from app.services.ai.service import AIService
from app.services.ai.schemas import AnalyzeRequest

service = AIService(db)

# Analyze satu post
result = await service.analyze_post(
    post_id=post_id,
    run_sentiment=True,
    run_ner=True,
    run_embedding=True,
)

# Analyze semua post satu keyword
stats = await service.analyze_keyword(AnalyzeRequest(
    keyword_id=keyword_id,
    force_reanalyze=False,
    run_sentiment=True,
    run_ner=True,
    run_embedding=True,
))
```

---

## Repository Baru

### SentimentRepository (`app/repositories/sentiment_repository.py`)

| Method | Deskripsi |
|--------|-----------|
| `get_by_post_id(post_id)` | Ambil sentimen satu post |
| `create(sentiment)` | Simpan satu sentimen |
| `bulk_create(sentiments)` | Simpan batch |
| `delete_by_post_id(post_id)` | Hapus sentimen (untuk reanalysis) |
| `list_by_keyword(keyword_id)` | Semua sentimen satu keyword |
| `count_by_label_for_keyword(keyword_id)` | Distribusi `{positive: N, negative: N, neutral: N}` |

### EntityRepository (`app/repositories/entity_repository.py`)

| Method | Deskripsi |
|--------|-----------|
| `list_by_post_id(post_id)` | Entities satu post |
| `bulk_create(entities)` | Simpan batch |
| `delete_by_post_id(post_id)` | Hapus (untuk reanalysis) |
| `list_by_keyword(keyword_id, entity_type)` | Filter by type |
| `top_entities_by_keyword(keyword_id, top_n)` | Top N entity paling sering |

### PostRepository — method baru

| Method | Deskripsi |
|--------|-----------|
| `update_embedding(post_id, embedding)` | Simpan vector ke pgvector |
| `list_processed_by_keyword(keyword_id, force)` | Post siap AI inference |
| `search_by_embedding(embedding, keyword_id, limit)` | Semantic search via cosine distance |

---

## API Endpoints Baru

### Sentiment (`/api/v1/sentiment`)

| Method | Path | Deskripsi |
|--------|------|-----------|
| `POST` | `/analyze` | Trigger AI pipeline via Celery (async, 202) |
| `POST` | `/analyze-sync` | Jalankan sinkron (dev/debug) |
| `GET` | `/results/{post_id}` | Hasil sentimen satu post |
| `GET` | `/summary/{keyword_id}` | Distribusi sentimen keyword |

### Entities (`/api/v1/entities`)

| Method | Path | Deskripsi |
|--------|------|-----------|
| `GET` | `/post/{post_id}` | Entities satu post |
| `GET` | `/keyword/{keyword_id}` | Entities satu keyword (dengan filter type) |
| `GET` | `/top/{keyword_id}` | Top N entity paling sering muncul |
| `GET` | `/{entity_id}` | Detail satu entity |

---

## Celery Tasks Baru

| Task Name | Deskripsi |
|-----------|-----------|
| `workers.analyze_post` | Inference satu post (sentiment + NER + embedding) |
| `workers.analyze_keyword` | Inference semua post satu keyword |

```python
from app.workers.ai_worker import analyze_keyword_task

task = analyze_keyword_task.delay(
    str(keyword_id),
    force_reanalyze=False,
    run_sentiment=True,
    run_ner=True,
    run_embedding=True,
)
# Track via GET /api/v1/collectors/jobs/{task.id}
```

---

## Docker — Perubahan

### Service Baru: `ollama`

Menjalankan Qwen3 8B LLM:

```bash
# Setelah docker compose up, pull model Qwen3
docker compose exec ollama ollama pull qwen3:8b
```

### Service Baru: `worker-ai`

Container terpisah dengan ML dependencies untuk AI inference:

```
Dockerfile.worker-ai
├── python:3.12-slim (base)
├── Poetry install (base deps)
├── requirements-ai.txt (torch CPU + transformers + gliner)
└── CMD: celery worker --queues=ai,celery --concurrency=1
```

Memory limit: 8GB (torch + models ~3-4GB RAM)

### Worker biasa (`worker`) 

Sekarang hanya menangani queue ringan:
- `collector` — scraping EnsembleData
- `processing` — clean, normalize, dedup teks
- `celery` — default queue

### Volume baru: `models_cache`

Persistent volume untuk cache HuggingFace models agar tidak re-download setiap restart.

---

## Cara Menjalankan

```bash
# 1. Start semua services
docker compose up -d

# 2. Jalankan migration
docker compose exec api alembic upgrade head

# 3. Pull Qwen3 8B ke Ollama (hanya sekali ~5GB)
docker compose exec ollama ollama pull qwen3:8b

# 4. Collect posts (Phase 2)
curl -X POST http://localhost:8000/api/v1/collectors/collect \
  -H "Authorization: Bearer {token}" \
  -d '{"keyword_id": "...", "platforms": ["tiktok"]}'

# 5. Process posts (Phase 3)
curl -X POST http://localhost:8000/api/v1/processing/trigger \
  -H "Authorization: Bearer {token}" \
  -d '{"keyword_id": "..."}'

# 6. AI Analysis (Phase 4)
curl -X POST http://localhost:8000/api/v1/sentiment/analyze \
  -H "Authorization: Bearer {token}" \
  -d '{"keyword_id": "...", "run_sentiment": true, "run_ner": true, "run_embedding": true}'

# 7. Cek hasil sentimen
curl http://localhost:8000/api/v1/sentiment/summary/{keyword_id} \
  -H "Authorization: Bearer {token}"

# 8. Cek top entities
curl http://localhost:8000/api/v1/entities/top/{keyword_id}?top_n=10 \
  -H "Authorization: Bearer {token}"
```

---

## Alur Lengkap Phase 2 → 3 → 4

```
Phase 2: Collect  →  posts.content (raw)
Phase 3: Process  →  posts.cleaned_content + is_processed + language
Phase 4: AI       →  posts.embedding (pgvector)
                  →  sentiments.label + score
                  →  entities.text + entity_type
```

---

## Tests

**12/12 tests passed:**

| File | Tests |
|------|-------|
| `test_sentiment_analyzer.py` | 7 tests (label mapping, empty, positive, negative, batch) |
| `test_ai_schemas.py` | 5 tests (AIAnalysisResult, KeywordStats, AnalyzeRequest, EntityResult) |

---

## Files yang Dibuat/Dimodifikasi

```
app/
├── services/ai/
│   ├── __init__.py                           ← NEW
│   ├── schemas.py                            ← NEW
│   ├── sentiment_analyzer.py                 ← NEW (IndoBERT)
│   ├── embedding_generator.py                ← NEW (BGE-M3)
│   ├── ner_extractor.py                      ← NEW (GLiNER)
│   ├── llm_client.py                         ← NEW (Ollama/Qwen3)
│   └── service.py                            ← NEW (Orchestrator)
├── repositories/
│   ├── sentiment_repository.py               ← NEW
│   ├── entity_repository.py                  ← NEW
│   └── post_repository.py                    ← MODIFIED (+3 methods)
├── workers/
│   ├── ai_worker.py                          ← NEW
│   ├── sentiment_worker.py                   ← MODIFIED (delegate)
│   ├── embedding_worker.py                   ← MODIFIED (delegate)
│   └── celery_app.py                         ← MODIFIED (include ai_worker)
├── api/v1/
│   ├── sentiment.py                          ← MODIFIED (full implementation)
│   └── entities.py                           ← MODIFIED (full implementation)
└── shared/config.py                          ← MODIFIED (AI settings)
deployment/docker/
└── Dockerfile.worker-ai                      ← NEW
requirements-ai.txt                           ← NEW
docker-compose.yml                            ← MODIFIED (ollama + worker-ai)
.env.example                                  ← MODIFIED (AI env vars)
tests/unit/
├── test_sentiment_analyzer.py                ← NEW
└── test_ai_schemas.py                        ← NEW
docs/phase-done/
└── phase4-done.md                            ← NEW (dokumen ini)
```

---

## Catatan Penting

### Model Download (Pertama Kali)
Models otomatis di-download ke `models_cache` volume saat pertama kali digunakan:
- IndoBERT: ~430MB
- BGE-M3: ~570MB
- GLiNER: ~460MB
- Qwen3 8B (via Ollama): ~5GB

### CPU vs GPU
Default konfigurasi menggunakan **CPU** untuk portabilitas. Untuk GPU:
1. Di `sentiment_analyzer.py`: ganti `device=-1` → `device=0`
2. Di `embedding_generator.py`: SentenceTransformer otomatis detect GPU
3. Di `Dockerfile.worker-ai`: ganti base image ke `pytorch/pytorch:2.3.1-cuda12.1-cudnn8-runtime`
4. Di `docker-compose.yml`: tambah `deploy.resources.reservations.devices` untuk NVIDIA

### Concurrency Worker AI
`--concurrency=1` karena memory model besar. Untuk throughput lebih tinggi: scale horizontal dengan `docker compose up --scale worker-ai=3`

## Siap untuk Phase 5 — Agent Service

Phase 5 akan menggunakan hasil Phase 4 untuk:
- **Planner Agent** — merencanakan multi-step analysis menggunakan Qwen3 8B
- **Search Agent** — semantic search menggunakan pgvector embeddings dari Phase 4
- **Sentiment Agent** — aggregasi dan trend sentimen dari data Phase 4
- **Entity Agent** — graph analysis dari entitas yang diextract Phase 4
- **Summary Agent** — ringkasan executive dari OllamaClient (Qwen3 8B)
