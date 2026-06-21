import hashlib
import secrets


def generate_api_key() -> tuple[str, str]:
    """Returns (raw_key, hashed_key). Store only the hash."""
    raw_key = secrets.token_urlsafe(32)
    hashed = hash_api_key(raw_key)
    return raw_key, hashed


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def verify_api_key(raw_key: str, hashed_key: str) -> bool:
    return hash_api_key(raw_key) == hashed_key
