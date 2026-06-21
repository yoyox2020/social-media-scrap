import pytest

from app.infrastructure.security.jwt import create_access_token, create_refresh_token, decode_token
from app.shared.exceptions import UnauthorizedError


def test_access_token_decode_success():
    token = create_access_token("user-123", {"role": "user"})
    payload = decode_token(token)
    assert payload["sub"] == "user-123"
    assert payload["type"] == "access"
    assert payload["role"] == "user"


def test_refresh_token_decode_success():
    token = create_refresh_token("user-123")
    payload = decode_token(token)
    assert payload["sub"] == "user-123"
    assert payload["type"] == "refresh"


def test_invalid_token_raises_unauthorized():
    with pytest.raises(UnauthorizedError):
        decode_token("not.a.valid.token")
