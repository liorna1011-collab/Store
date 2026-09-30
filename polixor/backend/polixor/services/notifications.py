"""
התראות למשתמש: עיבוד שהסתיים, קליפים מוכנים, ייצוא, פרסום ותזמון,
כשלים, וחשבון שצריך להתחבר מחדש.

  * הטקסט נגזר בזמן ההצגה מ-`kind` + `params` (locales/notifications.py),
    ולכן כל התראה מוצגת בעברית ובאנגלית באותה מידה.
  * קיבוץ: התראה חדשה עם אותו `group_key` כמו התראה שעוד לא נקראה מעדכנת
    אותה (count + 1, זמן עדכון) במקום להוסיף שורה – למשל כמה ייצואים
    חוזרים של אותו קליפ, או כמה כשלי פרסום לאותו חשבון.
  * מצבים לתצוגה: needs_attention (שגיאה/אזהרה שלא נקראה), new (לא נקראה),
    earlier (נקראה).
  * כל יצירה/עדכון משודרים ב-WebSocket (`notification`), כך שהפעמון
    מתעדכן מיד.
  * שמירה: עד MAX_ROWS התראות; נקראות ישנות מ-KEEP_DAYS נמחקות.
  * כשל ביצירת התראה לעולם לא מפיל את הפעולה שיצרה אותה.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Iterable, Optional

from .. import i18n
from ..db import session_scope
from ..events import BUS
from ..models import Notification, utcnow

log = logging.getLogger("polixor.notifications")

MAX_ROWS = 500
KEEP_DAYS = 60

# סוג → רמה ברירת מחדל
KINDS: dict[str, str] = {
    "analysis_complete": "success",
    "clips_ready": "success",
    "clips_ready_review": "warning",     # מוכנים, חלקם דורשים בדיקה
    "no_clips": "warning",               # העיבוד הסתיים בלי קליפים שעברו את הרף
    "render_complete": "success",
    "processing_failed": "error",
    "render_failed": "error",
    "live_failed": "error",
    "publish_complete": "success",
    "publish_scheduled": "info",
    "publish_failed": "error",
    "schedule_failed": "error",
    "account_reconnect": "warning",      # טוקן פג/בוטל – צריך להתחבר מחדש
}
LEVELS = ("info", "success", "warning", "error")


def notify(kind: str, *, params: Optional[dict[str, Any]] = None,
           error: Optional[dict[str, Any]] = None, link: str = "", job_id: str = "",
           clip_id: str = "", account_id: str = "", group_key: str = "",
           level: str = "") -> Optional[int]:
    """יוצר (או מקבץ) התראה. מחזיר את המזהה, או None אם נכשל."""
    if kind not in KINDS:
        raise ValueError(f"unknown notification kind: {kind}")
    level = level if level in LEVELS else KINDS[kind]
    params = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        with session_scope() as s:
            row = None
            if group_key:
                row = (s.query(Notification)
                       .filter(Notification.group_key == group_key,
                               Notification.read_at.is_(None))
                       .order_by(Notification.id.desc()).first())
            now = utcnow()
            if row is not None:
                row.count = int(row.count or 1) + 1
                row.params, row.error, row.level = params, error or {}, level
                row.link = link or row.link
                row.updated_at = now
            else:
                row = Notification(kind=kind, level=level, group_key=group_key, count=1,
                                   params=params, error=error or {}, link=link,
                                   job_id=job_id, clip_id=clip_id, account_id=account_id,
                                   created_at=now, updated_at=now)
                s.add(row)
            s.flush()
            nid = int(row.id)
            _prune(s)
        BUS.emit("notification", job_id, id=nid, kind=kind, level=level)
        return nid
    except Exception as exc:                           # noqa: BLE001
        log.warning("notification %s not stored: %s", kind, exc)
        return None


def _prune(s: Any) -> None:
    cutoff = utcnow() - timedelta(days=KEEP_DAYS)
    s.query(Notification).filter(Notification.read_at.isnot(None),
                                 Notification.updated_at < cutoff).delete()
    total = s.query(Notification).count()
    if total > MAX_ROWS:
        old = (s.query(Notification.id).order_by(Notification.updated_at.asc())
               .limit(total - MAX_ROWS).all())
        s.query(Notification).filter(Notification.id.in_([o[0] for o in old])) \
            .delete(synchronize_session=False)


# --------------------------------------------------------------------------
# תצוגה
# --------------------------------------------------------------------------
def state_of(n: Notification) -> str:
    if n.read_at is None:
        return "needs_attention" if n.level in ("error", "warning") else "new"
    return "earlier"


def _text(n: Notification, part: str) -> str:
    params = {k: v for k, v in (n.params or {}).items()}
    params.setdefault("count", n.count)
    return i18n.tr(f"notifications.{n.kind}.{part}", **params)


def render(n: Notification) -> dict[str, Any]:
    from ..errors import PolixorError

    body = _text(n, "body")
    err = PolixorError.localize_record(n.error) if n.error else None
    if err and err.get("message"):
        body = f"{body} {err['message']}".strip()
    return {
        "id": n.id, "kind": n.kind, "level": n.level, "state": state_of(n),
        "title": _text(n, "title"), "body": body,
        "hint": (err or {}).get("hint") or "",
        "count": int(n.count or 1), "link": n.link or "",
        "job_id": n.job_id or "", "clip_id": n.clip_id or "",
        "created_at": n.created_at.isoformat() + "Z" if n.created_at else None,
        "updated_at": n.updated_at.isoformat() + "Z" if n.updated_at else None,
        "read": n.read_at is not None,
    }


def listing(limit: int = 60, unread_only: bool = False) -> dict[str, Any]:
    with session_scope() as s:
        q = s.query(Notification)
        if unread_only:
            q = q.filter(Notification.read_at.is_(None))
        rows = q.order_by(Notification.updated_at.desc(), Notification.id.desc()) \
            .limit(max(1, min(200, limit))).all()
        items = [render(n) for n in rows]
        unread = s.query(Notification).filter(Notification.read_at.is_(None)).count()
        attention = s.query(Notification).filter(
            Notification.read_at.is_(None),
            Notification.level.in_(("error", "warning"))).count()
    groups: dict[str, list[dict[str, Any]]] = {"needs_attention": [], "new": [], "earlier": []}
    for it in items:
        groups[it["state"]].append(it)
    return {"unread": unread, "needs_attention": attention, "items": items,
            "groups": [{"state": k, "title": i18n.tr(f"notifications.group.{k}"),
                        "items": v} for k, v in groups.items() if v]}


def mark_read(ids: Optional[Iterable[int]] = None) -> int:
    """מסמן כנקראו (הכול כש-ids=None). מחזיר כמה עודכנו."""
    with session_scope() as s:
        q = s.query(Notification).filter(Notification.read_at.is_(None))
        if ids is not None:
            q = q.filter(Notification.id.in_(list(ids)))
        n = q.update({Notification.read_at: utcnow()}, synchronize_session=False)
    if n:
        BUS.emit("notification", "", read=n)
    return int(n)


def delete(ids: Optional[Iterable[int]] = None, *, read_only: bool = False) -> int:
    with session_scope() as s:
        q = s.query(Notification)
        if ids is not None:
            q = q.filter(Notification.id.in_(list(ids)))
        if read_only:
            q = q.filter(Notification.read_at.isnot(None))
        n = q.delete(synchronize_session=False)
    if n:
        BUS.emit("notification", "", deleted=n)
    return int(n)


# --------------------------------------------------------------------------
# אירועי העיבוד (נקראים מה-worker, מהפייפליין ומהייצוא החוזר)
# --------------------------------------------------------------------------
def job_finished(job_id: str) -> None:
    """ריצה שהסתיימה בהצלחה: ניתוח הסתיים / קליפים מוכנים / אין קליפים."""
    from ..models import Clip, ClipStatus, Job, RunScope

    try:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            title = job.title or ""
            scope = job.run_scope or RunScope.ALL.value
            link = f"/projects/{job_id}"
            if scope == RunScope.ANALYZE.value:
                kind, params = "analysis_complete", {"project": title}
            else:
                clips = s.query(Clip).filter(Clip.job_id == job_id).all()
                ready = [c for c in clips if c.status in (ClipStatus.READY, ClipStatus.NEEDS_REVIEW)]
                review = [c for c in clips if c.status == ClipStatus.NEEDS_REVIEW]
                if not ready:
                    kind, params = "no_clips", {"project": title}
                else:
                    kind = "clips_ready_review" if review else "clips_ready"
                    params = {"project": title, "clips": len(ready), "review": len(review)}
        notify(kind, params=params, link=link, job_id=job_id, group_key=f"job:{job_id}:{kind}")
    except Exception as exc:                           # noqa: BLE001
        log.warning("job notification failed: %s", exc)


def job_failed(job_id: str, error: Optional[dict[str, Any]] = None) -> None:
    from ..models import Job

    try:
        with session_scope() as s:
            job = s.get(Job, job_id)
            title = job.title if job is not None else ""
        notify("processing_failed", params={"project": title}, error=error or {},
               link=f"/projects/{job_id}", job_id=job_id,
               group_key=f"job:{job_id}:failed")
    except Exception as exc:                           # noqa: BLE001
        log.warning("job failure notification failed: %s", exc)
