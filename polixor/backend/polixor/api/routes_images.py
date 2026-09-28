"""
נתיבי API לאזור AI Images.

כל קריאה לספק התמונות מתבצעת כאן, בצד השרת. מפתח ה-API נקרא מהאחסון
המוצפן או ממשתנה הסביבה POLIXOR_OPENAI_API_KEY, ולעולם אינו מוחזר
ללקוח – גם לא במסכה של תמונה שנוצרה.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..config import SETTINGS
from ..db import db_dependency
from ..errors import PolixorError
from ..models import (
    TIMELINE_ROLES,
    ROLE_LABELS_HE,
    Clip,
    GeneratedImage,
    ImagePlacement,
    ImageStatus,
    Job,
)
from ..schemas import (
    ImageCreateRequest,
    ImageOut,
    ImagePromptPatch,
    ImageProvidersOut,
    PlacementOut,
    PlacementRequest,
    SuggestVisualsOut,
)
from ..services import image_assets as assets
from ..services import images as img_svc
from ..services import visual_suggest as suggest_svc
from .routes_clips import content_disposition
from .routes_jobs import _http

log = logging.getLogger("polixor.api.images")
router = APIRouter(prefix="/api", tags=["images"])


# --------------------------------------------------------------------------
# סריאליזציה
# --------------------------------------------------------------------------
def image_to_out(row: GeneratedImage) -> ImageOut:
    has_file = bool(row.file_path) and Path(row.file_path).is_file()
    return ImageOut(
        id=row.id,
        job_id=row.job_id or "",
        parent_id=row.parent_id or "",
        prompt=row.prompt,
        revised_prompt=row.revised_prompt or "",
        aspect=row.aspect,
        status=row.status.value,
        error=row.error or "",
        error_code=row.error_code or "",
        provider=row.provider or "",
        model=row.model or "",
        is_ai=bool(row.is_ai),
        note=row.note or "",
        width=int(row.width or 0),
        height=int(row.height or 0),
        file_size=int(row.file_size or 0),
        has_file=has_file,
        url=f"/api/images/{row.id}/file" if has_file else "",
        thumb_url=(f"/api/images/{row.id}/thumbnail"
                   if row.thumb_path and Path(row.thumb_path).is_file() else ""),
        created_at=row.created_at,
    )


def placement_to_out(placement: ImagePlacement,
                     image: Optional[GeneratedImage]) -> PlacementOut:
    return PlacementOut(
        id=placement.id,
        clip_id=placement.clip_id,
        image_id=placement.image_id,
        role=placement.role.value,
        role_label=ROLE_LABELS_HE.get(placement.role.value, placement.role.value),
        at_time=float(placement.at_time),
        duration=float(placement.duration),
        opacity=float(placement.opacity),
        scale=float(placement.scale),
        position=placement.position,
        fit=placement.fit,
        enabled=bool(placement.enabled),
        extends_timeline=placement.role.value in TIMELINE_ROLES,
        image=image_to_out(image) if image is not None else None,
    )


def _get_image(db: Session, image_id: str) -> GeneratedImage:
    row = db.get(GeneratedImage, image_id)
    if row is None:
        raise _http(assets.ImageNotFoundError())
    return row


# --------------------------------------------------------------------------
# מצב הספק
# --------------------------------------------------------------------------
@router.get("/images/providers", response_model=ImageProvidersOut)
def image_providers() -> ImageProvidersOut:
    """
    מצב אמיתי של ספקי התמונות. אם אין מפתח – מדווח על כך במפורש,
    כדי שהממשק יוכל להציג מצב חסום במקום כפתור שנראה פעיל.
    """
    st = SETTINGS.get()
    providers = img_svc.provider_status(st)
    selected = next((p for p in providers if p["selected"]), None)
    ready = bool(selected and selected["available"])
    reason = "" if ready else (selected or {}).get("reason", "") or \
        "לא הוגדר ספק תמונות זמין."
    return ImageProvidersOut(providers=providers, selected=st.image_provider,
                             ready=ready, reason=reason)


# --------------------------------------------------------------------------
# יצירה וצפייה
# --------------------------------------------------------------------------
@router.post("/images", response_model=ImageOut, status_code=202)
def create_image(payload: ImageCreateRequest,
                 db: Session = Depends(db_dependency)) -> ImageOut:
    """יוצר בקשת תמונה ומחזיר מיד. ההתקדמות משודרת ב-WebSocket."""
    st = SETTINGS.get()
    job_id = payload.job_id.strip() or None
    if job_id and db.get(Job, job_id) is None:
        raise HTTPException(status_code=404, detail={
            "code": "job_not_found", "message": "המשימה לא נמצאה.", "hint": ""})
    try:
        image_id = assets.create_image_row(
            prompt=payload.prompt, aspect=payload.aspect,
            job_id=job_id, settings=st)
    except PolixorError as exc:
        raise _http(exc) from exc

    assets.start_generation(image_id, settings=st)
    db.expire_all()
    return image_to_out(_get_image(db, image_id))


@router.get("/images", response_model=list[ImageOut])
def list_images(job_id: str = Query(default=""),
                include_library: bool = Query(default=True),
                limit: int = Query(default=200, ge=1, le=500),
                db: Session = Depends(db_dependency)) -> list[ImageOut]:
    q = db.query(GeneratedImage)
    if job_id:
        if include_library:
            q = q.filter((GeneratedImage.job_id == job_id) |
                         (GeneratedImage.job_id.is_(None)))
        else:
            q = q.filter(GeneratedImage.job_id == job_id)
    rows = q.order_by(desc(GeneratedImage.created_at)).limit(limit).all()
    return [image_to_out(r) for r in rows]


@router.get("/images/{image_id}", response_model=ImageOut)
def get_image(image_id: str, db: Session = Depends(db_dependency)) -> ImageOut:
    return image_to_out(_get_image(db, image_id))


@router.get("/images/{image_id}/file")
def image_file(image_id: str, db: Session = Depends(db_dependency)):
    row = _get_image(db, image_id)
    path = Path(row.file_path or "")
    if not path.is_file() or not assets._within_images(path):
        raise HTTPException(status_code=404, detail={
            "code": "image_file_missing", "message": "קובץ התמונה חסר.",
            "hint": "נסה ליצור את התמונה מחדש."})
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=31536000"})


@router.get("/images/{image_id}/thumbnail")
def image_thumbnail(image_id: str, db: Session = Depends(db_dependency)):
    row = _get_image(db, image_id)
    path = Path(row.thumb_path or "")
    if not path.is_file() or not assets._within_images(path):
        raise HTTPException(status_code=404, detail={
            "code": "thumb_missing", "message": "אין תמונה ממוזערת.", "hint": ""})
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=31536000"})


@router.get("/images/{image_id}/download")
def download_image(image_id: str, db: Session = Depends(db_dependency)):
    row = _get_image(db, image_id)
    path = Path(row.file_path or "")
    if not path.is_file() or not assets._within_images(path):
        raise HTTPException(status_code=404, detail={
            "code": "image_file_missing", "message": "קובץ התמונה חסר.", "hint": ""})
    words = " ".join(row.prompt.split()[:6]) or "polixor-image"
    return FileResponse(
        path, media_type="image/png",
        headers={"Content-Disposition": content_disposition(f"{words}.png")})


# --------------------------------------------------------------------------
# פעולות על תמונה
# --------------------------------------------------------------------------
@router.post("/images/{image_id}/regenerate", response_model=ImageOut,
             status_code=202)
def regenerate_image(image_id: str,
                     db: Session = Depends(db_dependency)) -> ImageOut:
    """יוצר תמונה חדשה מאותו פרומפט. המקורית נשמרת."""
    row = _get_image(db, image_id)
    st = SETTINGS.get()
    try:
        new_image_id = assets.create_image_row(
            prompt=row.prompt, aspect=row.aspect, job_id=row.job_id,
            parent_id=row.parent_id or row.id, settings=st)
    except PolixorError as exc:
        raise _http(exc) from exc
    assets.start_generation(new_image_id, settings=st)
    db.expire_all()
    return image_to_out(_get_image(db, new_image_id))


@router.post("/images/{image_id}/variation", response_model=ImageOut,
             status_code=202)
def vary_image(image_id: str, db: Session = Depends(db_dependency)) -> ImageOut:
    """
    וריאציה על תמונה קיימת: התמונה נשלחת לספק כקלט.

    אם הספק אינו תומך בעריכת תמונה, השכבה נופלת חזרה ליצירה מחדש
    מאותו פרומפט, והדבר נרשם ב-meta של התמונה החדשה.
    """
    row = _get_image(db, image_id)
    source = Path(row.file_path or "")
    if not source.is_file():
        raise _http(assets.ImageNotReadyError(
            "אין קובץ מקור ליצירת וריאציה."))
    st = SETTINGS.get()
    try:
        new_image_id = assets.create_image_row(
            prompt=row.prompt, aspect=row.aspect, job_id=row.job_id,
            parent_id=row.id, settings=st)
    except PolixorError as exc:
        raise _http(exc) from exc
    assets.start_generation(new_image_id, source_path=source, settings=st)
    db.expire_all()
    return image_to_out(_get_image(db, new_image_id))


@router.patch("/images/{image_id}", response_model=ImageOut, status_code=202)
def edit_prompt(image_id: str, payload: ImagePromptPatch,
                db: Session = Depends(db_dependency)) -> ImageOut:
    """
    עריכת הפרומפט יוצרת תמונה חדשה – התמונה הקיימת לא משתנה,
    כדי שלא נאבד תוצאה שהמשתמש אולי עדיין רוצה.
    """
    row = _get_image(db, image_id)
    st = SETTINGS.get()
    try:
        new_image_id = assets.create_image_row(
            prompt=payload.prompt, aspect=row.aspect, job_id=row.job_id,
            parent_id=row.parent_id or row.id, settings=st)
    except PolixorError as exc:
        raise _http(exc) from exc
    assets.start_generation(new_image_id, settings=st)
    db.expire_all()
    return image_to_out(_get_image(db, new_image_id))


@router.post("/images/{image_id}/cancel")
def cancel_image(image_id: str,
                 db: Session = Depends(db_dependency)) -> dict[str, Any]:
    _get_image(db, image_id)
    stopped = assets.cancel_generation(image_id)
    return {"cancelled": stopped}


@router.delete("/images/{image_id}")
def delete_image(image_id: str,
                 db: Session = Depends(db_dependency)) -> dict[str, Any]:
    _get_image(db, image_id)
    db.close()
    ok = assets.delete_image(image_id)
    return {"deleted": ok}


# --------------------------------------------------------------------------
# שיבוץ בקליפים
# --------------------------------------------------------------------------
@router.get("/clips/{clip_id}/images", response_model=list[PlacementOut])
def list_placements(clip_id: str,
                    db: Session = Depends(db_dependency)) -> list[PlacementOut]:
    if db.get(Clip, clip_id) is None:
        raise HTTPException(status_code=404, detail={
            "code": "clip_not_found", "message": "הקליפ לא נמצא.", "hint": ""})
    rows = (db.query(ImagePlacement)
            .filter(ImagePlacement.clip_id == clip_id)
            .order_by(ImagePlacement.at_time, ImagePlacement.idx).all())
    return [placement_to_out(p, db.get(GeneratedImage, p.image_id)) for p in rows]


@router.post("/clips/{clip_id}/images", response_model=PlacementOut,
             status_code=201)
def create_placement(clip_id: str, payload: PlacementRequest,
                     db: Session = Depends(db_dependency)) -> PlacementOut:
    try:
        pid = assets.add_placement(
            clip_id=clip_id, image_id=payload.image_id, role=payload.role,
            at_time=payload.at_time, duration=payload.duration,
            opacity=payload.opacity, scale=payload.scale,
            position=payload.position, fit=payload.fit)
    except PolixorError as exc:
        raise _http(exc) from exc
    db.expire_all()
    row = db.get(ImagePlacement, pid)
    assert row is not None
    return placement_to_out(row, db.get(GeneratedImage, row.image_id))


@router.delete("/clips/{clip_id}/images/{placement_id}")
def delete_placement(clip_id: str, placement_id: str,
                     db: Session = Depends(db_dependency)) -> dict[str, Any]:
    row = db.get(ImagePlacement, placement_id)
    if row is None or row.clip_id != clip_id:
        raise HTTPException(status_code=404, detail={
            "code": "placement_not_found", "message": "השיבוץ לא נמצא.",
            "hint": ""})
    db.close()
    return {"deleted": assets.remove_placement(placement_id)}


# --------------------------------------------------------------------------
# Suggest Visuals
# --------------------------------------------------------------------------
@router.post("/clips/{clip_id}/suggest-visuals", response_model=SuggestVisualsOut)
def suggest_visuals_for_clip(clip_id: str, limit: int = Query(default=5, ge=1, le=12),
                             db: Session = Depends(db_dependency)) -> SuggestVisualsOut:
    """
    מציע נקודות שבהן תמונה תחזק את הסרטון.

    ההצעות אינן מיושמות אוטומטית: הן מוחזרות בלבד, והמשתמש בוחר
    אילו להפיק ואילו לשבץ.
    """
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail={
            "code": "clip_not_found", "message": "הקליפ לא נמצא.", "hint": ""})
    try:
        result = suggest_svc.suggest_for_clip(db, clip, limit=limit,
                                              settings=SETTINGS.get())
    except PolixorError as exc:
        raise _http(exc) from exc
    return SuggestVisualsOut(**result)
