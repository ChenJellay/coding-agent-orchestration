from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from fastapi import HTTPException
from fastapi.responses import JSONResponse


@dataclass(frozen=True)
class ApiError:
    code: str
    message: str
    status_code: int = 400

    def to_payload(self) -> Dict[str, Any]:
        return {"ok": False, "error": {"code": self.code, "message": self.message}}


def json_error(*, code: str, message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=ApiError(code=code, message=message, status_code=status_code).to_payload())

def raise_http_error(*, code: str, message: str, status_code: int = 400) -> None:
    # Keep payload shape stable for the frontend contract.
    payload = ApiError(code=code, message=message, status_code=status_code).to_payload()
    raise HTTPException(status_code=status_code, detail=payload["error"])


def ok_payload(data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"ok": True, **(data or {})}

