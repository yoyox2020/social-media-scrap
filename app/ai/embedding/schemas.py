from pydantic import BaseModel

from app.shared.constants import EMBEDDING_DIMENSION


class EmbeddingInput(BaseModel):
    text: str


class EmbeddingOutput(BaseModel):
    embedding: list[float]
    dimension: int = EMBEDDING_DIMENSION
    model_version: str
