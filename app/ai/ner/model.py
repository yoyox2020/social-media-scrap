"""GLiNER NER model wrapper."""


class NERModel:
    MODEL_NAME = "urchade/gliner_multi-v2.1"
    VERSION = "1.0.0"

    def __init__(self):
        self._model = None

    def load(self) -> None:
        # TODO: Phase 5 - load GLiNER from HuggingFace
        raise NotImplementedError

    def predict(self, text: str, entity_types: list[str]) -> list[dict]:
        # TODO: Phase 5 - run NER inference
        raise NotImplementedError
