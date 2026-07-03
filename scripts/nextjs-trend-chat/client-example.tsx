/**
 * Contoh pemakaian dari sisi frontend — komponen React yang kirim prompt user
 * ke /api/trend-chat lalu baca NDJSON stream-nya secara live (progress + hasil
 * eksekusi tool + jawaban akhir). Sesuaikan styling & path sesuai project kamu.
 */
"use client";

import { useState } from "react";
import type { ChatStreamEvent } from "./lib/events";

export function TrendChatBox() {
  const [prompt, setPrompt] = useState("");
  const [log, setLog] = useState<ChatStreamEvent[]>([]);
  const [busy, setBusy] = useState(false);

  async function handleSubmit() {
    if (!prompt.trim() || busy) return;
    setBusy(true);
    setLog([]);

    const res = await fetch("/api/trend-chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, provider: "claude" }),
    });

    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? ""; // baris terakhir mungkin belum lengkap

      for (const line of lines) {
        if (!line.trim()) continue;
        const event = JSON.parse(line) as ChatStreamEvent;
        setLog((prev) => [...prev, event]);
      }
    }

    setBusy(false);
  }

  return (
    <div>
      <input
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        placeholder="mis. cari 10 topik trending soal starbucks hari ini"
        disabled={busy}
      />
      <button onClick={handleSubmit} disabled={busy}>
        Kirim
      </button>

      <ul>
        {log.map((event, i) => (
          <li key={i}>
            {event.type === "status" && <em>{event.message}</em>}
            {event.type === "tool_result" && (
              <pre>{JSON.stringify(event.result, null, 2)}</pre>
            )}
            {event.type === "answer" && <p>{event.text}</p>}
            {event.type === "error" && <p style={{ color: "red" }}>{event.message}</p>}
          </li>
        ))}
      </ul>
    </div>
  );
}
