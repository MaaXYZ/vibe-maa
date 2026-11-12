from typing import Any


class DevError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "details": self.details}


def failure_detail(error: Exception) -> dict[str, Any]:
    if isinstance(error, DevError):
        return error.as_dict()
    return {"code": "OPERATION_FAILED", "message": str(error), "details": {}}
