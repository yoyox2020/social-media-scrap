from pydantic import BaseModel


class NERInput(BaseModel):
    text: str
    entity_types: list[str] = ["PERSON", "ORG", "LOC", "EVENT", "PRODUCT"]


class NEREntity(BaseModel):
    text: str
    entity_type: str
    start: int
    end: int
    score: float


class NEROutput(BaseModel):
    entities: list[NEREntity]
    model_version: str
