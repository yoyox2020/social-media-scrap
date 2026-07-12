/**
 * Smoke test PALING CEPAT — tanpa AI sama sekali, tanpa API key.
 * Langsung panggil executeSubmitTrendRecommendations() dengan data dummy untuk
 * memverifikasi koneksi ke backend + skema payload benar, sebelum menguji
 * lapisan AI yang lebih mahal/lambat.
 *
 * Jalankan: npx tsx test-tool-only.ts
 */
import { executeSubmitTrendRecommendations } from "./lib/submit-trend-tool";

async function main() {
  const result = await executeSubmitTrendRecommendations({
    source: "test-tool-only",
    items: [
      {
        topic: `Test Tool Calling ${new Date().toISOString()}`,
        score: 0.5,
        related_accounts: [{ platform: "twitter", username: "test_account" }],
      },
    ],
  });

  console.log(JSON.stringify(result, null, 2));

  if (!result.success) {
    console.error("\nGagal — cek TREND_API_BASE_URL & apakah backend bisa diakses.");
    process.exit(1);
  }
  console.log("\nOK — backend menerima payload. Cek 'created' di atas.");
}

main();
