from fastapi import HTTPException, status


class AppException(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class NotFoundError(AppException):
    def __init__(self, resource: str, resource_id: str = ""):
        super().__init__(
            code="NOT_FOUND",
            message=f"{resource} not found" + (f": {resource_id}" if resource_id else ""),
            status_code=404,
        )


class UnauthorizedError(AppException):
    def __init__(self, message: str = "Unauthorized"):
        super().__init__(code="UNAUTHORIZED", message=message, status_code=401)


class ForbiddenError(AppException):
    def __init__(self, message: str = "Forbidden"):
        super().__init__(code="FORBIDDEN", message=message, status_code=403)


class ValidationError(AppException):
    def __init__(self, message: str):
        super().__init__(code="VALIDATION_ERROR", message=message, status_code=422)


class ConflictError(AppException):
    def __init__(self, message: str):
        super().__init__(code="CONFLICT", message=message, status_code=409)


class ExternalAPIError(AppException):
    def __init__(self, service: str, message: str):
        super().__init__(
            code="EXTERNAL_API_ERROR",
            message=f"{service} error: {message}",
            status_code=502,
        )
