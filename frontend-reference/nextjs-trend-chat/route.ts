/**
 * Next.js App Router API route — taruh sebagai app/api/trend-chat/route.ts di
 * project Next.js kamu (sesuaikan import path lib/* setelah copy).
 *
 * Alur: user ketik prompt terkait "cari topik trending" di frontend -> POST ke
 * endpoint ini dengan { prompt, provider } -> AI (provider manapun) memutuskan
 * kapan panggil tool submit_trend_recommendations -> route ini yang BENAR-BENAR
 * mengeksekusi HTTP POST ke backend social-media-scrap -> progress + hasil
 * di-stream balik ke frontend sebagai NDJSON (satu JSON per baris).
 *
 * Kenapa provider-agnostic: tool (skema + eksekusi HTTP) didefinisikan sekali di
 * lib/submit-trend-tool.ts dan dipakai oleh ketiga provider — ganti/tambah provider
 * baru tidak perlu mengubah cara eksekusinya.
 */
import { NextRequest } from "next/server";
import { runClaude } from "./lib/providers/claude";
import { runOpenAI } from "./lib/providers/openai";
import { runOllama } from "./lib/providers/ollama";

// Wajib Node runtime (bukan Edge) — provider SDK & `fetch` ke backend internal
// butuh Node APIs penuh.
export const runtime = "nodejs";
// Web search + beberapa putaran tool call bisa makan waktu; naikkan kalau perlu
// (tergantung plan hosting — Vercel Hobby maks 60s, Pro bisa lebih).
export const maxDuration = 60;

type Provider = "claude" | "openai" | "ollama";

export async function POST(req: NextRequest) {
  let body: { prompt?: string; provider?: Provider };
  try {
    body = await req.json();
  } catch {
    return new Response(JSON.stringify({ error: "Body harus JSON" }), { status: 400 });
  }

  const { prompt, provider = "claude" } = body;
  if (!prompt || typeof prompt !== "string") {
    return new Response(JSON.stringify({ error: "prompt wajib diisi" }), { status: 400 });
  }

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      try {
        if (provider === "openai") await runOpenAI(prompt, controller);
        else if (provider === "ollama") await runOllama(prompt, controller);
        else await runClaude(prompt, controller);
      } catch (err) {
        controller.enqueue(
          new TextEncoder().encode(
            JSON.stringify({
              type: "error",
              message: err instanceof Error ? err.message : String(err),
            }) + "\n"
          )
        );
      } finally {
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: { "Content-Type": "application/x-ndjson; charset=utf-8" },
  });
}
