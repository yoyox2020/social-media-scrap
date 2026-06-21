"""BGE-M3 embedding model wrapper — outputs 1024-dim vectors."""


class EmbeddingModel:
    MODEL_NAME = "BAAI/bge-m3"
    VERSION = "1.0.0"
    DIMENSION = 1024

    def __init__(self):
        self._model = None

    def load(self) -> None:
        # TODO: Phase 5 - load BGE-M3 from HuggingFace
        raise NotImplementedError

    def encode(self, text: str) -> list[float]:
        # TODO: Phase 5 - return 1024-dim embedding
        raise NotImplementedError

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        # TODO: Phase 5 - batch encode
        raise NotImplementedError
