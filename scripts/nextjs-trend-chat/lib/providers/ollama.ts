/**
 * Provider: Ollama (model lokal, mis. qwen3:8b) — Ollama expose endpoint yang
 * kompatibel dengan OpenAI, jadi dipakai SDK `openai` yang sama, cuma beda
 * base_url. TIDAK ADA akses internet sama sekali — kalau butuh data trending
 * real-time, tambahkan tool search eksternal sendiri di `tools` di bawah.
 *
 * npm install openai   (dipakai juga untuk provider ini, bukan cuma OpenAI asli)
 * env: OLLAMA_BASE_URL (default http://localhost:11434), OLLAMA_MODEL (default qwen3:8b)
 */
import OpenAI from "openai";
import {
  SUBMIT_TREND_TOOL_NAME,
  SUBMIT_TREND_TOOL_DESCRIPTION,
  SUBMIT_TREND_TOOL_PARAMETERS,
  executeSubmitTrendRecommendations,
} from "../submit-trend-tool";
import { encodeEvent } from "../events";

const SYSTEM_PROMPT =
  "Kamu adalah AI trend-analyst. Kalau user minta submit topik trending, panggil tool " +
  "submit_trend_recommendations dengan hasilnya. CATATAN: kamu TIDAK punya akses internet — " +
  "kalau ditanya topik 'trending hari ini', jawab dari pengetahuanmu tapi beri tahu user ini " +
  "bukan data real-time.";

const MAX_ITERATIONS = 8;

export async function runOllama(
  prompt: string,
  controller: ReadableStreamDefaultController<Uint8Array>
): Promise<void> {
  const baseURL = `${process.env.OLLAMA_BASE_URL ?? "http://localhost:11434"}/v1`;
  const model = process.env.OLLAMA_MODEL ?? "qwen3:8b";
  const client = new OpenAI({ baseURL, apiKey: "ollama" }); // apiKey diabaikan Ollama, tapi wajib diisi

  const tools: OpenAI.Chat.ChatCompletionTool[] = [
    {
      type: "function",
      function: {
        name: SUBMIT_TREND_TOOL_NAME,
        description: SUBMIT_TREND_TOOL_DESCRIPTION,
        parameters: SUBMIT_TREND_TOOL_PARAMETERS as unknown as Record<string, unknown>,
      },
    },
  ];

  let messages: OpenAI.Chat.ChatCompletionMessageParam[] = [
    { role: "system", content: SYSTEM_PROMPT },
    { role: "user", content: prompt },
  ];

  for (let i = 0; i < MAX_ITERATIONS; i++) {
    const response = await client.chat.completions.create({ model, messages, tools });

    const msg = response.choices[0].message;
    messages = [...messages, msg];

    if (!msg.tool_calls || msg.tool_calls.length === 0) {
      controller.enqueue(encodeEvent({ type: "answer", text: msg.content ?? "" }));
      return;
    }

    for (const call of msg.tool_calls) {
      if (call.type !== "function" || call.function.name !== SUBMIT_TREND_TOOL_NAME) continue;

      const args = JSON.parse(call.function.arguments);
      controller.enqueue(
        encodeEvent({
          type: "status",
          message: `Mengirim ${args?.items?.length ?? 0} topik ke trend-recommendations...`,
        })
      );
      const result = await executeSubmitTrendRecommendations(args);
      controller.enqueue(encodeEvent({ type: "tool_result", tool: SUBMIT_TREND_TOOL_NAME, result }));
      messages = [...messages, { role: "tool", tool_call_id: call.id, content: JSON.stringify(result) }];
    }
  }

  controller.enqueue(encodeEvent({ type: "error", message: "Terlalu banyak iterasi, dihentikan." }));
}
