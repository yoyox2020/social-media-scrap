import pytest
import httpx
import respx

from app.integrations.ensemble_data.client import EnsembleDataClient


@pytest.mark.asyncio
@respx.mock
async def test_client_get_injects_token():
    token = "test-token"
    respx.get("https://ensembledata.com/apis/tt/keyword/search").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    client = EnsembleDataClient(api_token=token)
    async with client:
        result = await client.get("/tt/keyword/search", params={"keyword": "test"})
    assert result == {"data": []}
