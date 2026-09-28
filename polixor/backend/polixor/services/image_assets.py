"""
ניהול נכסי התמונות: יצירה אסינכרונית, שמירה ב-DB ושיבוץ בקליפים.

שכבה זו מבודדת במכוון ממנוע העריכה: כל כשל ביצירת תמונה נתפס כאן,
נרשם על השורה ב-DB ומשודר ללקוח – ולעולם אינו מפיל משימת עריכה,
רינדור או תמלול. אם שירות התמונות אינו זמין, שאר המוצר ממשיך לעבוד.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from .. import i18n
from ..config import PATHS, SETTINGS, AppSettings
from ..db import session_scope
from ..errors import ClipNotFoundError, PolixorError
from ..events import BUS
from ..models import (
    COMPOSITE_ROLES,
    TIMELINE_ROLES,
    Clip,
    GeneratedImage,
    ImagePlacement,
    ImageRole,
    ImageStatus,
    new_id,
)
from . import images as img_svc

log = logging.getLogger("polixor.images.assets")

# מקסימום זמן לתמונה אחת בציר הזמן. מעבר לזה הצופה מאבד עניין,
# והמגבלה גם מונעת תמונה שמאריכה קליפ בלי גבול.
MAX_PLACEMENT_SECONDS = 15.0
MIN_PLACEMENT_SECONDS = 0.3
PRESET_DURATIONS = (2.0, 3.0, 5.0)

POSITIONS = ("center", "top_left", "top_right", "bottom_left", "bottom_right")


class ImageNotFoundError(PolixorError):
    code = "image_not_found"
    message = "התמונה לא נמצאה."


class ImageNotReadyError(PolixorError):
    code = "image_not_ready"
    message = "התמונה עדיין לא מוכנה לשימוש."
    hint = "המתן לסיום היצירה, או צור אותה מחדש אם היא נכשלה."


class InvalidRoleError(PolixorError):
    code = "invalid_role"
    message = "תפקיד תמונה לא מוכר."


class InvalidPlacementError(PolixorError):
    code = "invalid_placement"
    message = "לא ניתן לשבץ את התמונה בנקודה הזו."


class ImageWorkers:
    """בריכת תהליכונים קטנה ליצירת תמונות, עם ביטול לכל תמונה."""

    def __init__(self, workers: int = 2) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers,
                                        thread_name_prefix="pximg")
        self._cancels: dict[str, threading.Event] = {}
        self._lock = threading.RLock()

    def submit(self, image_id: str, fn, *args: Any, **kwargs: Any) -> None:
        cancel = threading.Event()
        with self._lock:
            self._cancels[image_id] = cancel
        # התהליכון לא יורש את השפה של הבקשה, ולכן היא עוברת במפורש
        lang = i18n.get_lang()
        self._pool.submit(self._run, image_id, cancel, lang, fn, args, kwargs)

    def _run(self, image_id: str, cancel: threading.Event, lang: str, fn, args,
             kwargs) -> None:
        with i18n.use_lang(lang):
            self._run_in_lang(image_id, cancel, fn, args, kwargs)

    def _run_in_lang(self, image_id: str, cancel: threading.Event, fn, args,
                     kwargs) -> None:
        try:
            fn(image_id, cancel, *args, **kwargs)
        except Exception:                      # pragma: no cover - רשת ביטחון
            log.exception("image worker crashed for %s", image_id)
            _fail(image_id, "image_failed", i18n.tr("images.status.crashed"))
        finally:
            with self._lock:
                self._cancels.pop(image_id, None)

    def cancel(self, image_id: str) -> bool:
        with self._lock:
            ev = self._cancels.get(image_id)
        if ev is None:
            return False
        ev.set()
        return True

    def is_running(self, image_id: str) -> bool:
        with self._lock:
            return image_id in self._cancels

    def shutdown(self, wait: bool = False) -> None:
        with self._lock:
            for ev in self._cancels.values():
                ev.set()
        self._pool.shutdown(wait=wait)


WORKERS = ImageWorkers()


# --------------------------------------------------------------------------
# יצירה
# --------------------------------------------------------------------------
def create_image_row(*, prompt: str, aspect: str, job_id: Optional[str] = None,
                     parent_id: Optional[str] = None,
                     settings: Optional[AppSettings] = None) -> str:
    """
    מאמת קלט ויוצר שורה במצב 'בתור'. זורק PolixorError על קלט לא חוקי,
    לפני שנוצרה שורה כלשהי – כדי שלא יישארו רשומות זבל.
    """
    st = settings or SETTINGS.get()
    prompt = img_svc.validate_prompt(prompt)
    aspect = img_svc.validate_aspect(aspect)

    image_id = new_id()
    with session_scope() as s:
        s.add(GeneratedImage(
            id=image_id, job_id=job_id or None, parent_id=parent_id or None,
            prompt=prompt, aspect=aspect, status=ImageStatus.QUEUED,
            provider=st.image_provider, model=st.image_model,
            is_ai=img_svc.provider_is_ai(st.image_provider),
        ))
    _emit(image_id, "image.queued", job_id=job_id or "", prompt=prompt, aspect=aspect)
    return image_id


def start_generation(image_id: str, *, source_path: Optional[Path] = None,
                     settings: Optional[AppSettings] = None) -> None:
    """מפעיל יצירה ברקע. `source_path` מלא => וריאציה על תמונה קיימת."""
    st = settings or SETTINGS.get()
    WORKERS.submit(image_id, _generate_worker, st, source_path)


def _generate_worker(image_id: str, cancel: threading.Event,
                     settings: AppSettings, source_path: Optional[Path]) -> None:
    with session_scope() as s:
        row = s.get(GeneratedImage, image_id)
        if row is None:
            return
        prompt, aspect, job_id = row.prompt, row.aspect, row.job_id or ""
        row.status = ImageStatus.GENERATING
        row.error = ""
        row.error_code = ""

    _emit(image_id, "image.generating", job_id=job_id,
          message=i18n.tr("images.progress.analyzing"))

    try:
        data = img_svc.generate_image(
            prompt, aspect=aspect, settings=settings,
            cancel_event=cancel, source=source_path,
            on_progress=lambda note: _emit(
                image_id, "image.progress", job_id=job_id, message=note),
        )
    except img_svc.ImageCancelledError:
        _set_status(image_id, ImageStatus.CANCELLED, code="image_cancelled",
                    message=i18n.tr("images.status.cancelled"))
        _emit(image_id, "image.cancelled", job_id=job_id)
        return
    except PolixorError as exc:
        _fail(image_id, exc.code, exc.message, hint=getattr(exc, "hint", ""))
        return
    except Exception as exc:                   # pragma: no cover - רשת ביטחון
        log.exception("unexpected image failure")
        _fail(image_id, "image_failed", i18n.tr("images.status.failed", error=exc))
        return

    try:
        saved = img_svc.save_image(data, image_id)
    except OSError as exc:
        _fail(image_id, "image_failed", i18n.tr("images.status.save_failed", error=exc))
        return

    with session_scope() as s:
        row = s.get(GeneratedImage, image_id)
        if row is None:
            return
        row.status = ImageStatus.READY
        row.file_path = saved["file_path"]
        row.thumb_path = saved["thumb_path"]
        row.file_size = saved["size_bytes"]
        row.width = saved["width"]
        row.height = saved["height"]
        row.provider = data.provider
        row.model = data.model
        row.is_ai = data.is_ai
        row.note = data.note
        row.revised_prompt = data.revised_prompt or ""
        row.meta = dict(data.meta or {})
        job_id = row.job_id or ""

    _emit(image_id, "image.ready", job_id=job_id, message=i18n.tr("images.status.ready"))


def cancel_generation(image_id: str) -> bool:
    return WORKERS.cancel(image_id)


def _set_status(image_id: str, status: ImageStatus, *, code: str = "",
                message: str = "") -> None:
    with session_scope() as s:
        row = s.get(GeneratedImage, image_id)
        if row is None:
            return
        row.status = status
        row.error_code = code
        row.error = message


def _fail(image_id: str, code: str, message: str, hint: str = "") -> None:
    job_id = ""
    with session_scope() as s:
        row = s.get(GeneratedImage, image_id)
        if row is not None:
            row.status = ImageStatus.FAILED
            row.error_code = code
            row.error = message
            job_id = row.job_id or ""
    log.warning("image %s failed [%s]: %s", image_id, code, message)
    _emit(image_id, "image.failed", job_id=job_id, code=code,
          message=message, hint=hint)


def _emit(image_id: str, type_: str, *, job_id: str = "", **data: Any) -> None:
    try:
        BUS.emit(type_, job_id=job_id, image_id=image_id, **data)
    except Exception:                          # pragma: no cover
        log.debug("event publish failed", exc_info=True)


# --------------------------------------------------------------------------
# מחיקה
# --------------------------------------------------------------------------
def delete_image(image_id: str) -> bool:
    """מוחק שורה, קבצים וכל שיבוץ שמצביע עליה."""
    WORKERS.cancel(image_id)
    paths: list[str] = []
    with session_scope() as s:
        row = s.get(GeneratedImage, image_id)
        if row is None:
            return False
        paths = [p for p in (row.file_path, row.thumb_path) if p]
        (s.query(ImagePlacement)
          .filter(ImagePlacement.image_id == image_id).delete())
        s.delete(row)
    for p in paths:
        try:
            path = Path(p)
            if path.is_file() and _within_images(path):
                path.unlink()
        except OSError:
            log.warning("could not delete image file %s", p)
    return True


def _within_images(path: Path) -> bool:
    try:
        path.resolve().relative_to(PATHS.images.resolve())
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------
# שיבוץ בקליפ
# --------------------------------------------------------------------------
def validate_placement(*, role: str, at_time: float, duration: float,
                       clip_duration: float) -> tuple[ImageRole, float, float]:
    """
    מאמת תפקיד, זמן ומשך מול אורך הקליפ בפועל.

    זמנים נמדדים על ציר הפלט הערוך – מה שהמשתמש רואה בנגן.
    """
    try:
        role_enum = ImageRole(str(role))
    except ValueError as exc:
        raise InvalidRoleError(
            message_key="images.error.bad_role", hint_key="images.error.bad_role_hint",
            params={"role": role, "allowed": ", ".join(r.value for r in ImageRole)},
        ) from exc

    if role_enum is ImageRole.THUMBNAIL:
        return role_enum, 0.0, 0.0

    dur = float(duration or 0.0)
    if role_enum is ImageRole.BACKGROUND:
        # רקע פרוס על כל הקליפ
        return role_enum, 0.0, max(0.0, clip_duration)

    if dur <= 0:
        dur = 3.0
    dur = min(MAX_PLACEMENT_SECONDS, max(MIN_PLACEMENT_SECONDS, dur))

    at = float(at_time or 0.0)
    if role_enum is ImageRole.INTRO:
        at = 0.0
    elif role_enum is ImageRole.OUTRO:
        at = max(0.0, clip_duration)
    else:
        at = min(max(0.0, clip_duration), max(0.0, at))
        if role_enum in (ImageRole.BROLL, ImageRole.OVERLAY):
            # שכבות אינן מאריכות את הקליפ, ולכן נחתכות בסופו
            room = clip_duration - at
            if room < MIN_PLACEMENT_SECONDS:
                raise InvalidPlacementError(message_key="images.error.no_room",
                                            hint_key="images.error.no_room_hint")
            dur = min(dur, room)
    return role_enum, at, dur


def add_placement(*, clip_id: str, image_id: str, role: str,
                  at_time: float = 0.0, duration: float = 3.0,
                  opacity: float = 1.0, scale: float = 1.0,
                  position: str = "center", fit: str = "cover") -> str:
    with session_scope() as s:
        clip = s.get(Clip, clip_id)
        if clip is None:
            raise ClipNotFoundError()
        image = s.get(GeneratedImage, image_id)
        if image is None:
            raise ImageNotFoundError()
        if image.status != ImageStatus.READY or not image.file_path:
            raise ImageNotReadyError()
        if not Path(image.file_path).is_file():
            raise ImageNotReadyError(message_key="errors.image_file_missing_disk.message")

        role_enum, at, dur = validate_placement(
            role=role, at_time=at_time, duration=duration,
            clip_duration=float(clip.duration or 0.0))

        if role_enum is ImageRole.BACKGROUND:
            # רקע אחד לכל קליפ
            (s.query(ImagePlacement)
              .filter(ImagePlacement.clip_id == clip_id,
                      ImagePlacement.role == ImageRole.BACKGROUND).delete())

        idx = (s.query(ImagePlacement)
                .filter(ImagePlacement.clip_id == clip_id).count())
        pid = new_id()
        s.add(ImagePlacement(
            id=pid, clip_id=clip_id, image_id=image_id, role=role_enum,
            at_time=at, duration=dur,
            opacity=min(1.0, max(0.05, float(opacity))),
            scale=min(1.0, max(0.05, float(scale))),
            position=position if position in POSITIONS else "center",
            fit=fit if fit in ("cover", "contain") else "cover",
            idx=idx,
        ))
    return pid


def remove_placement(placement_id: str) -> bool:
    with session_scope() as s:
        row = s.get(ImagePlacement, placement_id)
        if row is None:
            return False
        s.delete(row)
    return True


def placements_for_clip(session, clip_id: str) -> list[dict[str, Any]]:
    """
    שיבוצים של קליפ, ממוינים לפי הזמן, עם נתיב הקובץ בפועל.

    שיבוץ שהתמונה שלו נמחקה מהדיסק מדולג בשקט – עדיף קליפ בלי
    התמונה מאשר רינדור שנכשל.
    """
    rows = (session.query(ImagePlacement, GeneratedImage)
            .join(GeneratedImage, ImagePlacement.image_id == GeneratedImage.id)
            .filter(ImagePlacement.clip_id == clip_id,
                    ImagePlacement.enabled.is_(True))
            .order_by(ImagePlacement.at_time, ImagePlacement.idx).all())
    out: list[dict[str, Any]] = []
    for placement, image in rows:
        path = Path(image.file_path or "")
        if not path.is_file():
            log.warning("placement %s skipped: image file missing", placement.id)
            continue
        out.append({
            "id": placement.id,
            "image_id": image.id,
            "role": placement.role.value,
            "at_time": float(placement.at_time),
            "duration": float(placement.duration),
            "opacity": float(placement.opacity),
            "scale": float(placement.scale),
            "position": placement.position,
            "fit": placement.fit,
            "path": str(path),
            "width": int(image.width or 0),
            "height": int(image.height or 0),
            "prompt": image.prompt,
            "is_ai": bool(image.is_ai),
        })
    return out


def split_placements(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]],
                                                           list[dict[str, Any]]]:
    """מפריד בין שיבוצים שמאריכים את ציר הזמן לשיבוצים שמצוירים מעליו."""
    timeline = [p for p in items if p["role"] in TIMELINE_ROLES]
    composite = [p for p in items if p["role"] in COMPOSITE_ROLES]
    return timeline, composite
