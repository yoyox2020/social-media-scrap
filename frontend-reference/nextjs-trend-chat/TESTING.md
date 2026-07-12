# Cara Testing

Ada 3 level pengetesan, dari paling cepat/murah sampai paling lengkap. Kerjakan
berurutan — kalau level 1 gagal, level 2 dan 3 pasti gagal juga.

## Level 0 — install dependency (sekali saja)

Dari folder `scripts/nextjs-trend-chat/`:

```bash
cd scripts/nextjs-trend-chat
npm init -y
npm install @anthropic-ai/sdk openai
npm install -D tsx typescript @types/node
```

Set environment variable (Git Bash / PowerShell):

```bash
export TREND_API_BASE_URL=http://187.77.125.10:8000
export ANTHROPIC_API_KEY=sk-ant-xxxxx   # kalau mau test provider claude
export OPENAI_API_KEY=sk-xxxxx          # kalau mau test provider openai
export OLLAMA_BASE_URL=http://localhost:11434   # kalau mau test provider ollama
```

## Level 1 — test koneksi backend saja (tanpa AI, tanpa API key)

Paling cepat, tidak butuh API key sama sekali — cuma memverifikasi
`executeSubmitTrendRecommendations()` benar-benar bisa POST ke backend dan
skema payload-nya diterima:

```bash
npx tsx test-tool-only.ts
```

Output sukses:
```json
{
  "success": true,
  "data": { "created": ["Test Tool Calling 2026-07-04T..."], "updated": [], "evicted": [], "rejected": [] }
}
```

Kalau gagal di sini (`success: false` atau connection error), masalahnya di
`TREND_API_BASE_URL` / backend, **bukan** di kode tool-calling-nya — cek dulu
backend jalan (`curl http://187.77.125.10:8000/docs`) sebelum lanjut ke level 2.

## Level 2 — test end-to-end dengan AI (tanpa Next.js)

Jalankan provider function yang sama persis dipakai `route.ts`, langsung dari
terminal — ini benar-benar memanggil Claude/OpenAI/Ollama DAN mengirim POST
sungguhan ke backend kalau AI memutuskan panggil tool:

```bash
npx tsx test-standalone.ts claude "cari 5 topik trending soal starbucks hari ini dan submit ke trend-recommendations"
```

Output yang diharapkan (event NDJSON di-print live ke terminal):
```
[status] Mengirim 5 topik ke trend-recommendations...
[tool_result:submit_trend_recommendations] {
  "success": true,
  "data": { "created": ["Topik A", "Topik B", ...], "updated": [], "evicted": [], "rejected": [] }
}

[answer]
Sudah submit 5 topik trending soal Starbucks hari ini...
```

Coba juga provider lain (ingat: tidak ada web search, jadi hasilnya dari
pengetahuan training model, bukan real-time):

```bash
npx tsx test-standalone.ts openai "submit 3 topik trending random buat testing ke trend-recommendations"
npx tsx test-standalone.ts ollama "submit 3 topik trending random buat testing ke trend-recommendations"
```

**Verifikasi hasilnya benar-benar masuk DB** (bukan cuma AI bilang berhasil):

```bash
TOKEN=$(curl -s -X POST http://187.77.125.10:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"EMAIL","password":"PASSWORD"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['access_token'])")

curl -s "http://187.77.125.10:8000/api/v1/trend-recommendations" \
  -H "Authorization: Bearer $TOKEN"
```

## Level 3 — test lewat Next.js beneran (setelah dipasang ke project)

Setelah folder ini dipindah ke project Next.js asli (lihat `README.md`
§ "Cara pasang"), jalankan dev server lalu tembak endpoint-nya dengan `curl -N`
(`-N` supaya curl tidak buffer, jadi kelihatan stream-nya baris per baris
seperti di browser):

```bash
npm run dev   # di root project Next.js

# di terminal lain:
curl -N -X POST http://localhost:3000/api/trend-chat \
  -H "Content-Type: application/json" \
  -d '{"prompt": "cari 5 topik trending soal starbucks hari ini", "provider": "claude"}'
```

Kalau muncul baris-baris NDJSON (`{"type":"status",...}`, lalu
`{"type":"tool_result",...}`, lalu `{"type":"answer",...}`) secara bertahap
(bukan nunggu lama lalu muncul sekaligus), berarti streaming-nya jalan benar.

Terakhir, buka komponen `client-example.tsx` di browser dan pastikan progress-nya
kelihatan live di UI, bukan cuma di terminal.

## Troubleshooting cepat

| Gejala | Kemungkinan sebab |
|---|---|
| `test-tool-only.ts` gagal connection refused | Backend down atau `TREND_API_BASE_URL` salah |
| `test-tool-only.ts` sukses tapi `test-standalone.ts` error soal API key | `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` belum di-`export` di shell yang sama |
| Claude tidak pernah panggil tool, cuma jawab teks | Prompt-nya tidak eksplisit minta submit — coba tambahkan "...dan submit ke trend-recommendations" |
| `stop_reason: "refusal"` | Jarang terjadi untuk prompt semacam ini — coba prompt lain, atau cek `docs/setting-tool-calling.md` |
| Di Next.js: response langsung muncul sekaligus (bukan bertahap) | Cek `export const runtime = "nodejs"` ada, dan tidak ada proxy/CDN di depan yang mem-buffer response |
