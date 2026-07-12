/**
 * Provider: Claude (Anthropic) — satu-satunya provider di sini yang punya
 * web_search bawaan (hosted server-side tool), jadi bisa benar-benar cari topik
 * trending nyata, bukan cuma dari pengetahuan training model.
 *
 * npm install @anthropic-ai/sdk
 * env: ANTHROPIC_API_KEY (atau login via `ant auth login` — SDK baca otomatis)
 */
import Anthropic from "@anthropic-ai/sdk";
import {
  SUBMIT_TREND_TOOL_NAME,
  SUBMIT_TREND_TOOL_DESCRIPTION,
  SUBMIT_TREND_TOOL_PARAMETERS,
  executeSubmitTrendRecommendations,
} from "../submit-trend-tool";
import { encodeEvent } from "../events";

const SYSTEM_PROMPT =
  "Kamu adalah AI trend-analyst. Kalau user minta cari topik trending, gunakan web_search " +
  "untuk menemukan topik NYATA (jangan mengarang), lalu panggil tool submit_trend_recommendations " +
  "dengan hasilnya. Tiap topic harus unik dalam satu payload. Kalau user cuma ngobrol biasa " +
  "(bukan minta cari/submit trending), jawab langsung tanpa memanggil tool.";

const MAX_ITERATIONS = 12;

export async function runClaude(
  prompt: string,
  controller: ReadableStreamDefaultController<Uint8Array>
): Promise<void> {
  const client = new Anthropic();

  // NOTE: `tools` ditulis longgar (any[]) karena nama tipe union persis di
  // @anthropic-ai/sdk (mis. Anthropic.Tool vs Anthropic.Messages.ToolUnion) bisa
  // beda antar versi SDK — biarkan TypeScript compiler di project kamu yang
  // memvalidasi begitu file ini di-copy & di-install dependency-nya.
  const tools: any[] = [
    // Server-side tool — Claude yang eksekusi pencarian sendiri, hasilnya otomatis
    // masuk ke response.content sebagai `web_search_tool_result`, tidak perlu
    // ditangani manual di sini. Ganti ke "web_search_20250305" kalau model/provider
    // yang dipakai tidak mendukung varian dynamic-filtering _20260209.
    { type: "web_search_20260209", name: "web_search", max_uses: 5 },
    {
      name: SUBMIT_TREND_TOOL_NAME,
      description: SUBMIT_TREND_TOOL_DESCRIPTION,
      input_schema: SUBMIT_TREND_TOOL_PARAMETERS,
    },
  ];

  let messages: Anthropic.MessageParam[] = [{ role: "user", content: prompt }];

  for (let i = 0; i < MAX_ITERATIONS; i++) {
    const response = await client.messages.create({
      model: "claude-opus-4-8",
      max_tokens: 8000,
      system: SYSTEM_PROMPT,
      thinking: { type: "adaptive" },
      tools,
      messages,
    });

    messages = [...messages, { role: "assistant", content: response.content }];

    if (response.stop_reason === "refusal") {
      controller.enqueue(
        encodeEvent({ type: "error", message: "Claude menolak permintaan ini (safety refusal)." })
      );
      return;
    }

    // web_search adalah server tool: kalau iterasi pencarian internalnya habis,
    // Claude mengembalikan stop_reason "pause_turn" — kirim ulang messages yang
    // SAMA (assistant response sudah ditambahkan di atas) tanpa user message baru,
    // Claude otomatis melanjutkan dari titik terakhir.
    if (response.stop_reason === "pause_turn") {
      controller.enqueue(encodeEvent({ type: "status", message: "Melanjutkan pencarian..." }));
      continue;
    }

    const customToolUses = response.content.filter(
      (b: any) => b.type === "tool_use" && b.name === SUBMIT_TREND_TOOL_NAME
    );

    if (customToolUses.length === 0) {
      const finalText = response.content.find((b: any) => b.type === "text") as any;
      controller.enqueue(encodeEvent({ type: "answer", text: finalText?.text ?? "" }));
      return;
    }

    const toolResults: any[] = [];
    for (const block of customToolUses as any[]) {
      controller.enqueue(
        encodeEvent({
          type: "status",
          message: `Mengirim ${block.input?.items?.length ?? 0} topik ke trend-recommendations...`,
        })
      );
      const result = await executeSubmitTrendRecommendations(block.input);
      controller.enqueue(encodeEvent({ type: "tool_result", tool: SUBMIT_TREND_TOOL_NAME, result }));
      toolResults.push({
        type: "tool_result",
        tool_use_id: block.id,
        content: JSON.stringify(result),
      });
    }

    messages = [...messages, { role: "user", content: toolResults }];
  }

  controller.enqueue(encodeEvent({ type: "error", message: "Terlalu banyak iterasi, dihentikan." }));
}
