/**
 * Simulasi server-side dari alur user klik-klik di TopicSearchBox.tsx --
 * memanggil FUNGSI YANG SAMA PERSIS dari lib/smart-search-api.ts, cuma
 * dijalankan di terminal (bukan browser) supaya token tidak pernah masuk
 * ke bundle JS yang dilayani ke browser mana pun.
 *
 * Jalankan: DEMO_TOKEN=<token> npx tsx simulate-flow.ts
 * File ini HANYA utk verifikasi lokal -- jangan commit token apa pun.
 */
import { getTopicDetail, searchTopics } from "./lib/smart-search-api";

const TOKEN = process.env.DEMO_TOKEN;
if (!TOKEN) {
  console.error("Set env DEMO_TOKEN dulu.");
  process.exit(1);
}

async function main() {
  const topicName = "Simulasi Revisi UU Tipikor";
  const keywords = ["revisi uu tipikor"];

  console.log("=== Langkah 1: user klik 'Cari' (confirm_third_party TIDAK dikirim) ===");
  const first = await searchTopics(TOKEN!, {
    topics: [{ name: topicName, keywords }],
    platforms: ["news"],
    save_topic: true,
    confirm_third_party: false,
  });
  console.log("status:", first.data.status);
  console.log("needs_confirmation_keywords:", first.data.needs_confirmation_keywords);
  const topic = first.data.topics[0];
  console.log("topic_id:", topic.topic_id);

  if (first.data.needs_confirmation_keywords.length === 0) {
    console.log("Data sudah ada duluan, tidak ada yang perlu dikonfirmasi. Simulasi selesai.");
    return;
  }

  console.log("\n=== Langkah 2: user klik 'Ya, cari' (confirm_third_party: true, payload SAMA) ===");
  const confirmed = await searchTopics(TOKEN!, {
    topics: [{ name: topicName, keywords }],
    platforms: ["news"],
    save_topic: true,
    confirm_third_party: true,
  });
  console.log("status:", confirmed.data.status);
  console.log("queued_keywords:", confirmed.data.queued_keywords);
  console.log("note:", confirmed.data.note);

  if (confirmed.data.queued_keywords.length === 0) {
    console.log("Ternyata sudah ketemu duluan saat verifikasi ulang. Simulasi selesai.");
    return;
  }

  console.log("\n=== Langkah 3: polling GET /search/topics/{id} sampai hasil muncul ===");
  const topicId = topic.topic_id!;
  for (let i = 0; i < 15; i++) {
    await new Promise((r) => setTimeout(r, 8000));
    const detail = await getTopicDetail(TOKEN!, topicId);
    const kw = detail.data.keyword_details[0];
    console.log(
      `  poll #${i + 1} (t+${(i + 1) * 8}s): total_posts=${kw.total_posts}, last_rescanned_at=${kw.last_rescanned_at}`
    );
    if (kw.total_posts > 0) {
      console.log("\n=== SELESAI: data ditemukan ===");
      console.log(
        kw.posts.slice(0, 3).map((p) => `  [${p.platform}] ${p.title.slice(0, 70)}...`).join("\n")
      );
      return;
    }
  }
  console.log("Timeout polling (2 menit) -- proses background mungkin masih jalan.");
}

main().catch((err) => {
  console.error("SIMULASI GAGAL:", err);
  process.exit(1);
});
