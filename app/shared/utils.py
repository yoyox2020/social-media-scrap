import uuid
from datetime import datetime, timezone


def generate_uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_success_response(data: object) -> dict:
    return {"success": True, "data": data}


def build_error_response(code: str, message: str) -> dict:
    return {"success": False, "error": {"code": code, "message": message}}


def paginate(page: int, page_size: int) -> tuple[int, int]:
    offset = (page - 1) * page_size
    return offset, page_size
