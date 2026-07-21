"""
Dynamic HTTP client for EnsembleData API (https://ensembledata.com/apis).

Design: all endpoints are called via request() so adding new endpoints
requires only a change to endpoints.py — not to this client.

2026-07-20: request() SEKARANG rotasi otomatis antar token di
app/services/ensembledata_pool/config.py kalau token yg dipakai kena
kuota harian (HTTP 495 "Maximum requests limit reached for today") --
pola SAMA dgn app/integrations/apify/rotation.py, TRANSPARAN ke SEMUA
caller (Threads/Instagram/YouTube fallback/viral_tracking/collector, 6
titik panggilan) krn semua cuma instantiate `EnsembleDataClient()` polos,
tidak perlu ubah kode di titik manapun. Kalau `api_token` diberikan
EKSPLISIT saat construct (caller sengaja minta token SPESIFIK), rotasi
di-skip -- pakai persis token itu, hormati intent eksplisit caller.
"""
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.shared.config import settings
from app.shared.ensembledata_errors import is_quota_error
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
        self._explicit_token = api_token is not None
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
    async def _request_once(
        self,
        method: str,
        endpoint: str,
        token: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("Client not initialized — use async context manager")

        all_params = {"token": token, **(params or {})}

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

    async def request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Send a request to any EnsembleData endpoint dynamically, dgn ROTASI
        OTOMATIS antar token kalau token yg dipakai kena kuota harian.

        Args:
            method:   HTTP method (GET, POST, etc.)
            endpoint: API path, e.g. "/tt/user/info"
            params:   Query parameters (api_token is injected automatically)
            json:     Request body for POST

        Returns:
            Parsed JSON response
        """
        from app.services.ensembledata_pool import config as pool_cfg

        pool: list[str] = [] if self._explicit_token else await pool_cfg.get_pool()
        tokens_to_try = pool if pool else ([self.api_token] if self.api_token else [])
        if not tokens_to_try:
            raise ExternalAPIError(service="EnsembleData", message="Belum ada EnsembleData API token (pool kosong & ENSEMBLE_DATA_API_TOKEN .env jg kosong)")

        if pool:
            exhausted_flags = {t: await pool_cfg.is_exhausted(t) for t in tokens_to_try}
            tokens_to_try = sorted(tokens_to_try, key=lambda t: exhausted_flags[t])

        last_exc: Exception | None = None
        for token in tokens_to_try:
            try:
                return await self._request_once(method, endpoint, token, params, json)
            except ExternalAPIError as exc:
                if not is_quota_error(exc=exc):
                    raise
                if pool:
                    await pool_cfg.mark_exhausted(token)
                last_exc = exc
                continue

        raise last_exc or ExternalAPIError(service="EnsembleData", message="Semua token EnsembleData kena quota")

    async def get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.request("GET", endpoint, params=params)

    async def post(self, endpoint: str, json: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.request("POST", endpoint, params=params, json=json)


def get_ensemble_client() -> EnsembleDataClient:
    """FastAPI dependency — yields a configured client instance."""
    return EnsembleDataClient()
