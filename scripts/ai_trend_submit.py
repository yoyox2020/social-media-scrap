"""
Contoh: biarkan AI (Claude / OpenAI / Ollama) langsung EKSEKUSI
POST /api/v1/trend-recommendations dari satu prompt user, pakai tool-calling.

Alur:
    User ketik prompt (mis. "cari 10 topik trending soal starbucks")
      -> dikirim ke LLM bersama definisi tool `submit_trend_recommendations`
      -> LLM putuskan kapan & dengan parameter apa tool itu dipanggil
      -> script ini yang BENAR-BENAR mengeksekusi HTTP POST ke API publik
      -> hasil (created/updated/evicted/rejected) dikembalikan ke LLM sebagai
         tool result, LLM kasih ringkasan akhir ke user

Requires (install sesuai provider yang dipakai):
    pip install anthropic      # untuk --provider claude
    pip install openai         # untuk --provider openai (juga dipakai untuk --provider ollama,
                                # karena Ollama expose endpoint kompatibel OpenAI)

Environment variables:
    ANTHROPIC_API_KEY   — untuk --provider claude
    OPENAI_API_KEY      — untuk --provider openai
    OLLAMA_BASE_URL     — untuk --provider ollama (default: http://localhost:11434)

Cara pakai:
    python scripts/ai_trend_submit.py --provider claude  --prompt "cari 10 topik trending soal starbucks hari ini dan submit ke trend-recommendations"
    python scripts/ai_trend_submit.py --provider openai  --prompt "..."
    python scripts/ai_trend_submit.py --provider ollama  --model qwen3:8b --prompt "..."

PENTING soal Ollama (model lokal):
    Model lokal seperti qwen3:8b TIDAK punya akses internet/web search bawaan
    seperti Claude/OpenAI hosted tools. Kalau prompt minta "cari yang lagi
    trending hari ini", Ollama cuma bisa jawab dari pengetahuan training-nya
    (bisa basi/tidak akurat) kecuali kamu tambahkan tool pencarian sendiri
    (mis. panggil SerpAPI/Bing API) dan daftarkan sebagai tool tambahan di
    TOOLS_OLLAMA di bawah.
"""
from __future__ import annotations

import argparse
import json
import os

import requests

API_BASE_URL = os.environ.get("TREND_API_BASE_URL", "http://187.77.125.10:8000")


# ─────────────────────────────────────────────────────────────────────────────
# Tool yang benar-benar dieksekusi (bukan cuma "diomongkan" oleh LLM)
# ─────────────────────────────────────────────────────────────────────────────

def submit_trend_recommendations(items: list[dict], source: str = "external_ai", recommendation_date: str | None = None) -> dict:
    """Eksekusi nyata: POST ke /api/v1/trend-recommendations (publik, tanpa auth)."""
    body = {"items": items, "source": source}
    if recommendation_date:
        body["recommendation_date"] = recommendation_date

    r = requests.post(f"{API_BASE_URL}/api/v1/trend-recommendations", json=body, timeout=30)
    r.raise_for_status()
    return r.json()


TOOL_DESCRIPTION = (
    "Submit daftar topik viral (maks 20/hari) ke tabel trend_recommendations. "
    "Setiap topik butuh: topic (nama isu, harus unik), score (0.0-1.0, seberapa viral), "
    "related_accounts (list akun sosial media yang terkait, per item: platform + username). "
    "Panggil ini SETELAH kamu benar-benar menemukan topik nyata (via web search/browsing), "
    "jangan mengarang data."
)

TOOL_PARAMETERS_JSONSCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "description": "Daftar topik viral, tiap topik object dengan topic/score/related_accounts",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "score": {"type": "number", "minimum": 0, "maximum": 1},
                    "related_accounts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "platform": {"type": "string"},
                                "username": {"type": "string"},
                            },
                            "required": ["platform", "username"],
                        },
                    },
                },
                "required": ["topic", "score", "related_accounts"],
            },
        },
        "source": {"type": "string", "description": "Nama AI/sistem yang submit, default 'external_ai'"},
        "recommendation_date": {"type": "string", "description": "Format YYYY-MM-DD, opsional (default hari ini)"},
    },
    "required": ["items"],
}

SYSTEM_PROMPT = (
    "Kamu adalah AI trend-analyst. Kalau user minta cari topik trending, gunakan web search "
    "untuk menemukan topik NYATA (jangan mengarang), lalu panggil tool "
    "submit_trend_recommendations dengan hasilnya. Tiap topic harus unik (tidak boleh sama "
    "persis dengan topic lain di payload yang sama)."
)


# ─────────────────────────────────────────────────────────────────────────────
# Provider: Claude (Anthropic) — pakai web_search bawaan + tool use
# ─────────────────────────────────────────────────────────────────────────────

def run_with_claude(prompt: str, model: str = "claude-sonnet-5") -> None:
    import anthropic

    client = anthropic.Anthropic()  # baca ANTHROPIC_API_KEY dari env
    tools = [
        {"type": "web_search_20250305", "name": "web_search"},  # hosted tool, Claude browsing sendiri
        {
            "name": "submit_trend_recommendations",
            "description": TOOL_DESCRIPTION,
            "input_schema": TOOL_PARAMETERS_JSONSCHEMA,
        },
    ]

    messages = [{"role": "user", "content": prompt}]

    while True:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            # Selesai — cetak jawaban akhir Claude
            for block in response.content:
                if block.type == "text":
                    print(block.text)
            break

        tool_results = []
        for block in tool_uses:
            if block.name == "submit_trend_recommendations":
                print(f"\n[EXECUTING] submit_trend_recommendations({json.dumps(block.input)[:200]}...)")
                result = submit_trend_recommendations(**block.input)
                print(f"[RESULT] {result}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })
            # block "web_search" ditangani otomatis oleh Claude (hosted tool), tidak perlu dieksekusi manual

        if tool_results:
            messages.append({"role": "user", "content": tool_results})


# ─────────────────────────────────────────────────────────────────────────────
# Provider: OpenAI — function calling (tanpa web browsing bawaan di Chat Completions)
# ─────────────────────────────────────────────────────────────────────────────

def run_with_openai(prompt: str, model: str = "gpt-4o") -> None:
    from openai import OpenAI

    client = OpenAI()  # baca OPENAI_API_KEY dari env
    tools = [{
        "type": "function",
        "function": {
            "name": "submit_trend_recommendations",
            "description": TOOL_DESCRIPTION,
            "parameters": TOOL_PARAMETERS_JSONSCHEMA,
        },
    }]

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    while True:
        response = client.chat.completions.create(model=model, messages=messages, tools=tools)
        msg = response.choices[0].message
        messages.append(msg.model_dump())

        if not msg.tool_calls:
            print(msg.content)
            break

        for call in msg.tool_calls:
            if call.function.name == "submit_trend_recommendations":
                args = json.loads(call.function.arguments)
                print(f"\n[EXECUTING] submit_trend_recommendations({json.dumps(args)[:200]}...)")
                result = submit_trend_recommendations(**args)
                print(f"[RESULT] {result}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result),
                })


# ─────────────────────────────────────────────────────────────────────────────
# Provider: Ollama (lokal) — endpoint kompatibel OpenAI, tanpa web search bawaan
# ─────────────────────────────────────────────────────────────────────────────

def run_with_ollama(prompt: str, model: str = "qwen3:8b") -> None:
    from openai import OpenAI

    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434") + "/v1"
    client = OpenAI(base_url=base_url, api_key="ollama")  # api_key diabaikan Ollama, tapi wajib diisi

    tools = [{
        "type": "function",
        "function": {
            "name": "submit_trend_recommendations",
            "description": TOOL_DESCRIPTION,
            "parameters": TOOL_PARAMETERS_JSONSCHEMA,
        },
    }]

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + (
            " CATATAN: kamu TIDAK punya akses internet — kalau user minta topik "
            "'trending hari ini', jawab dari pengetahuanmu tapi beri tahu user "
            "bahwa ini bukan data real-time."
        )},
        {"role": "user", "content": prompt},
    ]

    while True:
        response = client.chat.completions.create(model=model, messages=messages, tools=tools)
        msg = response.choices[0].message
        messages.append(msg.model_dump())

        if not msg.tool_calls:
            print(msg.content)
            break

        for call in msg.tool_calls:
            if call.function.name == "submit_trend_recommendations":
                args = json.loads(call.function.arguments)
                print(f"\n[EXECUTING] submit_trend_recommendations({json.dumps(args)[:200]}...)")
                result = submit_trend_recommendations(**args)
                print(f"[RESULT] {result}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result),
                })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["claude", "openai", "ollama"], required=True)
    parser.add_argument("--prompt", required=True, help="Prompt user, mis. 'cari 10 topik trending starbucks'")
    parser.add_argument("--model", default=None, help="Override model default per provider")
    args = parser.parse_args()

    if args.provider == "claude":
        run_with_claude(args.prompt, model=args.model or "claude-sonnet-5")
    elif args.provider == "openai":
        run_with_openai(args.prompt, model=args.model or "gpt-4o")
    elif args.provider == "ollama":
        run_with_ollama(args.prompt, model=args.model or "qwen3:8b")


if __name__ == "__main__":
    main()
