# Setting Tool Calling — Frontend Next.js (Trend Chat)

Panduan pemasangan & penggunaan fitur tool-calling di frontend Next.js: user
ketik prompt terkait pencarian trending, AI (Claude/OpenAI/Ollama — provider
apapun) otomatis mengeksekusi `POST /api/v1/trend-recommendations`, bukan
sekadar kasih teks yang harus di-copas manual.

Kode referensinya ada di [scripts/nextjs-trend-chat/](../scripts/nextjs-trend-chat/)
di repo backend ini. Lihat juga [trend-recommendations.md](trend-recommendations.md)
untuk detail API/tabel yang jadi tujuan tool ini.

---

## 1. Copy file ke project Next.js kamu

Dari `scripts/nextjs-trend-chat/`, pindahkan ke project Next.js (misal `frontend/`)
dengan struktur berikut (import path di `route.ts` sudah otomatis benar kalau
strukturnya persis begini):

```
frontend/
  app/api/trend-chat/
    route.ts                      ← dari scripts/nextjs-trend-chat/route.ts
    lib/
      submit-trend-tool.ts
      events.ts
      providers/
        claude.ts
        openai.ts
        ollama.ts
  # opsional, contoh komponen chat:
  # dari scripts/nextjs-trend-chat/client-example.tsx
```

## 2. Install dependency

Di dalam project Next.js:

```bash
npm install @anthropic-ai/sdk   # kalau mau pakai Claude
npm install openai              # kalau mau pakai OpenAI dan/atau Ollama
```

## 3. Isi environment variable

Bikin `.env.local` di root project Next.js:

```bash
ANTHROPIC_API_KEY=sk-ant-xxxxx
TREND_API_BASE_URL=http://187.77.125.10:8000

# opsional, hanya kalau mau pakai provider itu
OPENAI_API_KEY=sk-xxxxx
OLLAMA_BASE_URL=http://localhost:11434
```

## 4. Jalankan dev server

```bash
npm run dev
```

## 5. Pakai dari frontend (chat box)

Taruh komponen `client-example.tsx` di suatu halaman, atau `fetch` manual:

```ts
const res = await fetch("/api/trend-chat", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    prompt: "cari 10 topik trending soal starbucks hari ini",
    provider: "claude", // atau "openai" / "ollama"
  }),
});
```

`res.body` adalah stream NDJSON (satu event JSON per baris) — baca live-nya
sesuai contoh di `client-example.tsx`.

---

## Apa yang terjadi di balik layar

1. User ketik prompt apapun terkait pencarian trending → dikirim ke
   `/api/trend-chat`.
2. Claude memutuskan sendiri untuk pakai `web_search` mencari topik nyata
   (OpenAI/Ollama tidak punya browsing bawaan — lihat catatan di bawah).
3. Begitu ketemu, Claude memanggil tool `submit_trend_recommendations` —
   **route handler (server) yang benar-benar mengeksekusi**
   `POST http://187.77.125.10:8000/api/v1/trend-recommendations`, bukan AI-nya
   langsung.
4. Progress di-stream balik ke frontend baris demi baris (NDJSON), sehingga UI
   bisa tampil bertahap:
   - `status` → "Melanjutkan pencarian...", "Mengirim 5 topik ke trend-recommendations..."
   - `tool_result` → hasil asli dari backend: `{created, updated, evicted, rejected}`
   - `answer` → jawaban akhir AI
   - `error` → refusal, HTTP error dari backend, atau iterasi terlalu banyak
5. Verifikasi hasilnya benar-benar masuk ke DB via `GET /api/v1/trend-recommendations`
   (butuh token) atau query langsung ke Postgres di server — lihat
   [trend-recommendations.md](trend-recommendations.md) § "Cek langsung di database".

## Ganti provider

Ubah `provider: "claude"` jadi `"openai"` atau `"ollama"` di body request.
Catatan: kedua provider itu **tidak bisa browsing** (tidak ada `web_search`) —
topik trending yang "ditemukan" cuma dari pengetahuan training model, bukan
data real-time. Kalau tetap ingin pakai provider itu untuk data real-time,
tambahkan tool search eksternal sendiri (SerpAPI/Bing) di daftar `tools` pada
`lib/providers/openai.ts` / `lib/providers/ollama.ts`.

## Catatan keamanan

- Endpoint backend `POST /api/v1/trend-recommendations` **publik tanpa auth**
  by design — jangan panggil `executeSubmitTrendRecommendations()` dari client
  component, hanya dari server (route handler ini sudah benar).
- Route handler wajib `runtime = "nodejs"` (bukan Edge) karena SDK provider dan
  `fetch` ke backend internal butuh Node APIs penuh.

## Status

Belum dipasang ke project Next.js nyata — file di `scripts/nextjs-trend-chat/`
masih berupa referensi/scaffold di repo backend ini (lokasi frontend belum
diberikan). Kalau sudah ada path project Next.js-nya, file bisa dipindah/adaptasi
langsung ke sana.
