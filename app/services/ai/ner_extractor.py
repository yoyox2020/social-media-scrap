"""
GLiNER NER Extractor — lazy-loaded, singleton per worker process.

Model: urchade/gliner_multi-v2.1 (multilingual, termasuk Bahasa Indonesia)

Entity types yang diextract dari social media content:
  PERSON, ORGANIZATION, LOCATION, PRODUCT, EVENT, DATE, MONEY, LAW
"""
from __future__ import annotations

from app.services.ai.schemas import EntityResult

DEFAULT_ENTITY_TYPES = [
    "PERSON",
    "ORGANIZATION",
    "LOCATION",
    "PRODUCT",
    "EVENT",
    "DATE",
    "MONEY",
    "LAW",
]

_DEFAULT_THRESHOLD = 0.5
_MAX_TEXT_LENGTH = 1024


class NERExtractor:
    _instance: NERExtractor | None = None

    def __init__(self, model_name: str | None = None, cache_dir: str | None = None):
        from gliner import GLiNER

        from app.shared.config import settings

        self.model_name = model_name or settings.gliner_model_name
        self._model = GLiNER.from_pretrained(
            self.model_name,
            cache_dir=cache_dir or settings.models_cache_dir,
        )

    @classmethod
    def get_instance(cls) -> "NERExtractor":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def extract(
        self,
        text: str,
        entity_types: list[str] | None = None,
        threshold: float = _DEFAULT_THRESHOLD,
    ) -> list[EntityResult]:
        """
        Extract named entities dari teks.

        Args:
            text:         Teks yang akan di-NER
            entity_types: Type entity yang dicari (default: DEFAULT_ENTITY_TYPES)
            threshold:    Score minimum (0.0–1.0)

        Returns:
            list EntityResult diurutkan berdasarkan start_char
        """
        if not text or not text.strip():
            return []

        types = entity_types or DEFAULT_ENTITY_TYPES
        truncated = text[:_MAX_TEXT_LENGTH]

        raw_entities = self._model.predict_entities(
            truncated, types, threshold=threshold
        )

        results = [
            EntityResult(
                text=e["text"],
                entity_type=e["label"],
                start_char=e["start"],
                end_char=e["end"],
                score=round(e["score"], 4),
            )
            for e in raw_entities
        ]

        # Urutkan berdasarkan posisi dalam teks
        results.sort(key=lambda r: r.start_char)
        return results
