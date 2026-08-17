"""Domain-level errors, translated to HTTP responses in main.py.

Kept free of FastAPI imports so repositories can raise them without the
application layer depending on the web framework.
"""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def payload(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, **self.details}


class NotFoundError(ApiError):
    status_code = 404
    code = "not_found"


class ValidationError(ApiError):
    status_code = 422
    code = "invalid_request"


class AmbiguousEntityError(ApiError):
    """A fuzzy `q=` matched nothing confidently, or matched two things equally.

    422 with the candidates, never a guess: a real number attached to the
    wrong project is worse than a visibly wrong number (API_DESIGN.md §7).
    """

    status_code = 422
    code = "ambiguous_entity"
