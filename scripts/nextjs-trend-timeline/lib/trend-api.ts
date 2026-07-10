// Tipe + fetch helper untuk GET /trend-discovery/timeline.
// Lihat docs/trend-discovery-api.md (di repo backend) untuk spesifikasi lengkap.

export interface TimelineBucket {
  bucket: string; // ISO datetime, mis. "2026-07-07T00:00:00+00:00"
  count: number;
}

export interface TimelineSeries {
  total_mentions: number;
  total: TimelineBucket[];
  by_platform?: Record<string, TimelineBucket[]>;
}

export interface TimelineResponse {
  mode: "auto_discover" | "manual_keywords";
  date_from: string;
  date_to: string;
  since: string;
  until: string;
  interval: "hour" | "day";
  platform: string;
  keywords: string[];
  series: Record<string, TimelineSeries>;
}

export interface FetchTimelineParams {
  dateFrom: string;
  dateTo: string;
  topN?: number;
  interval?: "hour" | "day";
  platform?: string;
  /** Diisi hanya kalau mau OVERRIDE manual ke kata/frasa tertentu.
   *  Kosongkan (default) supaya API auto-discover sendiri kata paling
   *  sering disebut di rentang tanggal ini -- itu perilaku yang dipakai
   *  di page.tsx contoh ini. */
  keywords?: string[];
  includePlatformBreakdown?: boolean;
}

export async function fetchTrendTimeline(params: FetchTimelineParams): Promise<TimelineResponse> {
  const token = process.env.TREND_API_TOKEN;
  if (!token) {
    throw new Error("TREND_API_TOKEN belum di-set di .env.local");
  }

  const search = new URLSearchParams({
    date_from: params.dateFrom,
    date_to: params.dateTo,
    interval: params.interval ?? "day",
  });
  if (params.topN) search.set("top_n", String(params.topN));
  if (params.platform) search.set("platform", params.platform);
  if (params.includePlatformBreakdown) search.set("include_platform_breakdown", "true");
  if (params.keywords?.length) {
    // SENGAJA cuma di-set kalau ada isinya -- kalau param ini tidak pernah
    // dikirim sama sekali, API otomatis masuk mode auto-discover.
    search.set("keywords", params.keywords.join(","));
  }

  const baseUrl = process.env.TREND_API_BASE_URL ?? "https://api.dismi.xyz";
  const res = await fetch(`${baseUrl}/api/v1/trend-discovery/timeline?${search.toString()}`, {
    headers: { Authorization: `Bearer ${token}` },
    // ISR: data trending tidak perlu real-time detik-ke-detik, cache 5 menit
    // supaya tidak membebani DB tiap kali halaman dibuka.
    next: { revalidate: 300 },
  });

  if (!res.ok) {
    const body = await res.text();
    throw new Error(`fetchTrendTimeline gagal (${res.status}): ${body}`);
  }

  const json = await res.json();
  return json.data as TimelineResponse;
}
