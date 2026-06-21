"""Sentiment service coordinates IndoBERT inference and result storage."""


class SentimentService:
    async def analyze_post(self, post_id: str) -> dict:
        # TODO: Phase 5 - call AI sentiment module → store result
        raise NotImplementedError

    async def analyze_batch(self, post_ids: list[str]) -> list[dict]:
        # TODO: Phase 5 - batch inference
        raise NotImplementedError
