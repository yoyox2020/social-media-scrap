"""News connector — placeholder for news source integration."""
from typing import Any

from app.integrations.ensemble_data.client import EnsembleDataClient


class NewsConnector:
    def __init__(self, client: EnsembleDataClient):
        self.client = client

    async def search_by_keyword(self, keyword: str, **params: Any) -> dict[str, Any]:
        # TODO: Phase 3 - implement news keyword search
        raise NotImplementedError
