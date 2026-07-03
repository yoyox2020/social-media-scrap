# Trend Chat — Tool Calling di Next.js (multi-provider)

Referensi implementasi "AI yang benar-benar mengeksekusi HTTP request", bukan
cuma kasih teks — versi Next.js dari `scripts/ai_trend_submit.py`. User ketik
prompt terkait pencarian trending di frontend, AI (Claude/OpenAI/Ollama, atau
provider lain yang kamu tambahkan) memutuskan kapan panggil tool
`submit_trend_recommendations`, dan **route handler ini** (bukan AI-nya) yang
benar-benar mengirim `POST /api/v1/trend-recommendations` ke backend
social-media-scrap.

## Kenapa provider-agnostic

Tool (skema JSON + fungsi eksekusi HTTP) didefinisikan sekali di
`lib/submit-trend-tool.ts` dan dipakai oleh ketiga provider di `lib/providers/`.
Kalau besok mau ganti/tambah provider AI lain, cukup buat file provider baru
yang memanggil `executeSubmitTrendRecommendations()` yang sama — tidak perlu
ubah cara eksekusinya.

Hanya **Claude** yang punya `web_search` bawaan (hosted tool) sehingga bisa
benar-benar mencari topik trending nyata. OpenAI (Chat Completions) dan Ollama
di sini murni function calling tanpa browsing — kalau prompt minta data
real-time, tambahkan tool search eksternal sendiri (SerpAPI/Bing) di daftar
`tools` pada `lib/providers/openai.ts` / `lib/providers/ollama.ts`.

## Cara pasang ke project Next.js kamu

1. Copy folder ini ke project Next.js:
   - `route.ts` → `app/api/trend-chat/route.ts`
   - `lib/` → `app/api/trend-chat/lib/` (atau lokasi lain, sesuaikan import)
   - `client-example.tsx` → contoh, sesuaikan lalu taruh sebagai komponen client kamu

2. Install dependency sesuai provider yang dipakai:

   ```bash
   npm install @anthropic-ai/sdk   # untuk provider claude
   npm install openai              # untuk provider openai DAN ollama (endpoint kompatibel OpenAI)
   ```

3. Set environment variables (`.env.local`):

   ```bash
   ANTHROPIC_API_KEY=sk-ant-...       # kalau pakai provider claude
   OPENAI_API_KEY=sk-...              # kalau pakai provider openai
   OLLAMA_BASE_URL=http://localhost:11434   # kalau pakai provider ollama
   TREND_API_BASE_URL=http://187.77.125.10:8000   # backend social-media-scrap
   ```

4. Panggil dari frontend:

   ```ts
   const res = await fetch("/api/trend-chat", {
     method: "POST",
     headers: { "Content-Type": "application/json" },
     body: JSON.stringify({
       prompt: "cari 10 topik trending soal starbucks hari ini",
       provider: "claude", // atau "openai" / "ollama"
     }),
   });
   // res.body adalah NDJSON stream — lihat client-example.tsx untuk cara baca live
   ```

## Format event NDJSON (satu JSON per baris)

| `type` | Kapan muncul |
|---|---|
| `status` | Progress teks, mis. "Melanjutkan pencarian...", "Mengirim 5 topik..." |
| `tool_result` | Setelah `submit_trend_recommendations` benar-benar dieksekusi — isinya `{created, updated, evicted, rejected}` dari backend |
| `answer` | Jawaban akhir AI setelah selesai (tidak ada tool call lagi) |
| `error` | Refusal, HTTP error dari backend, atau iterasi terlalu banyak |

## Catatan penting

- **Runtime harus Node, bukan Edge** (`export const runtime = "nodejs"`) — SDK
  provider dan `fetch` ke backend internal butuh Node APIs penuh.
- **`maxDuration`** dinaikkan ke 60 detik karena web search + beberapa putaran
  tool call bisa makan waktu. Sesuaikan dengan plan hosting kamu (Vercel Hobby
  maks 60s).
- Endpoint backend `POST /api/v1/trend-recommendations` **publik tanpa auth**
  (lihat `docs/trend-recommendations.md`) — jangan expose `TREND_API_BASE_URL`
  ke client-side, panggil hanya dari server (route handler ini sudah benar,
  jangan pindahkan `executeSubmitTrendRecommendations` ke client component).
- Tipe `tools` di provider Claude ditulis longgar (`any[]`) karena nama tipe
  union persis di `@anthropic-ai/sdk` bisa beda antar versi — biarkan
  TypeScript compiler di project kamu yang validasi setelah `npm install`.
