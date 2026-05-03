from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, message: str, code: str, status_code: int, details: dict | None = None):
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


class NotFoundError(AppError):
    def __init__(self, message: str = "Resource not found", details: dict | None = None):
        super().__init__(message, "NOT_FOUND", 404, details)


class ForbiddenError(AppError):
    def __init__(self, message: str = "Access denied", details: dict | None = None):
        super().__init__(message, "AUTH_FORBIDDEN", 403, details)


class AuthRequiredError(AppError):
    def __init__(self, message: str = "Authentication required"):
        super().__init__(message, "AUTH_REQUIRED", 401)


class AuthInvalidError(AppError):
    def __init__(self, message: str = "Token is invalid or expired"):
        super().__init__(message, "AUTH_INVALID", 401)


class ConflictError(AppError):
    def __init__(self, message: str = "Resource already exists", details: dict | None = None):
        super().__init__(message, "CONFLICT", 409, details)


class ValidationError(AppError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, "VALIDATION_ERROR", 422, details)


class FileTooLargeError(AppError):
    def __init__(self, message: str = "File exceeds size limit"):
        super().__init__(message, "FILE_TOO_LARGE", 413)


class InternalError(AppError):
    def __init__(self, message: str = "An unexpected error occurred"):
        super().__init__(message, "INTERNAL_ERROR", 500)


# ── FastAPI exception handler ──────────────────────────────────────────────

async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        },
    )

# Is function ko file ke end mein add karein
def register_exception_handlers(app):
    """
    Ye function hamare custom AppError ko FastAPI ke saath register karta hai.
    """
    app.add_exception_handler(AppError, app_error_handler)