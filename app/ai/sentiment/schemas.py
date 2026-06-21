from pydantic import BaseModel


class SentimentInput(BaseModel):
    text: str


class SentimentOutput(BaseModel):
    label: str
    score: float
    model_version: str
