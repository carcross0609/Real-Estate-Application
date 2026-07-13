"""Typed exception hierarchy → RFC 9457 problem+json, mapped in one FastAPI handler.

Never raise bare HTTPException from module code; raise one of these so the response shape
stays consistent and callers get a stable `type` to branch on.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class DealLensError(Exception):
    status_code: int = 500
    problem_type: str = "about:blank"

    def __init__(self, detail: str, **extra: object) -> None:
        self.detail = detail
        self.extra = extra
        super().__init__(detail)


class NotFoundError(DealLensError):
    status_code = 404
    problem_type = "https://deallens.dev/errors/not-found"


class ValidationError(DealLensError):
    status_code = 422
    problem_type = "https://deallens.dev/errors/validation"


class UnauthenticatedError(DealLensError):
    status_code = 401
    problem_type = "https://deallens.dev/errors/unauthenticated"


class ForbiddenError(DealLensError):
    """Raised by `identity.authorize()` — role, entitlement, or ownership check failed."""

    status_code = 403
    problem_type = "https://deallens.dev/errors/forbidden"


class ConflictError(DealLensError):
    status_code = 409
    problem_type = "https://deallens.dev/errors/conflict"


class RateLimitedError(DealLensError):
    status_code = 429
    problem_type = "https://deallens.dev/errors/rate-limited"


class UpstreamServiceError(DealLensError):
    """A third-party dependency (Clerk, Stripe, ...) failed or was unreachable."""

    status_code = 502
    problem_type = "https://deallens.dev/errors/upstream-service"


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DealLensError)
    async def _handle(request: Request, exc: DealLensError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "type": exc.problem_type,
                "title": exc.__class__.__name__,
                "status": exc.status_code,
                "detail": exc.detail,
                **exc.extra,
            },
            media_type="application/problem+json",
        )
