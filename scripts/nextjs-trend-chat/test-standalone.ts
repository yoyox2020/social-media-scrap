/**
 * Test end-to-end tanpa perlu project Next.js — jalankan langsung provider
 * function (runClaude/runOpenAI/runOllama) yang sama persis dipakai oleh
 * route.ts, lalu print event NDJSON-nya ke terminal secara live.
 *
 * Ini MEMANGGIL API sungguhan (Anthropic/OpenAI/Ollama) dan MENGIRIM POST
 * sungguhan ke backend trend-recommendations kalau AI memutuskan panggil tool.
 *
 * Jalankan:
 *   npx tsx test-standalone.ts claude "cari 10 topik trending soal starbucks hari ini dan submit ke trend-recommendations"
 *   npx tsx test-standalone.ts openai "..."
 *   npx tsx test-standalone.ts ollama "..."
 */
import { runClaude } from "./lib/providers/claude";
import { runOpenAI } from "./lib/providers/openai";
import { runOllama } from "./lib/providers/ollama";
import type { ChatStreamEvent } from "./lib/events";

const [, , providerArg, ...promptParts] = process.argv;
const provider = (providerArg ?? "claude") as "claude" | "openai" | "ollama";
const prompt = promptParts.join(" ");

if (!prompt) {
  console.error(
    'Usage: npx tsx test-standalone.ts <claude|openai|ollama> "<prompt>"\n' +
      'Contoh: npx tsx test-standalone.ts claude "cari 5 topik trending soal starbucks hari ini dan submit ke trend-recommendations"'
  );
  process.exit(1);
}

async function main() {
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      try {
        if (provider === "openai") await runOpenAI(prompt, controller);
        else if (provider === "ollama") await runOllama(prompt, controller);
        else await runClaude(prompt, controller);
      } catch (err) {
        console.error("[fatal]", err);
      } finally {
        controller.close();
      }
    },
  });

  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      if (!line.trim()) continue;
      const event = JSON.parse(line) as ChatStreamEvent;
      switch (event.type) {
        case "status":
          console.log(`[status] ${event.message}`);
          break;
        case "tool_result":
          console.log(`[tool_result:${event.tool}]`, JSON.stringify(event.result, null, 2));
          break;
        case "answer":
          console.log(`\n[answer]\n${event.text}`);
          break;
        case "error":
          console.error(`[error] ${event.message}`);
          break;
      }
    }
  }
}

main();
