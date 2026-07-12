/**
 * Client API untuk Smart Search (POST /search/topics dan
 * POST /search/topics/{id}/search) -- termasuk alur konfirmasi+verifikasi
 * tier-3 (lihat README.md di folder ini utk penjelasan alurnya).
 *
 * Tipe di sini dicocokkan LANGSUNG ke response backend
 * (app/api/v1/topic_search.py), bukan tebakan -- kalau backend berubah
 * bentuk response-nya, tipe ini juga harus ikut disesuaikan.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "https://api.dismi.xyz/api/v1";

export interface SentimentSummary {
  total_analyzed: number;
  positif?: { count: number; pct: number };
  negatif?: { count: number; pct: number };
  netral?: { count: number; pct: number };
  dominant?: string;
}

export interface PostItem {
  id: string;
  platform: string;
  title: string; // isi konten post (nama field "title" apa adanya dari backend)
  author: string | null;
  url: string | null;
  view_count: number;
  likes: number;
  published_at: string | null;
  collected_at: string | null;
  thumbnail_url: string;
}

/** Status per-keyword yang mungkin muncul di status_per_keyword */
export type KeywordStatus = "found" | "empty" | "needs_confirmation" | "queued";

/** Status keseluruhan satu topik/request */
export type OverallStatus =
  | "ready"
  | "needs_confirmation"
  | "partial_needs_confirmation"
  | "queued"
  | "partial";

export interface TopicSearchInput {
  name: string;
  keywords: string[];
  description?: string;
}

export interface TopicSearchRequest {
  topics: TopicSearchInput[];
  /** Kosong/tidak dikirim = otomatis SEMUA platform terdaftar */
  platforms?: string[];
  limit_per_keyword?: number;
  include_sentiment?: boolean;
  auto_crawl?: boolean;
  /** WAJIB true baru tier-3 (Apify/Firecrawl/YouTube API) benar-benar dipanggil */
  confirm_third_party?: boolean;
  save_topic?: boolean;
  enable_recurring?: boolean;
  schedule_duration_days?: number;
}

export interface TopicResult {
  topic_id: string | null;
  topic: string;
  keywords: string[];
  total_posts: number;
  status_per_keyword: Record<string, KeywordStatus>;
  sentiment_per_keyword: Record<string, SentimentSummary | undefined>;
  results: PostItem[];
  queued: string[];
  needs_confirmation: string[];
}

export interface TopicSearchResponse {
  success: boolean;
  data: {
    status: OverallStatus;
    platforms: string[];
    total_topics: number;
    queued_keywords: string[];
    needs_confirmation_keywords: string[];
    note: string | null;
    topics: TopicResult[];
  };
}

export interface SavedTopicSearchResponse {
  success: boolean;
  data: {
    topic_id: string;
    topic: string;
    platforms: string[];
    status: OverallStatus;
    total_posts: number;
    status_per_keyword: Record<string, KeywordStatus>;
    sentiment_per_keyword: Record<string, SentimentSummary | undefined>;
    results: PostItem[];
    queued_keywords: string[];
    needs_confirmation_keywords: string[];
    note: string | null;
  };
}

export interface TopicDetailResponse {
  success: boolean;
  data: {
    topic_id: string;
    name: string;
    description: string | null;
    platforms: string[];
    total_keywords: number;
    total_posts: number;
    keyword_details: {
      keyword: string;
      keyword_id: string;
      total_posts: number;
      posts: PostItem[];
      last_rescanned_at: string | null;
      sentiment?: SentimentSummary;
    }[];
    auto_crawl: boolean;
    schedule_recurring: boolean;
    schedule_duration_days: number | null;
    schedule_started_at: string | null;
    schedule_expires_at: string | null;
    created_at: string;
    updated_at: string;
  };
}

function authHeaders(token: string): HeadersInit {
  return {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  };
}

/**
 * Langkah 1 & 2 (verifikasi pertama + kirim ulang setelah user konfirmasi)
 * pakai fungsi YANG SAMA ini -- bedanya cuma nilai `confirm_third_party`.
 * Body yang dikirim HARUS identik (topics/platforms sama persis) antara
 * panggilan pertama (confirm_third_party: false/omit) dan panggilan kedua
 * (confirm_third_party: true), karena backend mencocokkan ulang berdasarkan
 * isi request itu, bukan menyimpan state di sisi server antar-request.
 */
export async function searchTopics(
  token: string,
  body: TopicSearchRequest
): Promise<TopicSearchResponse> {
  const res = await fetch(`${API_BASE}/search/topics`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`searchTopics gagal: HTTP ${res.status}`);
  return res.json();
}

/** Cari ulang topik yang SUDAH tersimpan, cukup pakai topic_id (dropdown + tombol Search). */
export async function searchSavedTopic(
  token: string,
  topicId: string,
  opts: { confirm_third_party?: boolean; limit_per_keyword?: number; include_sentiment?: boolean } = {}
): Promise<SavedTopicSearchResponse> {
  const res = await fetch(`${API_BASE}/search/topics/${topicId}/search`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(opts),
  });
  if (!res.ok) throw new Error(`searchSavedTopic gagal: HTTP ${res.status}`);
  return res.json();
}

/** Ambil detail topik terkini -- dipakai utk POLLING setelah status "queued". */
export async function getTopicDetail(token: string, topicId: string): Promise<TopicDetailResponse> {
  const res = await fetch(`${API_BASE}/search/topics/${topicId}`, {
    headers: authHeaders(token),
  });
  if (!res.ok) throw new Error(`getTopicDetail gagal: HTTP ${res.status}`);
  return res.json();
}
