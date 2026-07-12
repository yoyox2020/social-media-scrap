/**
 * Event NDJSON (satu JSON per baris) yang di-stream dari route handler ke frontend,
 * supaya UI bisa tampilkan progress ("mencari...", "mengirim N topik...") sebelum
 * jawaban akhir muncul — bukan cuma menunggu satu response besar di akhir.
 */
export type ChatStreamEvent =
  | { type: "status"; message: string }
  | { type: "tool_result"; tool: string; result: unknown }
  | { type: "answer"; text: string }
  | { type: "error"; message: string };

export function encodeEvent(event: ChatStreamEvent): Uint8Array {
  return new TextEncoder().encode(JSON.stringify(event) + "\n");
}
