# Phase 6 — Report Service ✅

Tanggal: 2026-06-22

## Ringkasan

Report Service memungkinkan user men-generate laporan analisis sentimen dari keyword yang sudah diproses ke dalam tiga format: **JSON**, **PDF**, dan **DOCX**. Laporan berisi ringkasan post, distribusi sentimen, entitas terdeteksi, tren aktivitas, dan contoh post terbaru.

---

## File Baru

| File | Fungsi |
|------|--------|
| `app/services/reports/__init__.py` | Package init |
| `app/services/reports/schemas.py` | `GenerateReportRequest`, `ReportJobResponse`, `ReportData`, `SentimentData`, `EntityData`, `TrendData` |
| `app/services/reports/data_collector.py` | `ReportDataCollector` — query PostgreSQL untuk mengumpulkan semua data laporan |
| `app/services/reports/json_generator.py` | `JSONReportGenerator` — export ke file JSON |
| `app/services/reports/pdf_generator.py` | `PDFReportGenerator` — generate PDF dengan ReportLab (tabel, bar sentimen, section terstruktur) |
| `app/services/reports/docx_generator.py` | `DOCXReportGenerator` — generate DOCX dengan python-docx (heading, tabel, warna tema) |
| `app/services/reports/service.py` | `ReportService` — orchestrator (create_pending, generate, get_file_path) |
| `migrations/versions/005_reports_keyword_status.py` | Tambah `keyword_id` FK + kolom `status` ke tabel `reports` |
| `requirements-reports.txt` | Versi pin: reportlab, python-docx, Pillow |
| `tests/unit/test_report_schemas.py` | 8 tests untuk schemas |
| `tests/unit/test_json_generator.py` | 5 tests untuk JSON generator |

## File Diubah

| File | Perubahan |
|------|-----------|
| `app/domain/reports/models.py` | Tambah `keyword_id` (FK nullable ke keywords), `status` (default "pending") |
| `app/repositories/report_repository.py` | Full implementation: `get_by_id`, `list_by_project`, `list_by_keyword`, `create`, `update_status`, `update_after_generate`, `delete` |
| `app/workers/report_worker.py` | Full implementation: `generate_report_task` dengan asyncio.run wrapper, max_retries=3 |
| `app/api/v1/reports.py` | Full implementation: 5 endpoints |
| `app/shared/config.py` | Tambah `report_output_dir: str = "/app/reports"` + fungsi `get_settings()` |
| `pyproject.toml` | Tambah reportlab, python-docx, Pillow sebagai regular deps |
| `docker-compose.yml` | Tambah volume `reports_data`, mount ke api + worker, queue `reports` |
| `.env.example` | Tambah `REPORT_OUTPUT_DIR=/app/reports` |

---

## API Endpoints

| Method | Endpoint | Status Code | Fungsi |
|--------|----------|-------------|--------|
| `POST` | `/api/v1/reports/generate` | 202 | Trigger async via Celery, return job_id |
| `POST` | `/api/v1/reports/generate-sync` | 200 | Generate langsung, return hasil |
| `GET` | `/api/v1/reports/` | 200 | List laporan per project |
| `GET` | `/api/v1/reports/{report_id}` | 200 | Detail laporan + preview data JSON |
| `GET` | `/api/v1/reports/{report_id}/download` | 200 | Download file (PDF/DOCX/JSON) via FileResponse |
| `DELETE` | `/api/v1/reports/{report_id}` | 204 | Hapus record dari DB |

---

## Alur Generate Laporan

```
POST /reports/generate
        │
        ▼
ReportService.create_pending()   ← buat record DB status=pending
        │
        ▼
Celery: generate_report_task(report_id)
        │
        ▼
ReportService.generate()
  ├── update status → "generating"
  ├── ReportDataCollector.collect()
  │     ├── keyword info
  │     ├── post stats (total, processed, near-dupes, language)
  │     ├── sentiment distribution + examples
  │     ├── top entities by type
  │     ├── volume + sentiment trend per periode
  │     └── sample posts terbaru
  ├── Generator[format].generate(data, output_dir)
  │     ├── json  → JSONReportGenerator  → /app/reports/{id}.json
  │     ├── pdf   → PDFReportGenerator   → /app/reports/{id}.pdf
  │     └── docx  → DOCXReportGenerator  → /app/reports/{id}.docx
  └── update DB: file_path, summary, data JSON, status=done
        │
        ▼
GET /reports/{id}/download → FileResponse
```

---

## Isi Laporan

Setiap format (JSON/PDF/DOCX) berisi bagian yang sama:

1. **Meta** — keyword, tanggal generate, periode trend
2. **Ringkasan Post** — total, processed, near-duplicate, language breakdown
3. **Sentimen** — distribusi (positive/negative/neutral), persentase, dominan, contoh post per label
4. **Entitas** — top entitas per tipe (PERSON, ORG, LOCATION, PRODUCT, EVENT)
5. **Tren** — volume per periode (day/week/month), sentimen per periode, platform breakdown, arah (naik/turun/stabil)
6. **Sample Posts** — 5 post terbaru dengan label sentimen

---

## Status Lifecycle Report

```
pending → generating → done
                   └→ failed (retry up to 3x via Celery)
```

---

## Docker

- Volume baru: `reports_data:/app/reports` — di-share antara `api` dan `worker`
- Worker queue diperluas: `collector,processing,reports,celery`
- Env var: `REPORT_OUTPUT_DIR=/app/reports`

---

## Migration Chain

```
001 → 002 → 003 → 004 → 005
                         └── keyword_id FK + status column pada tabel reports
```

---

## Tests

```
70 passed (Phase 3–6 combined)

Phase 6 tambahan:
  test_report_schemas.py   — 8 tests
  test_json_generator.py   — 5 tests
```

---

## Phase 7 Selanjutnya

Phase 7 — Production Hardening:
- Structured logging (structlog JSON) + request ID middleware
- Distributed tracing (OpenTelemetry)
- Rate limiting (slowapi)
- Health check yang lebih detail (DB, Redis, Ollama status)
- Celery Beat untuk scheduled reports
