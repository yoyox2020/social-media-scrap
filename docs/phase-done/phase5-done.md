# Phase 5 — Agent Service ✅

**Status:** SELESAI  
**Tanggal:** 2026-06-22  

---

## Ringkasan

Phase 5 mengimplementasikan **Multi-Agent System** yang menjawab pertanyaan user dengan mengkoordinasikan beberapa specialized agents. Qwen3 8B (via Ollama) berperan sebagai otak untuk planning dan summarization.

---

## Arsitektur Multi-Agent

```
User POST /agents/ask
         │
         ▼
  PlannerAgent  ←── Rule-based (fast) atau LLM-based (Qwen3)
         │
         ├──[search]────→ SearchAgent     (pgvector + PostgreSQL FTS)
         ├──[sentiment]─→ SentimentAgent  (ambil data dari Phase 4)
         ├──[entity]────→ EntityAgent     (top entities per type)
         ├──[trend]─────→ TrendAgent      (volume + sentimen over time)
         └──[summary]───→ SummaryAgent    (Qwen3 8B / template fallback)
                                │
                                ▼
                         AgentResponse
                    (answer + details + sources)
```

---

## Komponen Baru

### 1. PlannerAgent (`app/services/agents/planner.py`)

Menentukan agents yang perlu dipanggil berdasarkan pertanyaan user.

**Mode 1 — Rule-based (default, `use_llm=False`):**
```python
planner = PlannerAgent(db)
plan = planner._plan_with_rules("Apa sentimen terkait produk ini?")
# → ["sentiment", "summary"]

plan = planner._plan_with_rules("Siapa tokoh yang sering disebutkan?")
# → ["entity", "summary"]

plan = planner._plan_with_rules("Cari dan analisis tren sentimen terkini")
# → ["search", "sentiment", "trend", "summary"]
```

**Keyword → Agent Mapping:**

| Kata kunci | Agent |
|-----------|-------|
| cari, temukan, search | `search` |
| sentimen, opini, positif/negatif | `sentiment` |
| siapa, tokoh, orang, organisasi | `entity` |
| tren, trend, waktu, minggu, naik | `trend` |
| (selalu) | `summary` |

**Mode 2 — LLM-based (`use_llm=True`):**
Menggunakan Qwen3 8B untuk merencanakan agents. Lebih akurat untuk pertanyaan kompleks. Fallback otomatis ke rule-based jika Ollama tidak tersedia.

### 2. SearchAgent (`app/services/agents/search_agent.py`)

Mencari post yang relevan dengan dua metode:

```python
# Semantic search — menggunakan embedding BGE-M3 + pgvector cosine distance
results = await search_agent._semantic_search(context)

# Full-text search — PostgreSQL tsvector
results = await search_agent._fulltext_search(context)

# Keduanya digabung dan dideduplikasi
```

### 3. SentimentAgent (`app/services/agents/sentiment_agent.py`)

Menganalisis distribusi sentimen dari data Phase 4:

```json
{
  "total_analyzed": 250,
  "distribution": {"positive": 150, "negative": 50, "neutral": 50},
  "percentages": {"positive": 60.0, "negative": 20.0, "neutral": 20.0},
  "dominant_sentiment": "positive",
  "examples": [{"post_id": "...", "label": "positive", "score": 0.96, "excerpt": "..."}]
}
```

### 4. EntityAgent (`app/services/agents/entity_agent.py`)

Top entities per type dari data Phase 4:

```json
{
  "by_type": {
    "PERSON": [{"text": "Joko Widodo", "count": 45}],
    "ORGANIZATION": [{"text": "Telkom", "count": 32}],
    "LOCATION": [{"text": "Jakarta", "count": 78}]
  }
}
```

### 5. TrendAgent (`app/services/agents/trend_agent.py`)

Volume post per periode + sentimen trend:

```json
{
  "volume_trend": [
    {"period": "2025-01-01", "count": 45},
    {"period": "2025-01-02", "count": 67}
  ],
  "sentiment_trend": [
    {"period": "2025-01-01", "positive": 30, "negative": 10, "neutral": 5}
  ],
  "trend_direction": "naik",
  "total_posts": 112
}
```

### 6. SummaryAgent (`app/services/agents/summary_agent.py`)

Menggunakan Qwen3 8B untuk mengagregasi hasil semua agents menjadi jawaban akhir. Fallback ke template jika Ollama tidak tersedia.

### 7. TopicAgent (`app/services/agents/topic_agent.py`)

Menggunakan Qwen3 untuk mendeteksi topik dominan dari sample post:

```python
agent = TopicAgent(db, num_topics=5)
result = await agent.run(context)
# data.topics = [
#   {"name": "Kualitas Produk", "description": "...", "keywords": ["bagus", "kualitas"]},
#   {"name": "Harga", "description": "...", "keywords": ["mahal", "murah", "diskon"]}
# ]
```

### 8. AgentService (`app/services/agents/service.py`)

Orchestrator pipeline:

```python
service = AgentService(db)
response = await service.ask(AskRequest(
    question="Apa sentimen dan topik utama tentang keyword ini?",
    keyword_id=keyword_id,
    use_llm_planner=False,
))

print(response.answer)          # jawaban akhir dari Qwen3
print(response.agent_plan)      # ["sentiment", "entity", "summary"]
print(response.processing_time_ms)  # e.g. 2340
```

---

## API Endpoints

### Agents (`/api/v1/agents`)

| Method | Path | Deskripsi |
|--------|------|-----------|
| `POST` | `/ask` | Trigger pipeline via Celery (async, 202) |
| `POST` | `/ask-sync` | Jalankan sinkron (dev/debug) |

**Contoh request:**
```json
POST /api/v1/agents/ask
{
  "question": "Apa sentimen dominan tentang produk ini di TikTok?",
  "keyword_id": "550e8400-...",
  "platform": "tiktok",
  "use_llm_planner": false
}
```

**Contoh response:**
```json
{
  "success": true,
  "data": {
    "question": "Apa sentimen dominan...",
    "keyword_id": "550e8400-...",
    "answer": "Sentimen positif mendominasi (65%) untuk keyword ini di TikTok...",
    "agent_plan": ["sentiment", "entity", "summary"],
    "details": {
      "sentiment": {"agent": "sentiment", "summary": "Positif 65%...", "data": {...}},
      "entity": {"agent": "entity", "summary": "Top entity: Samsung...", "data": {...}},
      "summary": {"agent": "summary", "summary": "Jawaban akhir...", "data": {...}}
    },
    "processing_time_ms": 3250,
    "errors": []
  }
}
```

### Search (`/api/v1/search`)

| Method | Path | Deskripsi |
|--------|------|-----------|
| `POST` | `/semantic` | Semantic search via pgvector |
| `POST` | `/fulltext` | Full-text search via PostgreSQL tsvector |

### Topics (`/api/v1/topics`)

| Method | Path | Deskripsi |
|--------|------|-----------|
| `POST` | `/detect` | Trigger topic detection via Celery |
| `POST` | `/detect-sync` | Deteksi topik sinkron |
| `GET` | `/keyword/{keyword_id}` | List topik yang sudah terdeteksi |

### Trends (`/api/v1/trends`)

| Method | Path | Deskripsi |
|--------|------|-----------|
| `GET` | `/keyword/{keyword_id}` | Volume post per periode (`?period=day\|week\|month`) |
| `GET` | `/sentiment/{keyword_id}` | Tren sentimen per periode |
| `GET` | `/platforms/{keyword_id}` | Breakdown per platform |

---

## Celery Tasks

| Task | File | Deskripsi |
|------|------|-----------|
| `workers.ask_agent` | `topic_worker.py` | Multi-agent pipeline async |
| `workers.detect_topics` | `topic_worker.py` | Topic detection via Qwen3 |

---

## Repositories Baru

- `app/repositories/topic_repository.py` — CRUD untuk Topic
- `app/repositories/trend_repository.py` — CRUD + upsert untuk Trend

---

## Alur Penggunaan Lengkap

```bash
# 1. Collect data (Phase 2)
curl -X POST /api/v1/collectors/collect -d '{"keyword_id": "...", "platforms": ["tiktok"]}'

# 2. Process teks (Phase 3)
curl -X POST /api/v1/processing/trigger -d '{"keyword_id": "..."}'

# 3. AI Analysis (Phase 4) — sentiment, NER, embedding
curl -X POST /api/v1/sentiment/analyze -d '{"keyword_id": "..."}'

# 4. Tanya ke Agent (Phase 5)
curl -X POST /api/v1/agents/ask-sync \
  -d '{"question": "Apa topik dan sentimen dominan?", "keyword_id": "..."}'

# 5. Lihat tren
curl /api/v1/trends/keyword/{keyword_id}?period=day

# 6. Search semantik
curl -X POST /api/v1/search/semantic \
  -d '{"query": "kualitas produk buruk", "keyword_id": "...", "limit": 5}'
```

---

## Tests

**13/13 tests passed:**

| File | Tests |
|------|-------|
| `test_agent_schemas.py` | 6 tests (AgentResult, AgentResponse, AskRequest, Context) |
| `test_planner_agent.py` | 7 tests (rule-based planning: sentiment, entity, trend, search, default, no-dup, order) |

---

## Files yang Dibuat/Dimodifikasi

```
app/
├── services/agents/
│   ├── __init__.py                    ← NEW
│   ├── schemas.py                     ← NEW (AskRequest, AgentContext, AgentResponse, ...)
│   ├── base.py                        ← NEW (BaseAgent abstract)
│   ├── planner.py                     ← NEW (rule-based + LLM planning)
│   ├── search_agent.py                ← NEW (pgvector + PG full-text)
│   ├── sentiment_agent.py             ← NEW
│   ├── entity_agent.py                ← NEW
│   ├── trend_agent.py                 ← NEW
│   ├── summary_agent.py               ← NEW (Qwen3 summarization)
│   ├── topic_agent.py                 ← NEW (Qwen3 topic detection)
│   └── service.py                     ← NEW (AgentService orchestrator)
├── repositories/
│   ├── topic_repository.py            ← NEW
│   └── trend_repository.py            ← NEW
├── workers/
│   └── topic_worker.py                ← MODIFIED (ask_agent + detect_topics tasks)
├── api/v1/
│   ├── agents.py                      ← MODIFIED (full implementation)
│   ├── search.py                      ← MODIFIED (semantic + fulltext)
│   ├── topics.py                      ← MODIFIED (detect + list)
│   └── trends.py                      ← MODIFIED (keyword + sentiment + platform)
tests/unit/
├── test_agent_schemas.py              ← NEW
└── test_planner_agent.py              ← NEW
docs/phase-done/
└── phase5-done.md                     ← NEW (dokumen ini)
```

---

## Catatan Penting

### Qwen3 8B Availability
- `SummaryAgent` dan `TopicAgent` menggunakan Qwen3 via Ollama
- Jika Ollama tidak tersedia → fallback ke template-based summary otomatis
- Pull model sekali: `docker compose exec ollama ollama pull qwen3:8b`

### Planner Mode
- Default: `use_llm_planner=false` (rule-based, ~0ms overhead, tidak perlu Qwen3)
- Aktifkan `use_llm_planner=true` untuk pertanyaan kompleks yang butuh nuansa

### Search Hybrid
- Semantic search butuh `posts.embedding` terisi (setelah Phase 4)
- Full-text search langsung dari `cleaned_content` (setelah Phase 3)
- Keduanya dapat digunakan tanpa satu sama lain

## Siap untuk Phase 6 — Report Service

Phase 6 akan menggunakan hasil Agent Service untuk:
- **PDF Report** — ringkasan eksekutif dengan grafik sentimen + trend
- **DOCX Report** — laporan rinci dengan tabel entities, contoh post
- **JSON Export** — data mentah untuk integrasi frontend/BI tools
- **Scheduled Reports** — laporan otomatis terjadwal via Celery Beat
