from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    error_code: str
    message: str
    request_id: Optional[str] = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
