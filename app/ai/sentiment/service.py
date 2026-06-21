"""Thin service wrapping SentimentModel — called by workers and API."""
from app.ai.sentiment.model import SentimentModel
from app.ai.sentiment.schemas import SentimentInput, SentimentOutput


class SentimentAIService:
    def __init__(self):
        self.model = SentimentModel()

    def analyze(self, input_data: SentimentInput) -> SentimentOutput:
        # TODO: Phase 5 - delegate to model.predict()
        raise NotImplementedError
