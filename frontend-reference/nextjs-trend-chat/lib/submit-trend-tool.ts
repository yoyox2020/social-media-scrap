/**
 * Skema tool yang SAMA dipakai oleh semua provider AI (Claude, OpenAI, Ollama, atau
 * AI lain apapun) — provider hanya perlu bisa "function calling" dengan JSON schema
 * generik ini. Yang benar-benar mengeksekusi HTTP request adalah kode di sini,
 * BUKAN AI-nya — jadi tool ini portable ke provider mana pun.
 *
 * Kontrak ini mengikuti docs/trend-recommendations.md di repo backend
 * (social-media-scrap) — POST /api/v1/trend-recommendations, publik tanpa auth.
 */

export const SUBMIT_TREND_TOOL_NAME = "submit_trend_recommendations";

export const SUBMIT_TREND_TOOL_DESCRIPTION =
  "Submit daftar topik viral (maks 20/hari) ke tabel trend_recommendations. " +
  "Setiap topik butuh: topic (nama isu, harus unik), score (0.0-1.0, seberapa viral), " +
  "related_accounts (list akun sosial media terkait, per item: platform + username). " +
  "Panggil ini SETELAH benar-benar menemukan topik nyata (via web search/browsing), " +
  "jangan mengarang data.";

// JSON Schema polos — dipakai baik sebagai `input_schema` (Claude) maupun
// `parameters` (OpenAI/Ollama function calling). Format ini disengaja generik.
export const SUBMIT_TREND_TOOL_PARAMETERS = {
  type: "object",
  properties: {
    items: {
      type: "array",
      description: "Daftar topik viral, tiap topik object dengan topic/score/related_accounts",
      items: {
        type: "object",
        properties: {
          topic: { type: "string" },
          score: { type: "number", minimum: 0, maximum: 1 },
          related_accounts: {
            type: "array",
            items: {
              type: "object",
              properties: {
                platform: { type: "string" },
                username: { type: "string" },
              },
              required: ["platform", "username"],
            },
          },
        },
        required: ["topic", "score", "related_accounts"],
      },
    },
    source: {
      type: "string",
      description: "Nama AI/sistem yang submit, default 'external_ai'",
    },
    recommendation_date: {
      type: "string",
      description: "Format YYYY-MM-DD, opsional (default hari ini)",
    },
  },
  required: ["items"],
} as const;

export interface SubmitTrendInput {
  items: Array<{
    topic: string;
    score: number;
    related_accounts: Array<{ platform: string; username: string }>;
  }>;
  source?: string;
  recommendation_date?: string;
}

export interface SubmitTrendResult {
  success: boolean;
  data?: {
    created: string[];
    updated: string[];
    evicted: string[];
    rejected: string[];
  };
  error?: string;
}

const TREND_API_BASE_URL = process.env.TREND_API_BASE_URL ?? "http://187.77.125.10:8000";

/**
 * Eksekusi NYATA: POST ke /api/v1/trend-recommendations (publik, tanpa auth).
 * Dipanggil oleh route handler setelah AI (provider apapun) meminta tool ini —
 * AI tidak pernah mengirim HTTP request-nya sendiri.
 */
export async function executeSubmitTrendRecommendations(
  input: SubmitTrendInput
): Promise<SubmitTrendResult> {
  const body = {
    items: input.items,
    source: input.source ?? "external_ai",
    ...(input.recommendation_date ? { recommendation_date: input.recommendation_date } : {}),
  };

  const res = await fetch(`${TREND_API_BASE_URL}/api/v1/trend-recommendations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    return { success: false, error: `HTTP ${res.status}: ${text.slice(0, 300)}` };
  }

  return (await res.json()) as SubmitTrendResult;
}
