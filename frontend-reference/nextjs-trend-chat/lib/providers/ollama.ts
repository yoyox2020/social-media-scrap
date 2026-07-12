/**
 * Provider: Ollama (model lokal, mis. qwen3:8b) — Ollama expose endpoint yang
 * kompatibel dengan OpenAI, jadi dipakai SDK `openai` yang sama, cuma beda
 * base_url. Ollama sendiri TIDAK punya akses internet — supaya tetap bisa cari
 * topik trending nyata, disediakan tool `web_search` (via SerpAPI) yang
 * dieksekusi oleh kode di sini, bukan oleh Ollama.
 *
 * npm install openai   (dipakai juga untuk provider ini, bukan cuma OpenAI asli)
 * env: OLLAMA_BASE_URL (default http://localhost:11434), OLLAMA_MODEL (default qwen3:8b)
 *      SERPAPI_API_KEY (untuk tool web_search — signup di https://serpapi.com)
 */
import OpenAI from "openai";
import {
  SUBMIT_TREND_TOOL_NAME,
  SUBMIT_TREND_TOOL_DESCRIPTION,
  SUBMIT_TREND_TOOL_PARAMETERS,
  executeSubmitTrendRecommendations,
} from "../submit-trend-tool";
import {
  WEB_SEARCH_TOOL_NAME,
  WEB_SEARCH_TOOL_DESCRIPTION,
  WEB_SEARCH_TOOL_PARAMETERS,
  executeWebSearch,
} from "../web-search-tool";
import { encodeEvent } from "../events";

const SYSTEM_PROMPT =
  "Kamu adalah AI trend-analyst. Kamu TIDAK punya akses internet bawaan, tapi punya " +
  "tool web_search yang bisa kamu panggil untuk mencari informasi nyata di internet " +
  "(dieksekusi oleh sistem, hasilnya dikembalikan ke kamu). Kalau user minta cari/submit " +
  "topik trending: WAJIB panggil web_search dulu (boleh beberapa kali dengan query " +
  "berbeda) untuk menemukan topik NYATA, baru panggil submit_trend_recommendations " +
  "dengan hasilnya. Jangan mengarang topik tanpa hasil pencarian.";

const MAX_ITERATIONS = 10;

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
        name: WEB_SEARCH_TOOL_NAME,
        description: WEB_SEARCH_TOOL_DESCRIPTION,
        parameters: WEB_SEARCH_TOOL_PARAMETERS as unknown as Record<string, unknown>,
      },
    },
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
      if (call.type !== "function") continue;

      if (call.function.name === WEB_SEARCH_TOOL_NAME) {
        const args = JSON.parse(call.function.arguments);
        controller.enqueue(encodeEvent({ type: "status", message: `Mencari: "${args.query}"...` }));
        const results = await executeWebSearch(args.query);
        messages = [
          ...messages,
          { role: "tool", tool_call_id: call.id, content: JSON.stringify(results) },
        ];
        continue;
      }

      if (call.function.name === SUBMIT_TREND_TOOL_NAME) {
        const args = JSON.parse(call.function.arguments);
        controller.enqueue(
          encodeEvent({
            type: "status",
            message: `Mengirim ${args?.items?.length ?? 0} topik ke trend-recommendations...`,
          })
        );
        const result = await executeSubmitTrendRecommendations(args);
        controller.enqueue(encodeEvent({ type: "tool_result", tool: SUBMIT_TREND_TOOL_NAME, result }));
        messages = [
          ...messages,
          { role: "tool", tool_call_id: call.id, content: JSON.stringify(result) },
        ];
      }
    }
  }

  controller.enqueue(encodeEvent({ type: "error", message: "Terlalu banyak iterasi, dihentikan." }));
}
