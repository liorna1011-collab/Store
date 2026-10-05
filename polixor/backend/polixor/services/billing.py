"""
Customer usage in SOURCE VIDEO MINUTES – the only number a customer ever sees.

A billable minute is the probed duration of the source media (ffprobe on the uploaded file,
or the probed length of the imported section / live capture). Not tokens, not dollars, not
model calls, not GPU seconds: those are internal costs (services/costs.py, admin only).

Ledger (models.UsageEntry, append-only). One charge per (project, source, cycle):

    reserve   after the source was probed, before anything runs – refused when the plan
              does not have the minutes ("You have 18 minutes remaining. This video is 42
              minutes."); the project is then not created
    commit    when processing starts (the first expensive stage)
    release   the charge is given back: an infrastructure failure before meaningful work
              (transcription finished), or a cancel the cancel policy refunds

A charge's state is its latest row. Every step has a unique idempotency key, so a double
click, an API retry, a refresh, a server restart, a worker resume – each repeats a step that
is already recorded and adds nothing. A re-render, a subtitle edit, a download, reopening a
project never touch the ledger at all. Reservations are atomic across projects: the check
and the insert run under one lock and one SQLite write transaction, so two projects started
at the same moment can never both take the last minutes.

Rounding (documented policy): the ledger stores exact milliseconds; every sum and every quota
check uses them. Rounding happens once, for display, on totals: whole minutes, half up
(59 s → 1, 61 s → 1, 89 s → 1, 90 s → 2, 28:34 → 29, 59:59 → 60). Remaining is shown as
plan minutes minus the shown used minutes, so the two always add up to the plan. Because
nothing is rounded per video, a hundred 59-second videos are 98 minutes, not 100.
"""

from __future__ import annotations

import calendar
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import Account, Job, UsageEntry

DEFAULT_ACCOUNT = "default"
MS_PER_MIN = 60_000

# plans: price in whole currency units per month, minutes of source video per period
PLANS: dict[str, dict[str, Any]] = {
    "starter": {"name": "Starter", "price": 99, "currency": "ILS", "minutes": 300},
    "pro": {"name": "Pro", "price": 249, "currency": "ILS", "minutes": 900},
    "studio": {"name": "Studio", "price": 599, "currency": "ILS", "minutes": 2400},
}

# what a cancel gives back (settings.billing_cancel_policy):
#   refund_before_output    released unless an output (a clip) already exists   (default)
#   refund_before_commit    released only while still reserved (processing not started)
#   never                   a committed charge is never released on cancel
CANCEL_POLICIES = ("refund_before_output", "refund_before_commit", "never")

USAGE_TYPES = ("source_processing", "additional_source_processing", "manual_credit_adjustment")

_LOCK = threading.Lock()


class QuotaExceeded(Exception):
    def __init__(self, remaining_ms: int, needed_ms: int) -> None:
        super().__init__("quota_exceeded")
        self.remaining_ms, self.needed_ms = remaining_ms, needed_ms

    def params(self) -> dict[str, str]:
        return {"remaining": display_duration(self.remaining_ms),
                "video": display_duration(self.needed_ms)}


# --------------------------------------------------------------------------
# rounding
# --------------------------------------------------------------------------
def round_minutes(ms: int) -> int:
    """Whole minutes, half up (exact integer arithmetic – no float rounding surprises)."""
    ms = int(ms)
    if ms <= 0:
        return 0
    return (ms + MS_PER_MIN // 2) // MS_PER_MIN


def display_duration(ms: int) -> str:
    """For messages: whole minutes; under 10 minutes also the seconds (9:41), so two close values never read the same."""
    ms = max(0, int(ms))
    if ms < 10 * MS_PER_MIN:
        s = ms // 1000
        return f"{s // 60}:{s % 60:02d}"
    return str(round_minutes(ms))


def seconds_to_ms(seconds: float) -> int:
    return int(round(float(seconds or 0) * 1000))


# --------------------------------------------------------------------------
# account and period
# --------------------------------------------------------------------------
def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def _add_month(dt: datetime) -> datetime:
    y, m = (dt.year + (dt.month // 12), dt.month % 12 + 1)
    day = min(dt.day, calendar.monthrange(y, m)[1])
    return dt.replace(year=y, month=m, day=day)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def account(s: Session, account_id: str = DEFAULT_ACCOUNT, *, now: Optional[datetime] = None) -> Account:
    """The account, created on first use; its period rolls forward month by month to contain now."""
    now = _naive(now or _now())
    a = s.get(Account, account_id)
    if a is None:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        a = Account(id=account_id, plan_code="starter", billing_period_start=start,
                    billing_period_end=_add_month(start))
        s.add(a)
        s.flush()
    start, end = _naive(a.billing_period_start), _naive(a.billing_period_end)
    if now >= end:
        while now >= end:
            start, end = end, _add_month(end)
        a.billing_period_start, a.billing_period_end = start, end
        s.flush()
    return a


def plan(a: Account) -> dict[str, Any]:
    p = PLANS.get(a.plan_code) or PLANS["starter"]
    return {"code": a.plan_code if a.plan_code in PLANS else "starter", **p}


# --------------------------------------------------------------------------
# folding the ledger
# --------------------------------------------------------------------------
def _write_lock(s: Session) -> None:
    """Takes SQLite's write lock before reading the balance (another process cannot slip in between)."""
    try:
        s.execute(text("BEGIN IMMEDIATE"))
    except Exception:                                  # noqa: BLE001 – already in a write transaction
        pass


def _latest(s: Session, account_id: str, period_start: datetime) -> list[UsageEntry]:
    """The latest row of every charge of a period (the charge's current state)."""
    sub = (s.query(func.max(UsageEntry.id))
           .filter(UsageEntry.account_id == account_id, UsageEntry.period_start == period_start)
           .group_by(UsageEntry.charge_id))
    return s.query(UsageEntry).filter(UsageEntry.id.in_(sub)).all()


def _totals(rows: list[UsageEntry]) -> tuple[int, int]:
    used = sum(r.source_duration_ms for r in rows if r.status == "committed")
    held = sum(r.source_duration_ms for r in rows if r.status == "reserved")
    return used, held


def charge_state(s: Session, charge_id: str) -> Optional[UsageEntry]:
    return (s.query(UsageEntry).filter(UsageEntry.charge_id == charge_id)
            .order_by(UsageEntry.id.desc()).first())


def _append(s: Session, *, a: Account, charge_id: str, key: str, status: str, ms: int,
            project_id: str = "", source_id: str = "", usage_type: str = "source_processing",
            period_start: Optional[datetime] = None, reason: str = "", note: str = "") -> bool:
    """Appends one row; False when this exact step was recorded before (idempotent)."""
    if s.query(UsageEntry.id).filter(UsageEntry.idempotency_key == key).first():
        return False
    s.add(UsageEntry(account_id=a.id, charge_id=charge_id, project_id=project_id, source_id=source_id,
                     source_duration_ms=int(ms), usage_type=usage_type, status=status,
                     idempotency_key=key, period_start=period_start or _naive(a.billing_period_start),
                     reason=reason, note=note[:500]))
    try:
        s.flush()
    except IntegrityError:            # another process recorded the same step a moment ago
        s.rollback()
        return False
    return True


def _charge_id(project_id: str, source_id: str, s: Session) -> str:
    """project:source:cycle – a new cycle only after the previous charge was released."""
    base = f"{project_id}:{source_id or '-'}"
    rows = (s.query(UsageEntry.charge_id, UsageEntry.status, UsageEntry.id)
            .filter(UsageEntry.charge_id.like(f"{base}:%")).order_by(UsageEntry.id).all())
    if not rows:
        return f"{base}:1"
    last_charge = rows[-1][0]
    last_state = [r for r in rows if r[0] == last_charge][-1][1]
    if last_state == "released":
        return f"{base}:{int(last_charge.rsplit(':', 1)[1]) + 1}"
    return last_charge


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------
def reserve(project_id: str, source_id: str, duration_seconds: float, *,
            account_id: str = DEFAULT_ACCOUNT, usage_type: str = "source_processing") -> dict[str, Any]:
    """
    Holds the source's minutes for this project. Raises QuotaExceeded (nothing recorded) when
    the period does not have them. Idempotent: an existing reservation/commit of this project
    and source is returned as it is; a reservation whose probed length changed is corrected.
    """
    ms = seconds_to_ms(duration_seconds)
    with _LOCK, session_scope() as s:
        _write_lock(s)
        a = account(s, account_id)
        charge = _charge_id(project_id, source_id, s)
        cur = charge_state(s, charge)
        if cur is not None and cur.status in ("reserved", "committed"):
            if cur.status == "committed" or cur.source_duration_ms == ms or ms <= 0:
                return _row(cur)
        rows = _latest(s, a.id, _naive(a.billing_period_start))
        used, held = _totals(rows)
        if cur is not None and cur.status == "reserved":
            held -= cur.source_duration_ms             # replacing this charge's own hold
        avail = plan(a)["minutes"] * MS_PER_MIN - used - held
        if ms > avail:
            raise QuotaExceeded(max(0, avail), ms)
        _append(s, a=a, charge_id=charge, key=f"{charge}:reserved:{ms}", status="reserved", ms=ms,
                project_id=project_id, source_id=source_id, usage_type=usage_type,
                period_start=_naive(cur.period_start) if cur else None)
        return _row(charge_state(s, charge))


def commit(project_id: str, source_id: str, duration_seconds: float = 0.0, *,
           account_id: str = DEFAULT_ACCOUNT) -> Optional[dict[str, Any]]:
    """Processing started: the reserved minutes are used. Idempotent; None if nothing was reserved."""
    with _LOCK, session_scope() as s:
        _write_lock(s)
        a = account(s, account_id)
        charge = _charge_id(project_id, source_id, s)
        cur = charge_state(s, charge)
        if cur is None:
            return None
        if cur.status == "committed":
            return _row(cur)
        if cur.status != "reserved":
            return _row(cur)
        ms = seconds_to_ms(duration_seconds) or cur.source_duration_ms
        _append(s, a=a, charge_id=charge, key=f"{charge}:committed", status="committed", ms=ms,
                project_id=project_id, source_id=source_id, usage_type=cur.usage_type,
                period_start=_naive(cur.period_start))
        return _row(charge_state(s, charge))


def release(project_id: str, source_id: str, *, reason: str,
            account_id: str = DEFAULT_ACCOUNT) -> Optional[dict[str, Any]]:
    """Gives the charge back (appends a 'released' row). Idempotent."""
    with _LOCK, session_scope() as s:
        _write_lock(s)
        a = account(s, account_id)
        base = f"{project_id}:{source_id or '-'}"
        rows = (s.query(UsageEntry).filter(UsageEntry.charge_id.like(f"{base}:%"))
                .order_by(UsageEntry.id.desc()).all())
        if not rows or rows[0].status == "released":
            return _row(rows[0]) if rows else None
        cur = rows[0]
        _append(s, a=a, charge_id=cur.charge_id, key=f"{cur.charge_id}:released", status="released", ms=0,
                project_id=project_id, source_id=source_id, usage_type=cur.usage_type,
                period_start=_naive(cur.period_start), reason=reason)
        return _row(charge_state(s, cur.charge_id))


def adjust(minutes: float, *, note: str, account_id: str = DEFAULT_ACCOUNT,
           key: str = "") -> dict[str, Any]:
    """Manual credit (negative minutes) or debit by an admin, in the current period."""
    with _LOCK, session_scope() as s:
        _write_lock(s)
        a = account(s, account_id)
        cid = f"adj:{key or uuid.uuid4().hex}"
        _append(s, a=a, charge_id=cid, key=f"{cid}:committed", status="committed",
                ms=seconds_to_ms(float(minutes) * 60), usage_type="manual_credit_adjustment", note=note)
        return _row(charge_state(s, cid))


def _row(r: Optional[UsageEntry]) -> Optional[dict[str, Any]]:
    if r is None:
        return None
    return {"charge_id": r.charge_id, "status": r.status, "duration_ms": r.source_duration_ms,
            "usage_type": r.usage_type, "project_id": r.project_id}


# --------------------------------------------------------------------------
# job hooks (pipeline / worker)
# --------------------------------------------------------------------------
def _job_source(job: Job) -> tuple[str, float]:
    arts = job.artifacts or {}
    dur = float((arts.get("source_info") or {}).get("duration") or 0.0)
    sec = arts.get("section") or {}
    if sec and sec.get("end"):
        dur = min(dur or 1e12, float(sec["end"]) - float(sec.get("start") or 0.0))
    return (job.source_id or "-"), dur


def on_processing_start(job_id: str) -> None:
    """Called when the first expensive stage starts. Reserves (if the creation could not, e.g. a
    link of unknown length) and commits. Raises QuotaExceeded when the probed length does not fit."""
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            return
        source_id, dur = _job_source(job)
        account_id = job.account_id or DEFAULT_ACCOUNT
    if dur <= 0:
        return
    reserve(job_id, source_id, dur, account_id=account_id)
    commit(job_id, source_id, dur, account_id=account_id)


def on_job_failed(job_id: str, *, infrastructure: bool) -> None:
    """
    A failure gives the minutes back when processing never started (still reserved), or when
    it was ours (infrastructure) and came before meaningful work – the transcript.
    """
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            return
        source_id = job.source_id or "-"
        account_id = job.account_id or DEFAULT_ACCOUNT
        meaningful = "transcribe" in (job.completed_stages or [])
        cur = (s.query(UsageEntry).filter(UsageEntry.charge_id.like(f"{job_id}:{source_id}:%"))
               .order_by(UsageEntry.id.desc()).first())
        state = cur.status if cur else None
    if state == "reserved":
        release(job_id, source_id, reason="failed_before_processing", account_id=account_id)
    elif state == "committed" and infrastructure and not meaningful:
        release(job_id, source_id, reason="infrastructure_failure", account_id=account_id)


def on_job_cancelled(job_id: str, policy: str) -> None:
    from ..models import Clip, ClipStatus

    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            return
        source_id = job.source_id or "-"
        account_id = job.account_id or DEFAULT_ACCOUNT
        cur = (s.query(UsageEntry).filter(UsageEntry.charge_id.like(f"{job_id}:{source_id}:%"))
               .order_by(UsageEntry.id.desc()).first())
        if cur is None or cur.status == "released":
            return
        has_output = s.query(Clip.id).filter(
            Clip.job_id == job_id, Clip.status.in_([ClipStatus.READY, ClipStatus.NEEDS_REVIEW])).first() is not None
    if cur.status == "reserved" or (policy == "refund_before_output" and not has_output):
        release(job_id, source_id, reason="cancelled", account_id=account_id)


def on_project_deleted(job_id: str, source_id: str, account_id: str = DEFAULT_ACCOUNT) -> None:
    """A project deleted before processing started gives its held minutes back (used ones stay used)."""
    with session_scope() as s:
        cur = (s.query(UsageEntry).filter(UsageEntry.charge_id.like(f"{job_id}:{source_id or '-'}:%"))
               .order_by(UsageEntry.id.desc()).first())
        reserved = cur is not None and cur.status == "reserved"
    if reserved:
        release(job_id, source_id, reason="deleted_before_processing", account_id=account_id)


# --------------------------------------------------------------------------
# views
# --------------------------------------------------------------------------
def customer_summary(account_id: str = DEFAULT_ACCOUNT, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """What the customer sees: plan, period, minutes used / remaining. Nothing else."""
    with session_scope() as s:
        a = account(s, account_id, now=now)
        p = plan(a)
        rows = _latest(s, a.id, _naive(a.billing_period_start))
        used_ms, held_ms = _totals(rows)
        used = round_minutes(used_ms + held_ms)
        titles = dict(s.query(Job.id, Job.title).filter(Job.id.in_({r.project_id for r in rows if r.project_id})).all())
        projects = [{"project_id": r.project_id, "title": titles.get(r.project_id, ""),
                     "minutes": round_minutes(r.source_duration_ms),
                     "status": "processing" if r.status == "reserved" else "used",
                     "type": r.usage_type, "date": r.created_at.isoformat() if r.created_at else None}
                    for r in sorted(rows, key=lambda x: -x.id)
                    if r.status in ("reserved", "committed") and r.usage_type != "manual_credit_adjustment"]
        credits = sum(r.source_duration_ms for r in rows
                      if r.status == "committed" and r.usage_type == "manual_credit_adjustment")
        return {
            "plan": {"code": p["code"], "name": p["name"], "price": p["price"], "currency": p["currency"],
                     "minutes": p["minutes"]},
            "period": {"start": a.billing_period_start.isoformat(), "end": a.billing_period_end.isoformat()},
            "used_minutes": used,
            "remaining_minutes": max(0, p["minutes"] - used),
            # exact values, for a precise check in the interface (no rounding there either)
            "used_ms": used_ms + held_ms, "available_ms": max(0, p["minutes"] * MS_PER_MIN - used_ms - held_ms),
            # admin credits (negative) / debits in this period, already inside used/remaining
            "adjusted_minutes": -round_minutes(-credits) if credits < 0 else round_minutes(credits),
            "projects": projects[:200],
            "rounding": "half_up_on_totals",
        }


def check(duration_seconds: float, account_id: str = DEFAULT_ACCOUNT) -> dict[str, Any]:
    """Would a source of this length fit right now (before a project is created)?"""
    summ = customer_summary(account_id)
    need = seconds_to_ms(duration_seconds)
    return {"fits": need <= summ["available_ms"], "remaining": display_duration(summ["available_ms"]),
            "video": display_duration(need), "remaining_minutes": summ["remaining_minutes"]}


def ledger(account_id: str = DEFAULT_ACCOUNT, limit: int = 500) -> list[dict[str, Any]]:
    """Every row (admin)."""
    with session_scope() as s:
        rows = (s.query(UsageEntry).filter(UsageEntry.account_id == account_id)
                .order_by(UsageEntry.id.desc()).limit(limit).all())
        return [{"id": r.id, "charge_id": r.charge_id, "project_id": r.project_id, "source_id": r.source_id,
                 "source_duration_seconds": r.source_duration_ms / 1000, "usage_type": r.usage_type,
                 "status": r.status, "idempotency_key": r.idempotency_key, "reason": r.reason, "note": r.note,
                 "period_start": r.period_start.isoformat() if r.period_start else None,
                 "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]


def set_plan(code: str, account_id: str = DEFAULT_ACCOUNT) -> dict[str, Any]:
    if code not in PLANS:
        raise ValueError(code)
    with session_scope() as s:
        a = account(s, account_id)
        a.plan_code = code
    return customer_summary(account_id)


def project_minutes(project_id: str) -> dict[str, Any]:
    """Minutes charged for one project (latest state of each of its charges)."""
    with session_scope() as s:
        sub = (s.query(func.max(UsageEntry.id)).filter(UsageEntry.project_id == project_id)
               .group_by(UsageEntry.charge_id))
        rows = s.query(UsageEntry).filter(UsageEntry.id.in_(sub)).all()
        ms = sum(r.source_duration_ms for r in rows if r.status in ("reserved", "committed"))
        state = "released" if rows and all(r.status == "released" for r in rows) else \
            ("committed" if any(r.status == "committed" for r in rows) else ("reserved" if rows else "none"))
        return {"duration_ms": ms, "minutes": round_minutes(ms), "state": state}


def period_window(a: Account) -> tuple[datetime, datetime]:
    return _naive(a.billing_period_start), _naive(a.billing_period_end)


__all__ = ["PLANS", "QuotaExceeded", "reserve", "commit", "release", "adjust", "customer_summary", "check",
           "ledger", "round_minutes", "display_duration", "on_processing_start", "on_job_failed",
           "on_job_cancelled", "on_project_deleted", "project_minutes", "set_plan"]
