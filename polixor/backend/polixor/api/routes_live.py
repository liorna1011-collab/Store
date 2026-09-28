"""
נתיבי API לקליטת שידור חי: זיהוי, התחלת הקלטה, מצב ועצירה.

ההקלטה עצמה רצה בתוך המשימה הרגילה (שלב CAPTURE בפייפליין), ולכן
אין כאן מנוע נפרד – רק שליטה ותצוגת מצב.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import SETTINGS
from ..db import db_dependency
from ..errors import JobNotFoundError, PolixorError
from ..models import LIVE_STATE_LABELS_HE, Job, JobStatus, LiveState
from ..schemas import LiveDetectRequest, LiveDetectResponse, LiveStatusOut
from ..services import live as live_svc
from .routes_jobs import _http

log = logging.getLogger("polixor.api.live")
router = APIRouter(prefix="/api", tags=["live"])


@router.post("/live/detect", response_model=LiveDetectResponse)
def detect(payload: LiveDetectRequest) -> LiveDetectResponse:
    """
    בודק קישור שידור חי ומחזיר מה שידוע עליו.

    אינו מתחיל הקלטה ואינו מוריד מדיה. אם השידור אינו זמין, מוחזר
    `available=false` עם הסיבה – ולא שגיאה, כדי שהממשק יוכל להציג
    את המצב האמיתי במקום להיראות שבור.
    """
    try:
        info = live_svc.detect_stream(payload.url, SETTINGS.get(),
                                      probe_media=payload.probe_media)
    except PolixorError as exc:
        raise _http(exc) from exc

    data = info.to_dict()
    data["ffmpeg_ready"] = live_svc.have_ffmpeg()
    if not data["ffmpeg_ready"]:
        data["notes"] = list(data.get("notes") or []) + [
            "FFmpeg אינו זמין – לא ניתן להקליט שידור ללא FFmpeg."]
        data["available"] = False
        data["reason"] = data["reason"] or "FFmpeg אינו מותקן."
    return LiveDetectResponse(**data)


@router.get("/jobs/{job_id}/live", response_model=LiveStatusOut)
def live_status(job_id: str,
                db: Session = Depends(db_dependency)) -> LiveStatusOut:
    job = db.get(Job, job_id)
    if job is None:
        raise _http(JobNotFoundError())
    return _status_of(job)


@router.post("/jobs/{job_id}/live/stop", response_model=LiveStatusOut)
def stop_capture(job_id: str,
                 db: Session = Depends(db_dependency)) -> LiveStatusOut:
    """
    עוצר הקלטה פעילה.

    זו אינה ביטול המשימה: המקטע הנוכחי נסגר, החומר שנאסף נשמר,
    והמשימה ממשיכה לתמלול, ניתוח ועריכה כרגיל.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise _http(JobNotFoundError())
    if not job.is_live_mode:
        raise HTTPException(status_code=400, detail={
            "code": "not_live", "message": "המשימה אינה במצב שידור חי.",
            "hint": ""})

    from ..pipeline import request_live_stop

    active = request_live_stop(job_id)
    db.expire_all()
    job = db.get(Job, job_id)
    assert job is not None
    out = _status_of(job)
    if not active:
        out.note = ("אין הקלטה פעילה כרגע. הבקשה נרשמה ותיכנס לתוקף "
                    "אם ההקלטה תתחיל.")
    else:
        out.note = "העצירה התקבלה. המקטע הנוכחי נסגר ונשמר."
    return out


def _status_of(job: Job) -> LiveStatusOut:
    from ..pipeline import is_live_capturing

    segments = list(job.live_segments or [])
    state = job.live_state or LiveState.IDLE.value
    return LiveStatusOut(
        job_id=job.id,
        is_live_mode=bool(job.is_live_mode),
        state=state,
        state_label=LIVE_STATE_LABELS_HE.get(state, state),
        capturing=is_live_capturing(job.id),
        stop_requested=bool(job.live_stop_requested),
        captured_seconds=float(job.live_captured_seconds or 0.0),
        segments=len(segments),
        reconnects=int(job.live_reconnects or 0),
        started_at=job.live_started_at,
        error=job.live_error or "",
        job_status=job.status.value if isinstance(job.status, JobStatus)
        else str(job.status),
    )
