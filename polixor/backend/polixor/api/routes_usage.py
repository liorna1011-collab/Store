"""
The customer's plan usage – source video minutes only (services/billing.py).

  GET  /api/usage          plan, billing period, minutes used / remaining, per-project minutes
  POST /api/usage/check    would a video of this length fit right now? (before Start)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..services import billing

router = APIRouter(prefix="/api", tags=["usage"])


@router.get("/usage")
def usage() -> dict[str, Any]:
    s = billing.customer_summary()
    # minutes only: never tokens, prices of our suppliers, model calls or machine time
    return {k: s[k] for k in ("plan", "period", "used_minutes", "remaining_minutes", "available_ms",
                              "projects", "rounding", "adjusted_minutes")}


class CheckIn(BaseModel):
    duration_seconds: float = Field(ge=0, le=7 * 24 * 3600)


@router.post("/usage/check")
def usage_check(body: CheckIn) -> dict[str, Any]:
    return billing.check(body.duration_seconds)
