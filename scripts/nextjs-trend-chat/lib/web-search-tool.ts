/**
 * Tool pencarian web via SerpAPI (hasil Google Search asli) — untuk provider yang
 * TIDAK punya browsing bawaan (Ollama, OpenAI Chat Completions). AI memanggil tool
 * ini dengan sebuah query, kode di sini yang BENAR-BENAR hit SerpAPI dan
 * mengembalikan ringkasan hasil sebagai tool_result — AI sendiri tidak pernah
 * langsung mengakses internet.
 *
 * Signup: https://serpapi.com → salin API key ke env SERPAPI_API_KEY
 */

export const WEB_SEARCH_TOOL_NAME = "web_search";

export const WEB_SEARCH_TOOL_DESCRIPTION =
  "Cari informasi TERBARU di internet via Google Search. WAJIB panggil tool ini " +
  "dulu untuk menemukan topik yang benar-benar sedang viral/trending saat ini — " +
  "JANGAN mengarang topik tanpa hasil pencarian nyata. Boleh dipanggil berkali-kali " +
  "dengan query berbeda (mis. per platform: 'trending twitter indonesia hari ini', " +
  "'trending tiktok indonesia hari ini', 'berita viral indonesia hari ini').";

export const WEB_SEARCH_TOOL_PARAMETERS = {
  type: "object",
  properties: {
    query: { type: "string", description: "Kata kunci pencarian" },
  },
  required: ["query"],
} as const;

export interface WebSearchResultItem {
  title: string;
  link: string;
  snippet: string;
}

const SERPAPI_BASE_URL = "https://serpapi.com/search.json";

/** Eksekusi NYATA: GET ke SerpAPI. Dipanggil route/provider setelah AI minta tool ini. */
export async function executeWebSearch(
  query: string
): Promise<WebSearchResultItem[] | { error: string }> {
  const apiKey = process.env.SERPAPI_API_KEY;
  if (!apiKey) {
    return { error: "SERPAPI_API_KEY belum di-set di environment" };
  }

  const url = new URL(SERPAPI_BASE_URL);
  url.searchParams.set("engine", "google");
  url.searchParams.set("q", query);
  url.searchParams.set("api_key", apiKey);
  url.searchParams.set("num", "10");
  url.searchParams.set("hl", "id");
  url.searchParams.set("gl", "id");

  const res = await fetch(url.toString());
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    return { error: `SerpAPI HTTP ${res.status}: ${text.slice(0, 300)}` };
  }

  const data = (await res.json()) as {
    organic_results?: Array<{ title?: string; link?: string; snippet?: string }>;
    top_stories?: Array<{ title?: string; link?: string; source?: string }>;
    error?: string;
  };

  if (data.error) {
    return { error: data.error };
  }

  const fromNews = (data.top_stories ?? []).map((r) => ({
    title: r.title ?? "",
    link: r.link ?? "",
    snippet: r.source ? `Sumber: ${r.source}` : "",
  }));

  const fromOrganic = (data.organic_results ?? []).map((r) => ({
    title: r.title ?? "",
    link: r.link ?? "",
    snippet: r.snippet ?? "",
  }));

  return [...fromNews, ...fromOrganic].slice(0, 10);
}
