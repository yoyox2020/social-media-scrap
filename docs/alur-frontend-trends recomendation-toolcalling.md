User ketik prompt di frontend Next.js
        │
        ▼
Frontend pakai API key AI-nya SENDIRI            ← API key #1
(ANTHROPIC_API_KEY / OPENAI_API_KEY, disimpan     (punya frontend,
di .env.local project Next.js — BEDA dengan        beda dari backend)
API key yang tadi di server backend)
        │
        ▼
Claude/OpenAI memutuskan panggil tool
submit_trend_recommendations
        │
        ▼
Route handler Next.js (server-side) eksekusi:
POST http://187.77.125.10:8000/api/v1/trend-recommendations
        │
        ▼
Endpoint ini PUBLIK — TIDAK BUTUH API key/token apapun
(sengaja dibuat begitu, biar sistem AI eksternal
manapun bisa langsung submit)
        │
        ▼
Data masuk ke trend_recommendations (status=pending)




[Subsistem A - BARU, kemarin] AI Viral Discovery (Claude)
   → cari topik viral → submit_recommendations() → trend_recommendations (pending)
   → SAAT INI GAGAL karena saldo Anthropic habis

[Subsistem B - LAMA, tidak diubah] Daily scrape worker (09:00 WIB)
   → ambil trend_recommendations yang pending → scrape via Apify (dgn fallback EnsembleData)
   → JALAN NORMAL, terbukti sukses di screenshot


   