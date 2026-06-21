"""
Dynamic HTTP client for EnsembleData API (https://ensembledata.com/apis).

Design: all endpoints are called via request() so adding new endpoints
requires only a change to endpoints.py — not to this client.
"""
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.shared.config import settings
from app.shared.exceptions import ExternalAPIError


class EnsembleDataClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_token: str | None = None,
        timeout: int | None = None,
    ):
        self.base_url = (base_url or settings.ensemble_data_base_url).rstrip("/")
        self.api_token = api_token or settings.ensemble_data_api_token
        self.timeout = timeout or settings.ensemble_data_timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "EnsembleDataClient":
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={"User-Agent": "social-intelligence/1.0"},
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client:
            await self._client.aclose()

    @retry(
        stop=stop_after_attempt(settings.ensemble_data_max_retries),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Send a request to any EnsembleData endpoint dynamically.

        Args:
            method:   HTTP method (GET, POST, etc.)
            endpoint: API path, e.g. "/tt/user/info"
            params:   Query parameters (api_token is injected automatically)
            json:     Request body for POST

        Returns:
            Parsed JSON response
        """
        if self._client is None:
            raise RuntimeError("Client not initialized — use async context manager")

        all_params = {"token": self.api_token, **(params or {})}

        try:
            response = await self._client.request(
                method=method.upper(),
                url=endpoint,
                params=all_params,
                json=json,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise ExternalAPIError(
                service="EnsembleData",
                message=f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except httpx.RequestError as exc:
            raise ExternalAPIError(service="EnsembleData", message=str(exc))

    async def get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.request("GET", endpoint, params=params)

    async def post(self, endpoint: str, json: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.request("POST", endpoint, params=params, json=json)


def get_ensemble_client() -> EnsembleDataClient:
    """FastAPI dependency — yields a configured client instance."""
    return EnsembleDataClient()
