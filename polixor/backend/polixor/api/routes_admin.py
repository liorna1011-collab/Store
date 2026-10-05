"""
Admin / developer view – internal numbers a customer never sees (cost, tokens, model calls,
margin, upload telemetry, machine tuning, stuck jobs).

Access needs the admin token, separate from the site password:
  POLIXOR_ADMIN_TOKEN, or the file <data>/admin.token (created on first start, mode 600).
POST /api/admin/session {token} sets a signed, HttpOnly admin cookie (12 hours); scripts may send
the token as the X-Polixor-Admin header instead. Without it every route here answers 403.

Customer routes (/api/usage, /api/projects, /api/studio/...) carry minutes only.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import PATHS
from ..db import db_dependency
from ..models import Job
from ..services import billing
from .http import api_error

router = APIRouter(prefix="/api/admin", tags=["admin"])

COOKIE = "polixor_admin"
SESSION_SECONDS = 12 * 3600


def admin_token() -> str:
    env = os.environ.get("POLIXOR_ADMIN_TOKEN", "").strip()
    if env:
        return env
    path = PATHS.data / "admin.token"
    try:
        t = path.read_text("utf-8").strip()
        if len(t) >= 24:
            return t
    except OSError:
        pass
    PATHS.data.mkdir(parents=True, exist_ok=True)
    t = secrets.token_urlsafe(32)
    path.write_text(t + "\n", "utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return t


def _key() -> bytes:
    return hashlib.sha256(b"polixor-admin." + admin_token().encode("utf-8")).digest()


def _sign(issued: int) -> str:
    return f"{issued}.{hmac.new(_key(), str(issued).encode(), hashlib.sha256).hexdigest()}"


def _valid_cookie(value: str) -> bool:
    try:
        issued_s, sig = value.split(".", 1)
        issued = int(issued_s)
    except ValueError:
        return False
    if time.time() - issued > SESSION_SECONDS:
        return False
    return hmac.compare_digest(_sign(issued), value)


def is_admin(request: Request) -> bool:
    header = request.headers.get("x-polixor-admin", "")
    if header and hmac.compare_digest(header.strip(), admin_token()):
        return True
    return _valid_cookie(request.cookies.get(COOKIE, ""))


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise api_error("admin_required", 403)


class SessionIn(BaseModel):
    token: str = Field(max_length=200)


@router.get("/session")
def session_state(request: Request) -> dict[str, Any]:
    return {"admin": is_admin(request)}


@router.post("/session")
def open_session(body: SessionIn, request: Request, response: Response) -> dict[str, Any]:
    time.sleep(0.4)                                  # slows guessing; the token is 256 bits anyway
    if not hmac.compare_digest(body.token.strip(), admin_token()):
        raise api_error("admin_required", 403)
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(COOKIE, _sign(int(time.time())), max_age=SESSION_SECONDS, httponly=True,
                        samesite="strict", secure=secure, path="/api/admin")
    return {"admin": True}


@router.delete("/session")
def close_session(response: Response) -> dict[str, Any]:
    response.delete_cookie(COOKIE, path="/api/admin")
    return {"admin": False}


@router.get("/overview", dependencies=[Depends(require_admin)])
def overview(limit: int = Query(50, ge=1, le=500), db: Session = Depends(db_dependency)) -> dict[str, Any]:
    from ..services import costs, hardware

    summ = billing.customer_summary()
    plan = summ["plan"]
    rows = []
    for job in db.query(Job).order_by(Job.created_at.desc()).limit(limit).all():
        charged = billing.project_minutes(job.id)
        c = costs.project(job, charged["duration_ms"], plan)
        rows.append({"project_id": job.id, "title": job.title, "status": job.status.value,
                     "phase": job.phase, "billing_state": charged["state"],
                     "minutes_deducted": charged["minutes"], **c})
    tot = {k: round(sum(r[k] for r in rows), 4) for k in ("ai_cost_usd", "infra_usd", "revenue_usd",
                                                         "gross_margin_usd")}
    return {"account": summ, "totals": tot, "projects": rows, "prices": costs.prices(),
            "machine": hardware.profile(), "tuning": hardware.tuning()}


@router.get("/ledger", dependencies=[Depends(require_admin)])
def ledger(limit: int = Query(500, ge=1, le=5000)) -> dict[str, Any]:
    return {"entries": billing.ledger(limit=limit)}


class PlanIn(BaseModel):
    code: str


@router.post("/plan", dependencies=[Depends(require_admin)])
def set_plan(body: PlanIn) -> dict[str, Any]:
    try:
        return billing.set_plan(body.code)
    except ValueError:
        raise api_error("bad_request", 400) from None


class AdjustIn(BaseModel):
    minutes: float = Field(ge=-100000, le=100000)    # negative = credit back to the customer
    note: str = Field(default="", max_length=500)
    key: Optional[str] = Field(default=None, max_length=60)   # repeat-safe


@router.post("/adjust", dependencies=[Depends(require_admin)])
def adjust(body: AdjustIn) -> dict[str, Any]:
    return {"entry": billing.adjust(body.minutes, note=body.note, key=body.key or ""),
            "account": billing.customer_summary()}


@router.get("/projects/{pid}/diagnostics", dependencies=[Depends(require_admin)])
def project_diagnostics(pid: str, db: Session = Depends(db_dependency)) -> dict[str, Any]:
    """The full profile, model usage included."""
    from ..services import costs, diagnostics

    job = db.get(Job, pid)
    if job is None:
        raise api_error("job_not_found", 404)
    charged = billing.project_minutes(pid)
    return {"performance": diagnostics.profile(job, internal=True), "gate": diagnostics.gate(job),
            "economics": costs.project(job, charged["duration_ms"], billing.customer_summary()["plan"]),
            "billing": charged}


@router.get("/uploads", dependencies=[Depends(require_admin)])
def upload_telemetry(limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    from ..services import uploads

    return {"uploads": uploads.all_telemetry(limit)}


@router.get("/health", dependencies=[Depends(require_admin)])
def health() -> dict[str, Any]:
    from ..services import health as health_svc

    return health_svc.report()


def token_location() -> Path:
    return PATHS.data / "admin.token"
