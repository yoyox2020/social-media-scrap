"""IndoBERT sentiment model wrapper."""


class SentimentModel:
    MODEL_NAME = "indobenchmark/indobert-base-p1"
    VERSION = "1.0.0"

    def __init__(self):
        self._model = None
        self._tokenizer = None

    def load(self) -> None:
        # TODO: Phase 5 - load IndoBERT model from HuggingFace
        raise NotImplementedError

    def predict(self, text: str) -> dict:
        # TODO: Phase 5 - run inference, return {"label": ..., "score": ...}
        raise NotImplementedError

    def predict_batch(self, texts: list[str]) -> list[dict]:
        # TODO: Phase 5 - batch inference
        raise NotImplementedError
