"""המרת שגיאות לתשובות HTTP מתורגמות – משותף לכל ה-routers."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from .. import i18n
from ..errors import PolixorError

ERROR_STATUS: dict[str, int] = {
    "job_not_found": 404, "clip_not_found": 404, "project_not_found": 404,
    "image_not_found": 404, "upload_missing": 404, "file_missing": 404,
    "no_subtitles": 404, "no_files": 404, "thumb_missing": 404, "not_found": 404,
    "invalid_url": 400, "unsupported_platform": 400, "not_a_video": 400,
    "playlist_not_supported": 400, "blocked_address": 400, "invalid_section": 400,
    "missing_input": 400, "unsupported_format": 400, "empty_file": 400,
    "analysis_incomplete": 400, "bad_range": 400, "invalid_style": 400,
    "live_requires_capture": 409, "project_busy": 409, "already_running": 409,
    "live_not_started": 409, "live_already_running": 409, "live_invalid_state": 409,
    "image_not_ready": 409, "image_cancelled": 409,
    "source_too_long": 413, "source_missing": 410,
    "private_or_unavailable": 403, "drm_protected": 403, "restricted": 403,
    "members_only": 403, "sign_in_required": 403,
    "rate_limited": 429, "image_rate_limit": 429,
    "network_error": 503, "ffmpeg_missing": 503, "model_unavailable": 503,
    "image_key_missing": 503, "image_unavailable": 503,
    "extractor_failed": 502, "ai_provider_failed": 502, "image_failed": 502,
    "image_bad_response": 502, "image_timeout": 504, "image_rejected": 422,
    "invalid_role": 400, "invalid_placement": 400, "disk_space": 507,
    "upload_write_failed": 507, "preview_failed": 500, "frame_failed": 500,
}


def http_error(exc: PolixorError) -> HTTPException:
    """שגיאה מובנית → HTTPException עם הודעה בשפת הבקשה."""
    return HTTPException(status_code=ERROR_STATUS.get(exc.code, 400),
                         detail=exc.to_dict())


def api_error(code: str, status: int | None = None, **params: Any) -> HTTPException:
    """שגיאה לפי קוד בקטלוג (errors.<code>.message / hint)."""
    message = i18n.tr(f"errors.{code}.message", **params)
    hint_key = f"errors.{code}.hint"
    hint = i18n.tr(hint_key, **params) if i18n.has(hint_key) else ""
    return HTTPException(status_code=status or ERROR_STATUS.get(code, 400),
                         detail={"code": code, "message": message, "hint": hint,
                                 "detail": ""})
