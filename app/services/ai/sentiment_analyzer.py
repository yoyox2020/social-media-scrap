"""
IndoBERT Sentiment Analyzer — lazy-loaded, singleton per worker process.

Model default: mdhugol/indonesia-bert-sentiment-classification
  - LABEL_0 → negative
  - LABEL_1 → neutral
  - LABEL_2 → positive

Model di-load saat pertama kali analyze() dipanggil, lalu di-cache selamanya
selama proses worker hidup.
"""
from __future__ import annotations

from app.services.ai.schemas import SentimentResult

# Label mapping dari model mdhugol ke label yang kita gunakan
_LABEL_MAP: dict[str, str] = {
    "LABEL_0": "negative",
    "LABEL_1": "neutral",
    "LABEL_2": "positive",
    # fallback jika model lain pakai label langsung
    "negative": "negative",
    "neutral": "neutral",
    "positive": "positive",
    "NEGATIVE": "negative",
    "NEUTRAL": "neutral",
    "POSITIVE": "positive",
}

_MAX_LENGTH = 512  # BERT max token limit


class SentimentAnalyzer:
    _instance: SentimentAnalyzer | None = None

    def __init__(self, model_name: str | None = None, cache_dir: str | None = None):
        from transformers import AutoModelForSequenceClassification, AutoTokenizer, pipeline

        from app.shared.config import settings

        self.model_name = model_name or settings.indobert_model_name
        resolved_cache_dir = cache_dir or settings.models_cache_dir

        # cache_dir cuma relevan saat DOWNLOAD model/tokenizer — kalau diteruskan
        # langsung ke pipeline(cache_dir=...), transformers 4.45.x ikut
        # menyisipkannya ke argumen tokenizer saat inference (bukan cuma
        # download) dan crash: "_batch_encode_plus() got an unexpected
        # keyword argument 'cache_dir'". Load tokenizer/model eksplisit dulu
        # (cache_dir cuma dipakai di sini), baru bungkus jadi pipeline tanpa
        # cache_dir sama sekali.
        tokenizer = AutoTokenizer.from_pretrained(self.model_name, cache_dir=resolved_cache_dir)
        model = AutoModelForSequenceClassification.from_pretrained(self.model_name, cache_dir=resolved_cache_dir)

        self._pipeline = pipeline(
            "text-classification",
            model=model,
            tokenizer=tokenizer,
            truncation=True,
            max_length=_MAX_LENGTH,
            device=-1,  # CPU; set 0 untuk GPU
        )

    @classmethod
    def get_instance(cls) -> "SentimentAnalyzer":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def analyze(self, text: str) -> SentimentResult:
        """Analisis sentimen satu teks. Return SentimentResult."""
        if not text or not text.strip():
            return SentimentResult(label="neutral", score=0.0, model_version=self.model_name)

        truncated = text[:2000]  # potong sebelum tokenisasi
        output = self._pipeline(truncated)[0]
        label = _LABEL_MAP.get(output["label"], "neutral")

        return SentimentResult(
            label=label,
            score=round(output["score"], 4),
            model_version=self.model_name,
        )

    def analyze_batch(self, texts: list[str]) -> list[SentimentResult]:
        """Analisis batch teks. Lebih efisien dari loop manual."""
        if not texts:
            return []
        cleaned = [t[:2000] if t else "" for t in texts]
        outputs = self._pipeline(cleaned, batch_size=16)
        return [
            SentimentResult(
                label=_LABEL_MAP.get(o["label"], "neutral"),
                score=round(o["score"], 4),
                model_version=self.model_name,
            )
            for o in outputs
        ]
