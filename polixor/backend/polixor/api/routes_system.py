"""נתיבי API להגדרות, סודות, בדיקת מערכת ואזור מצלמה."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import APP_VERSION, PATHS, SECRETS, SETTINGS, system_report
from ..db import db_dependency
from ..models import Clip, Job, StageTiming
from ..schemas import SecretIn, SettingsOut, SystemOut
from ..services import llm
from ..util.fs import dir_size, human_size

log = logging.getLogger("polixor.api.system")
router = APIRouter(prefix="/api", tags=["system"])

SECRET_NAMES = ("anthropic_api_key", "openai_api_key", "cookiefile_path")


# --------------------------------------------------------------------------
# הגדרות
# --------------------------------------------------------------------------
@router.get("/settings", response_model=SettingsOut)
def get_settings() -> SettingsOut:
    s = SETTINGS.get()
    return SettingsOut(
        values=s.to_dict(),
        secrets={name: {"configured": SECRETS.has(name),
                        "masked": SECRETS.mask(name) or ""}
                 for name in SECRET_NAMES},
        ai_status=llm.check_availability(s),
    )


@router.put("/settings", response_model=SettingsOut)
def update_settings(patch: dict[str, Any]) -> SettingsOut:
    """
    עדכון הגדרות. סודות אינם מתקבלים כאן – יש נתיב נפרד,
    כדי שמפתחות API לא יישמרו בטעות בקובץ ההגדרות הרגיל.
    """
    clean = {k: v for k, v in (patch or {}).items()
             if k not in SECRET_NAMES and not k.endswith("_api_key")}
    s = SETTINGS.update(clean)
    return SettingsOut(
        values=s.to_dict(),
        secrets={name: {"configured": SECRETS.has(name),
                        "masked": SECRETS.mask(name) or ""}
                 for name in SECRET_NAMES},
        ai_status=llm.check_availability(s),
    )


@router.post("/settings/secrets")
def set_secret(payload: SecretIn) -> dict[str, Any]:
    """
    שמירת מפתח API. הערך מוצפן בדיסק ולעולם לא מוחזר ל-API.
    המשתמש רואה רק מסכה (4 תווים אחרונים).
    """
    name = payload.name.strip()
    if name not in SECRET_NAMES:
        raise HTTPException(status_code=400, detail={
            "code": "unknown_secret",
            "message": f"שם סוד לא מוכר: {name}",
            "hint": "מותר: " + ", ".join(SECRET_NAMES)})

    value = payload.value.strip()
    if name == "cookiefile_path" and value and not Path(value).exists():
        raise HTTPException(status_code=400, detail={
            "code": "file_missing",
            "message": "קובץ העוגיות שצוין לא נמצא.",
            "hint": "יש לספק נתיב מלא לקובץ cookies.txt שייצאת בעצמך."})

    SECRETS.set(name, value)
    return {"saved": True, "name": name, "configured": SECRETS.has(name),
            "masked": SECRETS.mask(name) or ""}


@router.delete("/settings/secrets/{name}")
def delete_secret(name: str) -> dict[str, Any]:
    if name not in SECRET_NAMES:
        raise HTTPException(status_code=400, detail={
            "code": "unknown_secret", "message": "שם סוד לא מוכר.", "hint": ""})
    SECRETS.delete(name)
    return {"deleted": True, "name": name}


@router.post("/settings/ai/test")
def test_ai() -> dict[str, Any]:
    """בדיקה חיה של חיבור ה-AI, בבקשה קצרה אחת."""
    s = SETTINGS.get()
    status = llm.check_availability(s)
    if status.get("mode") == "heuristic":
        return {**status, "tested": False,
                "message": "מצב היוריסטי מקומי – אין מה לבדוק, הוא תמיד זמין."}
    try:
        raw = llm.call_model(
            "החזר JSON בלבד.",
            'ענה בדיוק: {"ok": true}',
            s,
        )
        ok = "ok" in (raw or "").lower()
        return {**status, "tested": True, "success": ok,
                "message": "החיבור תקין." if ok else "התקבלה תשובה לא צפויה.",
                "sample": (raw or "")[:200]}
    except Exception as exc:  # noqa: BLE001
        detail = getattr(exc, "message", None) or str(exc)
        return {**status, "tested": True, "success": False, "message": detail}


@router.post("/settings/camera-region")
def save_camera_region(region: dict[str, float]) -> dict[str, Any]:
    """
    שמירת אזור מצלמת הסטרימר להגדרות, כך שישמש כברירת מחדל
    בשידורים הבאים של אותו סטרימר.
    """
    try:
        clean = {k: float(region[k]) for k in ("x", "y", "w", "h")}
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail={
            "code": "bad_region",
            "message": "אזור המצלמה חייב לכלול x, y, w, h בין 0 ל-1.", "hint": ""})
    for k, v in clean.items():
        if not 0.0 <= v <= 1.0:
            raise HTTPException(status_code=400, detail={
                "code": "bad_region",
                "message": f"הערך {k}={v} חורג מהטווח 0..1.", "hint": ""})
    SETTINGS.update({"camera_region": clean})
    return {"saved": True, "camera_region": clean}


# --------------------------------------------------------------------------
# מערכת
# --------------------------------------------------------------------------
@router.get("/edit/styles")
def edit_styles() -> dict[str, Any]:
    """
    קטלוג סגנונות העריכה – מה כל אחד עושה. הממשק מציג אותו
    במסך ההגדרות ובמסך עריכת הקליפ.
    """
    from ..services import editing

    return {"styles": editing.style_catalog(),
            "caption_animations": [
                {"name": "none", "label": "ללא", "description": "המילה הפעילה רק מחליפה צבע."},
                {"name": "pop", "label": "קפיצה", "description": "קפיצה קצרה של 8% שחוזרת לגודל המקורי."},
                {"name": "punch", "label": "חזקה", "description": "קפיצה של 18% עם התעבות מתאר. לשורטים."},
            ]}


@router.get("/system", response_model=SystemOut)
def get_system() -> SystemOut:
    report = system_report()
    warnings: list[str] = []

    if not report["ffmpeg"]["available"]:
        warnings.append("FFmpeg לא נמצא – לא ניתן לעבד וידאו. "
                        "הרץ scripts/install_windows.ps1 או התקן ידנית.")
    if not report["modules"]["yt_dlp"]["available"]:
        warnings.append("yt-dlp לא מותקן – ייבוא מקישור לא יעבוד. "
                        "ניתן עדיין להעלות קבצים מהמחשב.")
    if not report["modules"]["faster_whisper"]["available"]:
        warnings.append("faster-whisper לא מותקן – לא יהיה תמלול ולא כתוביות.")
    if not report["modules"]["cv2"]["available"]:
        warnings.append("OpenCV לא מותקן – אין ניתוח חזותי ואין מעקב פנים.")

    free = report["free_disk_bytes"]
    if free and free < 5 * 1024 ** 3:
        warnings.append(f"נותרו רק {human_size(free)} פנויים בדיסק. "
                        "עיבוד שידור ארוך עלול להיכשל.")

    return SystemOut(**report, warnings=warnings)


@router.get("/system/storage")
def storage_report(db: Session = Depends(db_dependency)) -> dict[str, Any]:
    """שימוש בדיסק לפי סוג – מאפשר למשתמש לנקות בצורה מושכלת."""
    sources = dir_size(PATHS.sources)
    work = dir_size(PATHS.work)
    exports = dir_size(PATHS.exports)
    custom = SETTINGS.get().resolved_export_dir()
    custom_size = dir_size(custom) if custom != PATHS.exports else 0

    return {
        "sources_bytes": sources, "sources_human": human_size(sources),
        "work_bytes": work, "work_human": human_size(work),
        "exports_bytes": exports, "exports_human": human_size(exports),
        "custom_export_dir": str(custom),
        "custom_export_bytes": custom_size,
        "free_bytes": PATHS.free_bytes(),
        "free_human": human_size(PATHS.free_bytes()),
        "job_count": db.query(func.count(Job.id)).scalar() or 0,
        "clip_count": db.query(func.count(Clip.id)).scalar() or 0,
    }


@router.post("/system/cleanup")
def cleanup_work_files(db: Session = Depends(db_dependency)) -> dict[str, Any]:
    """
    מוחק קבצי עבודה זמניים (אודיו מחולץ, תמלול, נתוני ניתוח) של משימות
    שהסתיימו. קובצי המקור והקליפים המיוצאים נשמרים.
    """
    from ..models import JobStatus
    from ..util.fs import rmtree_quiet

    finished = (db.query(Job)
                .filter(Job.status.in_([JobStatus.COMPLETED, JobStatus.FAILED,
                                        JobStatus.CANCELLED]))
                .all())
    before = dir_size(PATHS.work)
    removed = 0
    for job in finished:
        d = PATHS.work / job.id
        if d.exists():
            rmtree_quiet(d)
            removed += 1
    after = dir_size(PATHS.work)
    return {"jobs_cleaned": removed, "freed_bytes": max(0, before - after),
            "freed_human": human_size(max(0, before - after))}


@router.get("/system/benchmarks")
def benchmarks(db: Session = Depends(db_dependency)) -> dict[str, Any]:
    """
    מדידות זמן אמיתיות שנאספו – הבסיס היחיד להערכות זמן בממשק.
    אם אין מדידות, לא מוצגת שום הערכה.
    """
    rows = (db.query(StageTiming.stage,
                     func.sum(StageTiming.seconds),
                     func.sum(StageTiming.media_seconds),
                     func.count(StageTiming.id))
            .group_by(StageTiming.stage).all())
    data = []
    for stage, secs, media, n in rows:
        data.append({
            "stage": stage, "samples": int(n),
            "total_seconds": round(float(secs or 0), 2),
            "media_seconds": round(float(media or 0), 2),
            "ratio": round(float(secs) / float(media), 4)
            if media and media > 0 else None,
        })
    return {"version": APP_VERSION, "stages": data,
            "has_data": any(d["ratio"] is not None for d in data)}
