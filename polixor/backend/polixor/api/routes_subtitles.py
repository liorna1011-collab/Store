"""
נתיבי API לכתוביות v2: presets, גופנים מותקנים ותצוגה מקדימה.

התצוגה המקדימה מרונדרת בשרת עם אותו כותב ASS ואותו libass של הייצוא,
ולכן מה שהמשתמש רואה בעורך הוא מה שייצרב בווידאו.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import i18n
from ..config import PATHS
from ..db import db_dependency
from ..errors import ClipNotFoundError, PolixorError, ProjectNotFoundError
from ..models import Clip, Job
from ..project_config import ASPECT_RESOLUTION
from ..services import fonts as fontsvc
from ..services import subtitle_preview as preview_svc
from ..services import subtitle_style as style_svc
from ..util.ffmpeg import extract_thumbnail
from .http import api_error, http_error

log = logging.getLogger("polixor.api.subtitles")
router = APIRouter(prefix="/api/subtitles", tags=["subtitles"])

FRAME_RANGE = (240, 4096)


class SubtitlePreviewIn(BaseModel):
    style: dict[str, Any] = Field(default_factory=dict)
    aspect: Optional[str] = None            # 9:16 | 1:1 | 4:5 | 16:9
    width: Optional[int] = None
    height: Optional[int] = None
    language: Optional[str] = None          # שפת התוכן; ברירת מחדל: שפת הבקשה
    text: Optional[str] = None
    project_id: Optional[str] = None        # רקע: פריים מקובץ המקור של הפרויקט
    clip_id: Optional[str] = None           # רקע: פריים מהמקור בזמן הקליפ
    progress: float = 0.25
    max_side: int = 640


def _content_language(value: Optional[str]) -> str:
    lang = (value or "").lower()
    if lang.startswith(("he", "iw")):
        return "he"
    if lang.startswith("en"):
        return "en"
    return i18n.get_lang()


@router.get("/presets")
def presets(language: Optional[str] = Query(None)) -> dict[str, Any]:
    """ה-presets עם סגנון מלא ותוויות בכל השפות, וטווחי הערכים החוקיים."""
    lang = _content_language(language)
    return {
        "presets": style_svc.preset_catalog(lang),
        "default": style_svc.default_style(lang),
        "limits": {
            "size": style_svc.SIZE_RANGE, "outline": style_svc.OUTLINE_RANGE,
            "shadow": style_svc.SHADOW_RANGE, "offset": style_svc.OFFSET_RANGE,
            "words_per_line": style_svc.WORDS_PER_LINE_RANGE,
            "max_lines": style_svc.MAX_LINES_RANGE,
            "weights": list(style_svc.WEIGHTS),
            "backgrounds": list(style_svc.BACKGROUNDS),
            "positions": list(style_svc.POSITIONS),
            "animations": list(style_svc.ANIMATIONS),
        },
    }


@router.get("/fonts")
def fonts() -> dict[str, Any]:
    """
    רק גופנים שמותקנים במחשב הזה, עם כיסוי עברית/לטינית והמשקלים שקיימים
    בפועל. משקל שלא מותקן יוצג בממשק ככזה – libass ישתמש במשקל הקרוב.
    """
    return {"fonts": fontsvc.available_fonts(),
            "default": {"he": style_svc.default_font("he"),
                        "en": style_svc.default_font("en")}}


def _frame_size(body: SubtitlePreviewIn, clip: Optional[Clip]) -> tuple[int, int]:
    if body.aspect:
        res = ASPECT_RESOLUTION.get(body.aspect)
        if not res:
            raise api_error("bad_aspect", aspect=body.aspect,
                            allowed=", ".join(ASPECT_RESOLUTION))
        w, h = (int(x) for x in res.split("x"))
        return w, h
    if body.width and body.height:
        w, h = int(body.width), int(body.height)
    elif clip is not None and clip.width and clip.height:
        w, h = int(clip.width), int(clip.height)
    else:
        w, h = 1080, 1920
    lo, hi = FRAME_RANGE
    if not (lo <= w <= hi and lo <= h <= hi):
        raise api_error("bad_frame_size", width=w, height=h)
    return w, h


def _source_path(job: Job) -> Optional[Path]:
    src = (job.artifacts or {}).get("source_path")
    return Path(src) if src and Path(src).exists() else None


def _background(db: Session, body: SubtitlePreviewIn,
                clip: Optional[Clip]) -> Optional[Path]:
    """פריים מקובץ המקור המקומי (לא מהקליפ המוגמר – שם כבר צרובות כתוביות)."""
    if clip is not None:
        job = db.get(Job, clip.job_id)
        src = _source_path(job) if job else None
        if src is None:
            return None
        cache = PATHS.job_work_dir(job.id) / f"subtitle_bg_{clip.id}.jpg"
        if not cache.exists():
            at = float(clip.source_start or 0.0) + min(1.5, float(clip.duration or 0.0) / 2)
            if extract_thumbnail(src, cache, at_seconds=at, width=1080) is None:
                return None
        return cache
    if body.project_id:
        from ..pipeline import extract_source_thumbnail

        job = db.get(Job, body.project_id)
        if job is None:
            raise http_error(ProjectNotFoundError())
        cache = PATHS.job_work_dir(job.id) / "source_thumb.jpg"
        if not cache.exists() and extract_source_thumbnail(job, cache) is None:
            return None
        return cache
    return None


@router.post("/preview")
def preview(body: SubtitlePreviewIn, db: Session = Depends(db_dependency)) -> dict[str, Any]:
    """תמונה של הכתוביות בסגנון הנתון, כפי ש-libass ירנדר אותן בייצוא."""
    if body.text and len(body.text) > preview_svc.MAX_TEXT:
        raise api_error("preview_text_too_long", limit=preview_svc.MAX_TEXT)
    clip = None
    if body.clip_id:
        clip = db.get(Clip, body.clip_id)
        if clip is None:
            raise http_error(ClipNotFoundError())
    width, height = _frame_size(body, clip)
    lang = _content_language(body.language)
    bg = _background(db, body, clip)
    fmt = "jpg" if bg is not None else "png"
    try:
        res = preview_svc.render_preview(
            body.style, width=width, height=height, language=lang, text=body.text,
            background=bg, progress=body.progress,
            max_side=max(240, min(1080, int(body.max_side or 640))), fmt=fmt)
    except PolixorError as exc:
        raise http_error(exc)
    except Exception as exc:                                  # noqa: BLE001
        log.exception("subtitle preview failed")
        raise api_error("preview_failed") from exc
    note_key = "subtitles.background_source_frame" if bg is not None \
        else "subtitles.background_plain"
    style = style_svc.clamp_style(body.style, language=lang)
    return {
        "image": f"data:{res.media_type};base64,"
                 + base64.b64encode(res.image).decode("ascii"),
        "width": res.width, "height": res.height,
        "target_width": res.target_width, "target_height": res.target_height,
        "at": res.at, "lines": res.lines, "language": lang,
        "style": style,
        "background": "source_frame" if bg is not None else "plain",
        "background_note": i18n.tr(note_key),
        "font_weights": fontsvc.family_weights(style["font"]),
        "elapsed_ms": res.elapsed_ms,
    }
