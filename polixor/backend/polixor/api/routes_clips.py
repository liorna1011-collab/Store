"""נתיבי API לקליפים: צפייה, עריכה, תיקון כתוביות, ייצוא מחדש והורדה."""

from __future__ import annotations

import io
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import desc
from sqlalchemy.orm import Session

from .. import i18n
from ..config import PATHS, SETTINGS, AppSettings
from ..db import db_dependency
from ..events import BUS
from ..errors import ClipNotFoundError, JobNotFoundError, PolixorError
from ..models import Clip, ClipKind, ClipReview, ClipStatus, Job, SubtitleCue
from ..project_config import ASPECT_RESOLUTION, LAYOUT_TO_PIPELINE
from ..schemas import ClipOut, ClipPatch, CueIn, CueOut, ReExportRequest, ZipRequest
from ..services import editing as editing_svc
from ..services import reframe as reframe_svc
from ..services import render as render_svc
from ..services import render_qa as qa_svc
from ..services import subtitle_render
from ..services import subtitle_style as style_svc
from ..services import subtitles as sub_svc
from ..util.ffmpeg import probe as probe_media
from ..util.fs import is_within, safe_filename, unique_path
from .http import api_error
from .routes_jobs import _http
from .serializers import clip_to_out, cue_to_out

log = logging.getLogger("polixor.api.clips")
router = APIRouter(prefix="/api", tags=["clips"])

_ALLOWED_ROOTS = [PATHS.exports, PATHS.sources, PATHS.work]


def content_disposition(filename: str, *, inline: bool = False) -> str:
    """
    בונה כותרת Content-Disposition שעובדת עם שמות בעברית.

    כותרות HTTP מקודדות ב-latin-1, ולכן שם קובץ בעברית חייב להישלח
    בקידוד RFC 5987 (filename*=UTF-8''...), עם חלופת ASCII לדפדפנים ישנים.
    """
    from urllib.parse import quote

    disp = "inline" if inline else "attachment"
    ascii_name = "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_"
                         for c in filename).strip("_ ") or "file"
    encoded = quote(filename, safe="")
    return f"{disp}; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


def _allowed_roots() -> list[Path]:
    roots = list(_ALLOWED_ROOTS)
    custom = SETTINGS.get().resolved_export_dir()
    if custom not in roots:
        roots.append(custom)
    return roots


# --------------------------------------------------------------------------
# רשימה ופרטים
# --------------------------------------------------------------------------
@router.get("/clips", response_model=list[ClipOut])
def list_clips(job_id: Optional[str] = None, kind: Optional[str] = None,
               limit: int = Query(200, ge=1, le=1000),
               db: Session = Depends(db_dependency)) -> list[ClipOut]:
    q = db.query(Clip)
    if job_id:
        q = q.filter(Clip.job_id == job_id)
    if kind:
        try:
            q = q.filter(Clip.kind == ClipKind(kind))
        except ValueError:
            pass
    rows = q.order_by(desc(Clip.score), Clip.source_start).limit(limit).all()
    return [clip_to_out(db, c) for c in rows]


@router.get("/clips/download-zip")
def download_zip_get(ids: str = Query(..., max_length=20000), subtitles: bool = Query(True),
                     db: Session = Depends(db_dependency)):
    """The same archive as a plain link, so the browser downloads it natively (never through page memory)."""
    clip_ids = [x for x in ids.split(",") if x][:500]
    return _zip_response(ZipRequest(clip_ids=clip_ids, include_subtitles=subtitles), db)


@router.get("/clips/{clip_id}", response_model=ClipOut)
def get_clip(clip_id: str, db: Session = Depends(db_dependency)) -> ClipOut:
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise _http(ClipNotFoundError())
    return clip_to_out(db, clip)


@router.patch("/clips/{clip_id}", response_model=ClipOut)
def patch_clip(clip_id: str, payload: ClipPatch,
               db: Session = Depends(db_dependency)) -> ClipOut:
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise _http(ClipNotFoundError())
    if payload.title is not None:
        clip.title = payload.title.strip()[:200]
    if payload.description is not None:
        clip.description = payload.description.strip()[:1000]
    db.commit()
    db.refresh(clip)
    return clip_to_out(db, clip)


@router.delete("/clips/{clip_id}")
def delete_clip(clip_id: str, db: Session = Depends(db_dependency)) -> dict[str, Any]:
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise _http(ClipNotFoundError())
    removed = 0
    for p in (clip.file_path, clip.thumbnail_path):
        if p and Path(p).exists() and is_within(Path(p), _allowed_roots()):
            try:
                Path(p).unlink()
                removed += 1
            except OSError:
                pass
    db.query(SubtitleCue).filter(SubtitleCue.clip_id == clip_id).delete()
    db.query(ClipReview).filter(ClipReview.clip_id == clip_id).delete()
    db.delete(clip)
    db.commit()
    return {"deleted": True, "files_removed": removed}


# --------------------------------------------------------------------------
# כתוביות
# --------------------------------------------------------------------------
def _image_shift(clip: Optional[Clip]) -> list[dict[str, float]]:
    """נקודות ההכנסה של תמונות בייצוא האחרון, לצורך הזזת כתוביות בקריאה."""
    if clip is None:
        return []
    inserts = (clip.render_params or {}).get("image_inserts") or []
    return [{"at": float(i.get("at", 0.0)), "seconds": float(i.get("seconds", 0.0))}
            for i in inserts if isinstance(i, dict)]


def _shift_amount(start: float, inserts: list[dict[str, float]]) -> float:
    return sum(i["seconds"] for i in inserts if i["at"] <= start + 1e-6)


@router.get("/clips/{clip_id}/cues", response_model=list[CueOut])
def get_cues(clip_id: str, db: Session = Depends(db_dependency)) -> list[CueOut]:
    """
    כתוביות הקליפ בזמני הווידאו שהמשתמש רואה.

    ב-DB נשמרים זמני הקליפ **ללא** התמונות, כדי שייצוא חוזר יתחיל
    תמיד ממצב נקי. תמונות שהוכנסו דוחפות את הזמנים, וההזזה מחושבת
    כאן בקריאה בלבד.
    """
    clip = db.get(Clip, clip_id)
    rows = (db.query(SubtitleCue).filter(SubtitleCue.clip_id == clip_id)
            .order_by(SubtitleCue.idx).all())
    inserts = _image_shift(clip)
    out: list[CueOut] = []
    for row in rows:
        cue = cue_to_out(row)
        delta = _shift_amount(cue.start, inserts)
        if delta:
            cue = cue.model_copy(update={
                "start": round(cue.start + delta, 3),
                "end": round(cue.end + delta, 3),
                "words": [{**w, "start": float(w.get("start", 0.0)) + delta,
                           "end": float(w.get("end", 0.0)) + delta}
                          for w in (cue.words or []) if isinstance(w, dict)],
            })
        out.append(cue)
    return out


@router.put("/clips/{clip_id}/cues", response_model=list[CueOut])
def put_cues(clip_id: str, cues: list[CueIn],
             db: Session = Depends(db_dependency)) -> list[CueOut]:
    """
    שמירת כתוביות מתוקנות. הטקסט המקורי נשמר לצד המתוקן,
    כדי שתמיד יהיה אפשר לראות מה נאמר בפועל בתמלול.
    """
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise _http(ClipNotFoundError())

    existing = {c.id: c for c in db.query(SubtitleCue)
                .filter(SubtitleCue.clip_id == clip_id).all()}
    seen: set[int] = set()

    # הלקוח עובד בזמנים שכוללים את התמונות שהוכנסו. ב-DB נשמר ציר
    # הזמן ללא התמונות, ולכן מחסירים כאן את אותה הזזה בדיוק.
    inserts = _image_shift(clip)

    def unshift(value: float) -> float:
        if not inserts:
            return value
        delta = 0.0
        for item in sorted(inserts, key=lambda i: i["at"]):
            if value - delta >= item["at"] - 1e-6:
                delta += item["seconds"]
        return max(0.0, value - delta)

    for idx, item in enumerate(cues):
        start = unshift(max(0.0, float(item.start)))
        end = max(start + 0.1, unshift(float(item.end)))
        if item.id and item.id in existing:
            row = existing[item.id]
            if row.text != item.text:
                row.edited = True
                _log_user_edit(clip, row, item.text)
            row.text = item.text
            row.start = start
            row.end = end
            row.idx = idx
            seen.add(item.id)
        else:
            row = SubtitleCue(clip_id=clip_id, idx=idx,
                              start=start, end=end,
                              text=item.text, original_text="", edited=True,
                              language=clip.subtitle_style.get("language", ""))
            db.add(row)

    for cue_id, row in existing.items():
        if cue_id not in seen:
            db.delete(row)

    db.commit()
    rows = (db.query(SubtitleCue).filter(SubtitleCue.clip_id == clip_id)
            .order_by(SubtitleCue.idx).all())
    return [cue_to_out(c) for c in rows]


@router.get("/clips/{clip_id}/proofread")
def get_proofread(clip_id: str, db: Session = Depends(db_dependency)) -> dict[str, Any]:
    """
    תוצאות ההגהה בטווח של הקליפ: מה תוקן (ולפי איזו ראיה), מה אושר, ומה
    סומן לבדיקה – כולל החלופה שהמודל החזק שמע, כהצעה לעורך (לא מוחלת).
    """
    from ..services import transcript_correct as tc

    clip = db.get(Clip, clip_id)
    if clip is None:
        raise _http(ClipNotFoundError())
    data = tc.load(PATHS.job_work_dir(clip.job_id) / "transcript.corrections.json") or {}
    spans = [(float(a), float(b)) for a, b in (clip.segments_json or [])] or \
        [(clip.source_start, clip.source_end)]
    items = []
    for r in data.get("segments") or []:
        if not any(r["end"] > a and r["start"] < b for a, b in spans):
            continue
        alt = next((e.get("text") for e in r.get("evidence") or []
                    if e.get("kind") == "retranscription" and e.get("text")), None)
        reason = r.get("reason") or {}
        items.append({
            "start": r["start"], "end": r["end"], "status": r["status"],
            "original": r["original"], "corrected": r.get("corrected"),
            "alternative": alt if (alt and r["status"] == "flagged"
                                   and tc.norm_text(alt) != tc.norm_text(r["original"])) else None,
            "source": r.get("source", ""), "confidence": r.get("confidence", 0.0),
            "low_words": [w.get("text") for w in r.get("low_words") or []],
            "reason": i18n.tr(f"correct.{reason['key']}", **(reason.get("params") or {}))
            if reason.get("key") else "",
        })
    stats = {k: sum(1 for i in items if i["status"] == k)
             for k in ("confirmed", "corrected", "flagged")}
    return {"available": bool(data), "strong_model": data.get("strong_model", ""),
            "stats": stats, "items": items}


def _log_user_edit(clip: Clip, row: SubtitleCue, new_text: str) -> None:
    """
    תיקון ידני נרשם ביומן התיקונים של הפרויקט, לצד מה שהמזהה שמע במקור –
    כדי שאפשר יהיה ללמוד מהתיקונים (ולא רק לדרוס את הטקסט).
    """
    from ..services import transcript_correct as tc

    try:
        asr = " ".join(str(w.get("asr")) for w in (row.words or [])
                       if isinstance(w, dict) and w.get("asr")) or row.original_text
        tc.record_user_edit(PATHS.job_work_dir(clip.job_id) / "transcript.corrections.json",
                            clip_id=clip.id, cue_id=row.id,
                            source_start=clip.source_start + row.start,
                            source_end=clip.source_start + row.end,
                            before=row.text, after=new_text, asr_text=asr)
    except Exception as exc:                            # noqa: BLE001
        log.warning("could not record the subtitle edit: %s", exc)


@router.get("/clips/{clip_id}/subtitles.srt")
def download_srt(clip_id: str, db: Session = Depends(db_dependency)) -> Response:
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise _http(ClipNotFoundError())
    cues = _cues_for_render(db, clip_id, inserts=_image_shift(clip))
    if not cues:
        raise api_error("no_subtitles", 404)
    buf = io.StringIO()
    tmp = PATHS.work / f"{clip_id}.srt"
    sub_svc.write_srt(cues, tmp)
    buf.write(tmp.read_text("utf-8"))
    tmp.unlink(missing_ok=True)
    name = safe_filename(clip.title or clip_id, max_length=60)
    return Response(
        content=buf.getvalue(),
        media_type="application/x-subrip; charset=utf-8",
        headers={"Content-Disposition": content_disposition(f"{name}.srt")},
    )


# --------------------------------------------------------------------------
# ייצוא מחדש
# --------------------------------------------------------------------------
_REEXPORT_LOCK = threading.Lock()
_REEXPORTING: set[str] = set()
_REEXPORT_POOL = None


def _reexport_pool():
    global _REEXPORT_POOL
    with _REEXPORT_LOCK:
        if _REEXPORT_POOL is None:
            from concurrent.futures import ThreadPoolExecutor

            from ..services import hardware

            _REEXPORT_POOL = ThreadPoolExecutor(max_workers=hardware.render_workers(),
                                                thread_name_prefix="polixor-reexport")
        return _REEXPORT_POOL


def _set_reexport_state(db: Session, clip: Clip, **state: Any) -> None:
    params = dict(clip.render_params or {})
    params["reexport"] = {**dict(params.get("reexport") or {}), **state}
    clip.render_params = params


@router.post("/clips/{clip_id}/reexport", response_model=ClipOut)
def reexport_clip(clip_id: str, payload: ReExportRequest, response: Response,
                  background: bool = Query(False),
                  db: Session = Depends(db_dependency)) -> ClipOut:
    """
    מייצא קליפ מחדש עם פרמטרים חדשים: זמני התחלה/סיום, יחס מסך,
    פריסה, אזור מצלמה, וכתוביות מתוקנות.

    background=true (the interface): the render runs in the re-export pool and the call
    returns at once (202) with the clip in "rendering"; the page follows the clip until
    render_params.reexport.state leaves "running". A second request while one runs returns
    the running one (a double click never starts two renders). A failed re-edit keeps the
    previous file and status. Without it the call waits for the render (scripts, tests).
    """
    if not background:
        with _REEXPORT_LOCK:
            if clip_id in _REEXPORTING:
                raise api_error("clip_busy", 409)
            _REEXPORTING.add(clip_id)
        try:
            return _reexport_run(clip_id, payload, db)
        finally:
            with _REEXPORT_LOCK:
                _REEXPORTING.discard(clip_id)
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise _http(ClipNotFoundError())
    job = db.get(Job, clip.job_id)
    if job is None:
        raise _http(JobNotFoundError())
    if not Path((job.artifacts or {}).get("source_path", "")).is_file():
        raise api_error("source_missing")
    response.status_code = 202
    from ..services import taskq

    if taskq.process_mode():
        # a worker process renders it (the interactive worker is kept free for exactly this)
        if taskq.active_task("reexport", clip_id) is not None:
            return clip_to_out(db, clip)
        prev = clip.status.value if clip.status != ClipStatus.RENDERING else ClipStatus.READY.value
        _set_reexport_state(db, clip, state="running", started_at=time.time(), prev_status=prev, error="")
        clip.status = ClipStatus.RENDERING
        db.commit()
        taskq.enqueue("reexport", clip.job_id, ref=clip_id, key=f"reexport:{clip_id}",
                      priority=taskq.PRIO_INTERACTIVE,
                      payload={"request": payload.model_dump(), "lang": i18n.get_lang()})
        db.refresh(clip)
        return clip_to_out(db, clip)
    with _REEXPORT_LOCK:
        if clip_id in _REEXPORTING:
            return clip_to_out(db, clip)
        _REEXPORTING.add(clip_id)
    prev = clip.status.value if clip.status != ClipStatus.RENDERING else ClipStatus.READY.value
    _set_reexport_state(db, clip, state="running", started_at=time.time(), prev_status=prev, error="")
    clip.status = ClipStatus.RENDERING
    db.commit()
    try:
        _reexport_pool().submit(_reexport_background, clip_id, payload, i18n.get_lang())
    except RuntimeError:
        with _REEXPORT_LOCK:
            _REEXPORTING.discard(clip_id)
        raise
    db.refresh(clip)
    return clip_to_out(db, clip)


def _reexport_background(clip_id: str, payload: ReExportRequest, lang: str) -> None:
    with i18n.use_lang(lang):
        _reexport_background_run(clip_id, payload)


def _reexport_background_run(clip_id: str, payload: ReExportRequest) -> None:
    from fastapi import HTTPException

    from ..db import get_session

    db = get_session()
    error = ""
    try:
        try:
            _reexport_run(clip_id, payload, db)
        except HTTPException as exc:
            d = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            error = str(d.get("message") or "")[:300] or "render_failed"
        except Exception:                                 # noqa: BLE001
            log.exception("background re-export of %s crashed", clip_id)
            error = i18n.tr("errors.unexpected.message")
        db.rollback()
        clip = db.get(Clip, clip_id)
        if clip is not None:
            prev = str(((clip.render_params or {}).get("reexport") or {}).get("prev_status") or "ready")
            if error:
                # the previous file is untouched: the clip goes back to what it was
                clip.status = ClipStatus(prev) if prev in ClipStatus._value2member_map_ else ClipStatus.READY
                clip.error = ""
                _set_reexport_state(db, clip, state="failed", error=error, finished_at=time.time())
            else:
                _set_reexport_state(db, clip, state="done", error="", finished_at=time.time())
            db.commit()
            BUS.emit("clip.updated", clip.job_id, clip_id=clip_id, status=clip.status.value)
    finally:
        db.close()
        with _REEXPORT_LOCK:
            _REEXPORTING.discard(clip_id)


def run_reexport_task(clip_id: str, payload: ReExportRequest, lang: str) -> None:
    """A worker process runs a queued re-export (same steps as the in-process background one)."""
    with _REEXPORT_LOCK:
        _REEXPORTING.add(clip_id)
    _reexport_background(clip_id, payload, lang)


def fail_interrupted_reexport(clip_id: str) -> None:
    """A re-export whose worker was lost for good: the clip goes back to its previous file and state."""
    from ..db import session_scope

    with session_scope() as s:
        clip = s.get(Clip, clip_id)
        if clip is None or clip.status != ClipStatus.RENDERING:
            return
        st = (clip.render_params or {}).get("reexport") or {}
        prev = str(st.get("prev_status") or "ready")
        clip.status = ClipStatus(prev) if prev in ClipStatus._value2member_map_ else ClipStatus.READY
        _set_reexport_state(s, clip, state="failed", error="interrupted", finished_at=time.time())
        job_id = clip.job_id
    BUS.emit("clip.updated", job_id, clip_id=clip_id, status="failed")


def recover_interrupted_reexports() -> int:
    """At start: a re-export that was running when the server stopped is reported, never left spinning."""
    from ..db import session_scope
    from ..services import taskq

    n = 0
    with session_scope() as s:
        for clip in s.query(Clip).filter(Clip.status == ClipStatus.RENDERING).all():
            st = (clip.render_params or {}).get("reexport") or {}
            if st.get("state") != "running":
                continue
            if taskq.process_mode() and taskq.active_task("reexport", clip.id) is not None:
                continue                       # a worker process is rendering it (or will): not interrupted
            prev = str(st.get("prev_status") or "ready")
            clip.status = ClipStatus(prev) if prev in ClipStatus._value2member_map_ else ClipStatus.READY
            _set_reexport_state(s, clip, state="failed", error="interrupted", finished_at=time.time())
            n += 1
    return n


def _reexport_run(clip_id: str, payload: ReExportRequest, db: Session) -> ClipOut:
    from ..pipeline import load_analysis_for_job, load_layouts, load_visual

    clip = db.get(Clip, clip_id)
    if clip is None:
        raise _http(ClipNotFoundError())
    job = db.get(Job, clip.job_id)
    if job is None:
        raise _http(JobNotFoundError())

    source_path = Path((job.artifacts or {}).get("source_path", ""))
    if not source_path.is_file():
        raise api_error("source_missing")

    settings = AppSettings.from_dict(job.settings_snapshot or SETTINGS.get().to_dict())
    if payload.quality in ("low", "medium", "high"):
        settings.video_quality = payload.quality

    start = float(payload.source_start if payload.source_start is not None
                  else clip.source_start)
    end = float(payload.source_end if payload.source_end is not None
                else clip.source_end)
    source_info = (job.artifacts or {}).get("source_info") or {}
    duration_total = float(source_info.get("duration") or 0.0)
    start = max(0.0, start)
    end = min(end, duration_total) if duration_total else end
    if end - start < 0.5:
        raise api_error("bad_range")

    old_params = dict(clip.render_params or {})
    aspect = (payload.aspect or clip.aspect) or "16:9"
    if aspect not in ASPECT_RESOLUTION:
        raise api_error("bad_aspect", aspect=aspect, allowed=", ".join(ASPECT_RESOLUTION))
    # כל יחס שאינו 16:9 נחתך מהמקור הרוחבי (גם 1:1 ו-4:5, לא רק 9:16)
    vertical = aspect != "16:9"
    if aspect == clip.aspect and old_params.get("resolution"):
        requested = str(old_params["resolution"])
    elif not vertical and clip.kind == ClipKind.LONG:
        requested = settings.long_resolution
    else:
        requested = ASPECT_RESOLUTION[aspect]
    layout = payload.layout or old_params.get("layout_requested") or clip.layout \
        or ("auto" if vertical else "center")
    layout = LAYOUT_TO_PIPELINE.get(layout, layout)
    subs_on = (payload.subtitles_enabled if payload.subtitles_enabled is not None
               else clip.subtitles_enabled)

    # -- תכנית העריכה לטווח החדש --
    if payload.edit_style:
        settings.edit_style_short = payload.edit_style
        settings.edit_style_long = payload.edit_style
    if payload.caption_animation:
        settings.subtitle_animation = payload.caption_animation
    edit_style = editing_svc.style_from_settings(settings, vertical=vertical)

    # מקור ארוך במצב מהיר: גבולות חדשים מחוץ לחלון שנותח – מנתחים רק את החסר
    from ..pipeline import ensure_visual_for_range

    ensure_visual_for_range(job, start, end)
    db.commit()
    analysis = load_analysis_for_job(job)
    edit_plan = editing_svc.build_edit_plan(
        clip_start=start, clip_end=end,
        peak_time=old_params.get("peak_time", (start + end) / 2.0),
        style=edit_style,
        audio=None,
        timeline=analysis.get("timeline"),
        transcript=analysis.get("transcript"),
        silences=analysis.get("silences"),
    )

    src_w = int(source_info.get("width") or 1920)
    src_h = int(source_info.get("height") or 1080)
    w, h = render_svc.target_resolution(requested, src_w, src_h, vertical=vertical)

    # -- מסגור: אותו מנגנון של הייצוא הראשון, כולל פריסת תגובה --
    plan = None
    if vertical:
        plan = reframe_svc.plan_reframe(
            load_visual(job), clip_start=start, clip_end=end, layout=layout,
            manual_camera=payload.camera_region or old_params.get("camera_region")
            or (job.artifacts or {}).get("camera_region"),
            target_aspect=w / max(1, h), layouts=load_layouts(job),
            order=getattr(settings, "reaction_order", "auto"),
            segments=[(start, end)], out_size=(w, h), src_size=(src_w, src_h))
        if plan is not None:
            edit_plan = editing_svc.bind_reframe(edit_plan, plan)

    # -- כתוביות: הטקסט המתוקן מה-DB. הזמנים השמורים הם בזמני התוצר
    # הקודם; ממפים אותם לזמני המקור דרך הביטים הקודמים, ומשם דרך
    # תכנית העריכה החדשה. בלי זה, קליפ שהוסר ממנו אוויר מת היה מקבל
    # כתוביות מוזזות בכל ייצוא חוזר.
    rows: list[SubtitleCue] = []
    cues: list[sub_svc.Cue] = []
    if subs_on:
        rows, cues = _cues_in_new_timeline(db, clip, start=start, plan=edit_plan)
    lang = sub_svc.detect_cue_language(cues)

    stored = dict(clip.subtitle_style or {})
    patch = dict(payload.subtitle_style or {})
    use_v2 = (style_svc.is_v2(stored) or style_svc.is_v2(patch)
              or bool(job.project_config))
    hook_prev = dict((clip.render_params or {}).get("editorial_hook") or {})
    # the overlay is re-drawn only when it was drawn before AND is still switched on
    hook_text = (str(hook_prev.get("text") or "")
                 if vertical and hook_prev.get("rendered") and SETTINGS.get().editorial_hook_enabled
                 and settings.editorial_hook_enabled else "")
    title_text = clip.title if ((payload.title_card or settings.title_card_enabled)
                                and not hook_text) else ""
    work = PATHS.job_work_dir(job.id)
    sub_path = None
    if use_v2:
        base = stored if style_svc.is_v2(stored) else style_svc.from_legacy(
            stored, vertical=vertical, language=lang, ref_height=clip.height or None)
        style: Any = style_svc.clamp_style({**base, **patch}, language=lang)
        if cues:
            sub_path = work / f"{clip_id}_re.ass"
            subtitle_render.write_ass_v2(cues, sub_path, width=w, height=h, style=style,
                                         language=lang, title_text=title_text)
        style_row = dict(style)
        margin_v = subtitle_render.style_margin_v(style, w, h)
    else:
        style_dict = {**stored, **patch}
        # הסגנון נבנה רק כאן, כשגובה הפריים ידוע: גודל גופן ב-ASS נמדד
        # ביחידות הפלט, וגודל שכוון ל-1920 מסתיר חצי מסך בפריים 480.
        style = sub_svc.style_for_clip(settings, vertical=vertical, language=lang,
                                       override=style_dict, frame_height=h)
        style.animation = edit_style.caption_animation
        if cues:
            sub_path = work / f"{clip_id}_re.ass"
            sub_svc.write_ass(cues, sub_path, width=w, height=h, style=style,
                              title_text=title_text)
        style_row = style.__dict__.copy()
        margin_v = int(style.margin_v or 0)
    hook_rec: dict[str, Any] = {}
    if hook_text:
        from ..clip_factory import _style_font
        from ..services import hook_overlay

        sub_path, _, hook_rec = hook_overlay.apply_to_clip(
            hook_text, sub_path=sub_path, parts=[], work_dir=work, clip_id=f"{clip_id}_re",
            width=w, height=h, font=_style_font(style), plan=plan, style=style, v2=use_v2,
            subtitles_on=bool(cues), language=lang)
        hook_rec = {**hook_prev, **hook_rec}

    export_dir = settings.resolved_export_dir() / job.id
    export_dir.mkdir(parents=True, exist_ok=True)
    prefix = "short" if vertical else "long"
    out_path = unique_path(
        export_dir /
        f"{prefix}_re_{safe_filename(clip.title or clip_id, max_length=48)}.mp4")

    req = render_svc.build_request(
        source=source_path, output=out_path, segments=[(start, end)],
        vertical=vertical, settings=settings, reframe=plan,
        subtitle_path=sub_path, source_info=source_info,
        transitions=False, work_dir=work,
        edit_style=edit_style.name, edit_plans=[edit_plan],
        resolution=requested,
    )

    clip.status = ClipStatus.RENDERING
    clip.error = ""
    db.commit()

    try:
        result = render_svc.render_clip(req, cancel_event=threading.Event())
    except PolixorError as exc:
        clip.status = ClipStatus.FAILED
        clip.error = exc.message
        db.commit()
        from ..services import notifications

        notifications.notify("render_failed", params={"clip": clip.title or ""},
                             error=exc.to_record(), link=f"/clips/{clip_id}/edit",
                             job_id=clip.job_id, clip_id=clip_id,
                             group_key=f"clip:{clip_id}:render_failed")
        raise _http(exc) from exc

    # -- שיבוצי תמונות: מעבר נפרד, אחרי שהקליפ כבר קיים ותקין --
    # כשל כאן אינו מפיל את הייצוא. הקליפ נשמר בלי התמונות, והסיבה
    # נרשמת ב-render_params כדי שהממשק יוכל לומר למשתמש מה קרה.
    image_note, image_error, image_inserts = _apply_clip_images(
        db, clip_id, result, work=work, settings=settings)

    clip = db.get(Clip, clip_id)
    assert clip is not None

    # מסירים את הקובץ הישן רק אחרי שהחדש נוצר בהצלחה
    old = Path(clip.file_path) if clip.file_path else None
    old_thumb = Path(clip.thumbnail_path) if clip.thumbnail_path else None

    clip.file_path = str(result.path)
    clip.file_size = result.size_bytes
    clip.thumbnail_path = keep_custom_thumbnail(
        clip.thumbnail_path, str(result.thumbnail) if result.thumbnail else "")
    if old_thumb is not None and str(old_thumb) == clip.thumbnail_path:
        old_thumb = None                               # תמונת שער שנבחרה – לא נמחקת
    clip.width, clip.height = result.width, result.height
    clip.duration = result.duration
    clip.source_start, clip.source_end = start, end
    clip.segments_json = []
    clip.aspect = aspect
    clip.layout = plan.layout if plan else ("center" if vertical else clip.layout)
    clip.subtitles_enabled = bool(cues)
    clip.subtitle_style = style_row
    if subs_on:
        _store_cues(db, clip_id, rows, cues, language=lang)
    params = dict(clip.render_params or {})
    params["reframe_note"] = plan.note if plan else ""
    params["tracked_ratio"] = plan.tracked_ratio if plan else 0.0
    params["layout_plan"] = plan.to_dict() if plan is not None else None
    if plan and plan.camera_region:
        params["camera_region"] = plan.camera_region
    params["resolution"] = requested
    params["layout_requested"] = layout if vertical else ""
    params["reexported"] = True
    params["render_stats"] = dict(result.stats or {})
    if hook_rec:
        params["editorial_hook"] = hook_rec
    params["images_note"] = image_note
    params["images_error"] = image_error
    params["image_inserts"] = image_inserts
    params["edit_style"] = edit_style.name
    params["edit_style_label"] = edit_style.label
    params["edit_summary"] = editing_svc.describe_plan(edit_plan, edit_style)
    params["edit_stats"] = [edit_plan.stats()]
    params["raw_duration"] = round(end - start, 3)
    params["beats"] = [
        {"start": round(b.src_start, 3), "end": round(b.src_end, 3),
         "zoom": round(b.zoom, 3), "zoom_to": round(b.zoom_to, 3),
         "speed": round(b.speed, 3), "reason": b.reason}
        for b in edit_plan.beats
    ]
    # §25: גם ייצוא חוזר עובר בדיקת איכות. בלי זה, ייצוא מחדש היה
    # מנקה בשקט סימון „דורש בדיקה" בלי שאיש בדק כלום.
    try:
        report = qa_svc.check_render(
            result.path,
            expected_duration=edit_plan.out_duration,
            expect_audio=bool(source_info.get("has_audio", True)),
            vertical=vertical, cues=cues or None,
            safe_margin_v=margin_v)
        params["qa"] = report.to_dict()
        needs_review = report.needs_review
    except Exception as exc:                            # noqa: BLE001
        log.warning("re-export QA failed: %s", exc, exc_info=True)
        params["qa"] = {"error": str(exc)}
        needs_review = False

    clip.render_params = params
    clip.status = (ClipStatus.NEEDS_REVIEW if needs_review
                   else ClipStatus.READY)
    db.commit()
    from ..services import notifications

    notifications.notify("render_complete", params={"clip": clip.title or ""},
                         link=f"/clips/{clip_id}/edit", job_id=clip.job_id, clip_id=clip_id,
                         group_key=f"clip:{clip_id}:render_complete",
                         level="warning" if needs_review else "")

    for p in (old, old_thumb):
        if p and p.exists() and p != result.path and is_within(p, _allowed_roots()):
            try:
                p.unlink()
            except OSError:
                pass

    db.refresh(clip)
    return clip_to_out(db, clip)


def _cues_in_new_timeline(db: Session, clip: Clip, *, start: float,
                          plan) -> tuple[list[SubtitleCue], list[sub_svc.Cue]]:
    """
    הכתוביות השמורות של הקליפ, בזמני התוצר החדש.

    זמן שמור → זמן במקור (דרך הביטים של הייצוא הקודם) → זמן יחסי
    לתחילת הטווח החדש → זמן בתוצר החדש (דרך תכנית העריכה החדשה).
    כתובית שנחתכה כולה מהתוצר החדש לא נכנסת. מוחזרות גם השורות
    המקוריות (באותו סדר), כדי לשמור את הטקסט המקורי שלהן.
    """
    params = clip.render_params or {}
    segments = [(float(a), float(b)) for a, b in (clip.segments_json or [])] \
        or [(float(clip.source_start), float(clip.source_end))]
    to_source = editing_svc.output_to_source(params.get("beats") or [], segments)
    rows = (db.query(SubtitleCue).filter(SubtitleCue.clip_id == clip.id)
            .order_by(SubtitleCue.idx).all())
    kept_rows: list[SubtitleCue] = []
    out: list[sub_svc.Cue] = []
    for r in rows:
        span = plan.map_span(to_source(r.start) - start, to_source(r.end) - start)
        if span is None:
            continue
        words = []
        for wd in (r.words or []):
            ws = plan.map_time(to_source(float(wd.get("start", 0.0))) - start)
            we = plan.map_time(to_source(float(wd.get("end", 0.0))) - start)
            if ws is None or we is None:
                continue
            words.append({**wd, "start": round(ws, 3), "end": round(max(we, ws + 0.02), 3)})
        kept_rows.append(r)
        out.append(sub_svc.Cue(start=span[0], end=span[1], text=r.text, words=words,
                               language=r.language))
    return kept_rows, out


def _store_cues(db: Session, clip_id: str, rows: list[SubtitleCue],
                cues: list[sub_svc.Cue], *, language: str) -> None:
    """שומר את הכתוביות בזמני התוצר החדש (הטקסט המקורי נשמר)."""
    originals = [r.original_text or r.text for r in rows]
    db.query(SubtitleCue).filter(SubtitleCue.clip_id == clip_id).delete()
    for idx, (cue, original) in enumerate(zip(cues, originals)):
        db.add(SubtitleCue(clip_id=clip_id, idx=idx, start=cue.start, end=cue.end,
                           text=cue.text, original_text=original,
                           language=cue.language or language, words=cue.words))


def _apply_clip_images(db: Session, clip_id: str, result, *,
                       work: Path, settings: AppSettings
                       ) -> tuple[str, str, list[dict[str, float]]]:
    """
    מחיל שיבוצי תמונות על קליפ שזה עתה רונדר.

    מוחזר (הערה, שגיאה, נקודות הכנסה). כשל אינו נזרק החוצה: הקליפ
    כבר קיים ותקין, והמשתמש מקבל אותו עם הסבר במקום שגיאה כוללת.
    """
    from ..services import image_assets as assets
    from ..services import image_render as ir

    try:
        placements = assets.placements_for_clip(db, clip_id)
    except Exception as exc:                  # pragma: no cover
        log.warning("could not load placements for %s: %s", clip_id, exc)
        return "", i18n.tr("system.images.placements_failed"), []

    active = [p for p in placements if p["role"] != "thumbnail"]
    if not active:
        return "", "", []

    info = probe_media(result.path)
    staged = work / f"{clip_id}_img.mp4"
    try:
        out = ir.apply_placements(
            result.path, active,
            width=result.width, height=result.height,
            fps=info.fps or 30.0, has_audio=info.has_audio,
            work_dir=work / f"{clip_id}_imgparts", out=staged,
            codec_args=render_svc._video_codec_args(
                render_svc.RenderRequest(source=result.path, output=staged,
                                         segments=[], quality=settings.video_quality,
                                         hw_accel=settings.hw_accel)),
            audio_args=["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"],
            cancel_event=threading.Event(),
        )
    except PolixorError as exc:
        log.warning("image pass failed for clip %s: %s", clip_id, exc.message)
        return "", exc.message, []
    except Exception as exc:                  # pragma: no cover
        log.exception("image pass crashed for clip %s", clip_id)
        return "", i18n.tr("system.images.apply_failed", error=exc), []

    if out.path == result.path or not out.path.exists():
        return out.note, "; ".join(out.failed), []

    # מחליפים את הקובץ שנוצר בגרסה עם התמונות
    try:
        out.path.replace(result.path)
    except OSError:
        import shutil
        shutil.copy2(out.path, result.path)

    result.duration = out.duration
    result.size_bytes = result.path.stat().st_size

    # שורות הכתוביות ב-DB נשארות על ציר הזמן **ללא** התמונות, והן
    # מקור האמת לכל ייצוא הבא. ההזזה לפי התמונות מתבצעת רק בקריאה
    # (ראו `shifted_cues`). כתיבת ההזזה ל-DB הייתה מצטברת בכל ייצוא
    # מחדש ומרחיקה את הכתוביות מהמקום הנכון.
    #
    # הכתוביות הצרובות בווידאו נשארות מסונכרנות מאליהן: הן נצרבו
    # לפני מעבר התמונות, וההכנסה דוחפת את הווידאו כולו – כולל הן.
    return out.note, "; ".join(out.failed), out.inserts


def _cues_for_render(db: Session, clip_id: str,
                     shift: float = 0.0,
                     inserts: Optional[list[dict[str, float]]] = None,
                     ) -> list[sub_svc.Cue]:
    """
    כתוביות הקליפ לצורך רינדור או ייצוא.

    `shift` מזיז את כולן (שינוי טווח החיתוך). `inserts` מוסיף לכל
    שורה את משך התמונות שנכנסו לפניה – משמש לייצוא SRT בלבד, כי
    הרינדור צורב את הכתוביות **לפני** מעבר התמונות.
    """
    rows = (db.query(SubtitleCue).filter(SubtitleCue.clip_id == clip_id)
            .order_by(SubtitleCue.idx).all())
    out: list[sub_svc.Cue] = []
    for r in rows:
        extra = _shift_amount(r.start, inserts or []) if inserts else 0.0
        start = r.start + shift + extra
        end = r.end + shift + extra
        if end <= 0:
            continue
        words = r.words or []
        delta = shift + extra
        if delta and words:
            words = [{**w, "start": float(w.get("start", 0)) + delta,
                      "end": float(w.get("end", 0)) + delta} for w in words]
        out.append(sub_svc.Cue(start=max(0.0, start), end=max(0.1, end),
                               text=r.text, words=words, language=r.language))
    return out


# --------------------------------------------------------------------------
# הגשת קבצים
# --------------------------------------------------------------------------
@router.get("/clips/{clip_id}/file")
def stream_clip(clip_id: str, request: Request,
                db: Session = Depends(db_dependency)):
    """
    הגשת הווידאו לנגן שבממשק, עם תמיכה ב-Range requests
    כדי שאפשר יהיה לדלג בתוך הקליפ בלי להוריד אותו כולו.
    """
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise _http(ClipNotFoundError())
    path = Path(clip.file_path or "")
    if not path.exists() or not is_within(path, _allowed_roots()):
        raise api_error("file_missing", 404)
    return _ranged_file_response(path, request, _media_type_for(path))


_VIDEO_MEDIA_TYPES = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
    ".webm": "video/webm", ".mkv": "video/x-matroska",
}


def _media_type_for(path: Path) -> str:
    """סוג MIME לפי סיומת – כדי שהנגן בדפדפן יקבל את הכותרת הנכונה."""
    return _VIDEO_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _ranged_file_response(path: Path, request: Request, media_type: str):
    file_size = path.stat().st_size
    range_header = request.headers.get("range") or request.headers.get("Range")
    base_headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": content_disposition(path.name, inline=True),
        "Cache-Control": "no-cache",
    }

    if not range_header:
        return FileResponse(path, media_type=media_type, headers=base_headers)

    m = re.match(r"bytes=(\d*)-(\d*)", range_header.strip())
    if not m:
        return FileResponse(path, media_type=media_type, headers=base_headers)

    start_s, end_s = m.group(1), m.group(2)
    if start_s:
        start = int(start_s)
        end = int(end_s) if end_s else file_size - 1
    else:
        # bytes=-N – N הבייטים האחרונים
        length = int(end_s or 0)
        start = max(0, file_size - length)
        end = file_size - 1

    start = max(0, min(start, file_size - 1))
    end = max(start, min(end, file_size - 1))
    length = end - start + 1

    def _iter(chunk_size: int = 512 * 1024):
        remaining = length
        with path.open("rb") as fh:
            fh.seek(start)
            while remaining > 0:
                data = fh.read(min(chunk_size, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    headers = {
        **base_headers,
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(length),
    }
    return StreamingResponse(_iter(), status_code=206, media_type=media_type,
                             headers=headers)


def keep_custom_thumbnail(current: str, rendered: str) -> str:
    """תמונת שער שנבחרה בסטודיו (clipthumb_*) נשארת אחרי ייצוא מחדש."""
    cur = Path(current) if current else None
    if cur is not None and cur.name.startswith("clipthumb_") and cur.is_file():
        return str(cur)
    return rendered


@router.get("/clips/{clip_id}/thumbnail")
def clip_thumbnail(clip_id: str, db: Session = Depends(db_dependency)):
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise _http(ClipNotFoundError())
    path = Path(clip.thumbnail_path or "")
    if not path.exists() or not is_within(path, _allowed_roots()):
        raise api_error("thumb_missing", 404)
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


@router.get("/clips/{clip_id}/download")
def download_clip(clip_id: str, db: Session = Depends(db_dependency)):
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise _http(ClipNotFoundError())
    path = Path(clip.file_path or "")
    if not path.exists():
        raise api_error("file_missing", 404)
    name = safe_filename(clip.title or clip.id, max_length=70) + ".mp4"
    return FileResponse(path, media_type="video/mp4", filename=name)


@router.post("/clips/download-zip")
def download_zip(payload: ZipRequest, db: Session = Depends(db_dependency)):
    return _zip_response(payload, db)


def _zip_response(payload: ZipRequest, db: Session):
    """
    אורז קליפים נבחרים ל-ZIP, בזרימה: הבייטים הראשונים יוצאים מיד,
    בלי קובץ זמני ובלי להחזיק את הקבצים בזיכרון.
    """
    clips = (db.query(Clip).filter(Clip.id.in_(payload.clip_ids)).all()
             if payload.clip_ids else [])
    available = [c for c in clips if c.file_path and Path(c.file_path).exists()]
    if not available:
        raise api_error("no_files", 404)

    entries: list = []
    used: set[str] = set()
    for c in available:
        base = safe_filename(c.title or c.id, max_length=60)
        name = f"{c.kind.value}_{base}"
        candidate = f"{name}.mp4"
        i = 2
        while candidate in used:
            candidate = f"{name} ({i}).mp4"
            i += 1
        used.add(candidate)
        entries.append((candidate, Path(c.file_path)))
        if payload.include_subtitles:
            cues = _cues_for_render(db, c.id)
            if cues:
                entries.append((candidate.replace(".mp4", ".srt"), sub_svc.srt_text(cues)))

    from ..util.zipstream import stream_zip

    return StreamingResponse(
        stream_zip(entries), media_type="application/zip",
        headers={"Content-Disposition": content_disposition("polixor_clips.zip")},
    )
