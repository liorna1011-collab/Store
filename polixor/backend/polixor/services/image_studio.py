"""
סטודיו תמונות AI – עבודה בשיחה, כמו ב-ChatGPT: כותבים מה רוצים, מצרפים
תמונות ייחוס, מקבלים תמונה, וממשיכים לבקש שינויים עליה.

  * שיחה (ImageThread) עם היסטוריית הודעות (ImageMessage) שנשמרת ב-DB.
  * כל תשובה היא GeneratedImage רגילה – ולכן היא גם בגלריה, ואפשר לשבץ
    אותה בסרטון (אינטרו/אאוטרו/B-roll/שכבה/רקע/תמונת שער) בנתיב הקיים.
  * עריכה חוזרת: הבקשה הבאה עורכת את התמונה האחרונה בשיחה (Images API,
    /images/edits) עם ההוראות הקודמות כהקשר. המודלים החדשים של OpenAI לא
    זמינים בכלי התמונות של Responses API, ולכן ההיסטוריה מנוהלת כאן.
  * כמה תמונות ייחוס – עד המגבלה של המודל (שכבת היכולות).
  * העלאות: PNG/JPEG/WEBP בלבד, נבדקות ומקודדות מחדש ל-PNG (מסיר מטא-דאטה
    ותוכן שאינו תמונה), ונשמרות כנכס "הועלה" (לא AI).
  * המפתח של OpenAI נשאר בשרת; שום דבר כאן לא מחזיר אותו.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path
from typing import Any, Optional

from .. import i18n
from ..config import PATHS, SETTINGS, AppSettings
from ..db import session_scope
from ..errors import PolixorError
from ..models import GeneratedImage, ImageMessage, ImageStatus, ImageThread, new_id, utcnow
from . import image_assets, image_models
from . import images as img_svc

log = logging.getLogger("polixor.images.studio")

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_UPLOAD_EDGE = 4096
UPLOAD_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
MODES = ("auto", "new", "edit")
BACKGROUNDS = ("auto", "opaque", "transparent")
HISTORY_TURNS = 4                      # כמה הוראות קודמות נכנסות כהקשר לעריכה


class StudioError(PolixorError):
    code = "studio_error"
    message = "הבקשה לסטודיו התמונות לא תקינה."


def _err(key: str, **params: Any) -> StudioError:
    return StudioError(message_key=f"image_studio.error.{key}", params=params)


# --------------------------------------------------------------------------
# יכולות
# --------------------------------------------------------------------------
def capabilities(settings: Optional[AppSettings] = None) -> dict[str, Any]:
    st = settings or SETTINGS.get()
    caps = image_models.capabilities(st.image_provider, st.image_model)
    provider = img_svc.get_provider(st.image_provider)
    ok, why = provider.available()
    caps.update({"ready": ok, "reason": why, "is_ai": provider.is_ai,
                 "aspects": list(img_svc.ASPECTS), "modes": list(MODES),
                 "backgrounds": ["auto", "opaque"] + (["transparent"] if caps["model"]["transparent"] else []),
                 "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
                 "upload_types": sorted(set(UPLOAD_FORMATS.values())),
                 "max_prompt": img_svc.MAX_PROMPT})
    return caps


# --------------------------------------------------------------------------
# שיחות
# --------------------------------------------------------------------------
def _thread_dict(t: ImageThread, messages: Optional[list[ImageMessage]] = None) -> dict[str, Any]:
    d = {"id": t.id, "job_id": t.job_id or "", "title": t.title or "",
         "created_at": t.created_at.isoformat() if t.created_at else "",
         "updated_at": t.updated_at.isoformat() if t.updated_at else "",
         "cover_image_id": t.cover_image_id or ""}
    if messages is not None:
        d["messages"] = [_message_dict(m) for m in messages]
    return d


def _message_dict(m: ImageMessage) -> dict[str, Any]:
    return {"id": m.id, "thread_id": m.thread_id, "role": m.role, "text": m.text or "",
            "attachments": list(m.attachments or []), "image_id": m.image_id or "",
            "mode": m.mode or "", "aspect": m.aspect or "", "background": m.background or "",
            "created_at": m.created_at.isoformat() if m.created_at else ""}


def create_thread(*, job_id: str = "", title: str = "") -> dict[str, Any]:
    tid = new_id()
    with session_scope() as s:
        t = ImageThread(id=tid, job_id=job_id or None, title=_title(title))
        s.add(t)
        s.flush()
        return _thread_dict(t, [])


def _title(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())[:80]


def list_threads(job_id: str = "") -> list[dict[str, Any]]:
    with session_scope() as s:
        q = s.query(ImageThread)
        if job_id:
            q = q.filter(ImageThread.job_id == job_id)
        return [_thread_dict(t) for t in q.order_by(ImageThread.updated_at.desc()).limit(200)]


def get_thread(thread_id: str) -> dict[str, Any]:
    with session_scope() as s:
        t = s.get(ImageThread, thread_id)
        if t is None:
            raise _err("thread_missing")
        msgs = (s.query(ImageMessage).filter(ImageMessage.thread_id == thread_id)
                .order_by(ImageMessage.idx).all())
        return _thread_dict(t, msgs)


def rename_thread(thread_id: str, title: str) -> dict[str, Any]:
    with session_scope() as s:
        t = s.get(ImageThread, thread_id)
        if t is None:
            raise _err("thread_missing")
        t.title = _title(title)
        return _thread_dict(t)


def delete_thread(thread_id: str) -> bool:
    """מוחק את השיחה. התמונות שנוצרו נשארות בגלריה (הן נכסים, ואולי משובצות)."""
    with session_scope() as s:
        t = s.get(ImageThread, thread_id)
        if t is None:
            return False
        s.query(ImageMessage).filter(ImageMessage.thread_id == thread_id).delete()
        s.delete(t)
    return True


# --------------------------------------------------------------------------
# העלאות
# --------------------------------------------------------------------------
def upload(raw: bytes, filename: str, *, job_id: str = "") -> str:
    """בודק ומקודד מחדש קובץ תמונה, ושומר אותו כנכס "הועלה"."""
    if not raw:
        raise _err("upload_empty")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise _err("upload_too_big", mb=MAX_UPLOAD_BYTES // (1024 * 1024))
    try:
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = 60_000_000             # הגנה מפני "פצצת פיקסלים"
        with Image.open(io.BytesIO(raw)) as im:
            fmt = (im.format or "").upper()
            if fmt not in UPLOAD_FORMATS:
                raise _err("upload_type")
            im.load()
            im = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
            if max(im.size) > MAX_UPLOAD_EDGE:
                im.thumbnail((MAX_UPLOAD_EDGE, MAX_UPLOAD_EDGE), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "PNG", optimize=True)
            w, h = im.size
    except StudioError:
        raise
    except Exception as exc:                            # noqa: BLE001
        raise _err("upload_invalid") from exc
    image_id = new_id()
    name = re.sub(r"[^\w.\- ]+", "", Path(filename or "image").name, flags=re.UNICODE)[:80] or "image"
    saved = img_svc.save_image(img_svc.GeneratedImageData(
        data=buf.getvalue(), width=w, height=h, provider="upload", model="", is_ai=False), image_id)
    with session_scope() as s:
        s.add(GeneratedImage(
            id=image_id, job_id=job_id or None, prompt=name, aspect=img_svc.aspect_of(w, h),
            status=ImageStatus.READY, provider="upload", model="", is_ai=False,
            file_path=saved["file_path"], thumb_path=saved["thumb_path"],
            file_size=saved["size_bytes"], width=w, height=h,
            meta={"kind": "upload", "filename": name}))
    return image_id


# --------------------------------------------------------------------------
# שליחת הודעה
# --------------------------------------------------------------------------
def _ready_path(s, image_id: str) -> Path:
    row = s.get(GeneratedImage, image_id)
    if row is None or row.status != ImageStatus.READY or not row.file_path \
            or not Path(row.file_path).exists():
        raise _err("attachment_missing")
    return Path(row.file_path)


def compose_prompt(text: str, *, history: list[str], editing: bool, refs: int) -> str:
    """ההוראה שנשלחת למודל: ההוראה החדשה, ההקשר הקודם ותפקיד כל תמונת קלט."""
    parts: list[str] = []
    if editing:
        parts.append("Edit the first input image according to the new instruction. "
                     "Keep everything that the instruction does not ask to change "
                     "(subject, composition, style, text).")
        if history:
            parts.append("Earlier instructions in this conversation, already applied: " +
                         " | ".join(history[-HISTORY_TURNS:]))
        if refs > 1:
            parts.append(f"The other {refs - 1} input image(s) are references to use as the "
                         "instruction says.")
    elif refs:
        parts.append(f"Use the {refs} input image(s) as references for the new image.")
    parts.append("New instruction: " + text if parts else text)
    out = "\n".join(parts)
    return out[-img_svc.MAX_COMPOSED_PROMPT:]


def send(thread_id: str, text: str, *, attachments: Optional[list[str]] = None,
         aspect: str = "", mode: str = "auto", background: str = "auto",
         settings: Optional[AppSettings] = None) -> dict[str, Any]:
    """הודעת משתמש + תשובה (תמונה בתור). מחזיר את שתי ההודעות."""
    st = settings or SETTINGS.get()
    text = img_svc.validate_prompt(text)
    attachments = [a for a in dict.fromkeys(attachments or []) if a]
    if mode not in MODES:
        raise _err("bad_mode")
    if background not in BACKGROUNDS:
        raise _err("bad_background")
    model = image_models.for_provider(st.image_provider, st.image_model)
    if background == "transparent" and not model.transparent:
        raise _err("no_transparent")

    with session_scope() as s:
        t = s.get(ImageThread, thread_id)
        if t is None:
            raise _err("thread_missing")
        msgs = (s.query(ImageMessage).filter(ImageMessage.thread_id == thread_id)
                .order_by(ImageMessage.idx).all())
        base_id = ""
        for m in reversed(msgs):
            if m.role == "assistant" and m.image_id:
                row = s.get(GeneratedImage, m.image_id)
                if row is not None and row.status == ImageStatus.READY and row.file_path:
                    base_id = row.id
                    break
        if mode == "edit" and not base_id:
            raise _err("nothing_to_edit")
        editing = bool(base_id) and mode != "new"
        source_ids = ([base_id] if editing else []) + [a for a in attachments if a != base_id]
        if source_ids and not model.edit:
            raise _err("no_edit", model=model.id)
        if len(source_ids) > model.max_refs:
            raise _err("too_many_refs", max=model.max_refs)
        sources = [_ready_path(s, i) for i in source_ids]
        if not aspect:
            base = s.get(GeneratedImage, base_id) if editing else None
            aspect = base.aspect if base is not None else "9:16"
        aspect = img_svc.validate_aspect(aspect)
        history = [m.text for m in msgs if m.role == "user" and m.text]
        job_id = t.job_id or ""
        next_idx = (msgs[-1].idx + 1) if msgs else 0

    composed = compose_prompt(text, history=history, editing=editing, refs=len(sources))
    image_id = image_assets.create_image_row(prompt=text, aspect=aspect, job_id=job_id or None,
                                             parent_id=base_id or None, settings=st)
    with session_scope() as s:
        row = s.get(GeneratedImage, image_id)
        row.meta = {**(row.meta or {}), "studio": {
            "thread_id": thread_id, "mode": "edit" if editing else ("refs" if sources else "generate"),
            "sources": source_ids, "background": background, "composed_prompt": composed}}
        user = ImageMessage(id=new_id(), thread_id=thread_id, idx=next_idx, role="user", text=text,
                            attachments=attachments, mode=mode, aspect=aspect, background=background)
        bot = ImageMessage(id=new_id(), thread_id=thread_id, idx=next_idx + 1, role="assistant",
                           image_id=image_id, mode="edit" if editing else "generate",
                           aspect=aspect, background=background)
        s.add_all([user, bot])
        t = s.get(ImageThread, thread_id)
        if not t.title:
            t.title = _title(text)
        t.updated_at = utcnow()
        t.cover_image_id = image_id
        out = {"user": _message_dict(user), "assistant": _message_dict(bot)}
    image_assets.start_generation(image_id, sources=sources, prompt=composed,
                                  background=background, settings=st)
    return out


def retry(message_id: str, settings: Optional[AppSettings] = None) -> dict[str, Any]:
    """מריץ שוב תשובה שנכשלה/בוטלה – אותה בקשה, אותם מקורות."""
    st = settings or SETTINGS.get()
    with session_scope() as s:
        m = s.get(ImageMessage, message_id)
        if m is None or m.role != "assistant" or not m.image_id:
            raise _err("message_missing")
        row = s.get(GeneratedImage, m.image_id)
        if row is None:
            raise _err("message_missing")
        if row.status in (ImageStatus.QUEUED, ImageStatus.GENERATING):
            return _message_dict(m)
        studio = dict((row.meta or {}).get("studio") or {})
        sources = [_ready_path(s, i) for i in studio.get("sources") or []]
        row.status = ImageStatus.QUEUED
        row.error = row.error_code = ""
        out = _message_dict(m)
        image_id = row.id
    image_assets.start_generation(image_id, sources=sources,
                                  prompt=studio.get("composed_prompt") or None,
                                  background=studio.get("background") or "auto", settings=st)
    return out


# --------------------------------------------------------------------------
# תמונת שער לקליפ
# --------------------------------------------------------------------------
THUMB_MAX_BYTES = 2 * 1024 * 1024       # המגבלה המחמירה (YouTube); מתאים לכל הפלטפורמות


def set_clip_thumbnail(clip_id: str, image_id: str) -> dict[str, Any]:
    """
    הופך תמונה לתמונת השער של הקליפ: חיתוך ליחס הקליפ, 1280 בצלע הארוכה,
    JPEG עד 2MB. נשמר ב-clip.thumbnail_path – ומשם הפרסום שולח אותו
    לפלטפורמות שתומכות בתמונה ממוזערת. נרשם גם כשיבוץ מסוג "תמונת שער".
    """
    from PIL import Image, ImageOps

    from ..models import Clip

    with session_scope() as s:
        clip = s.get(Clip, clip_id)
        if clip is None:
            raise image_assets.ClipNotFoundError()
        src = _ready_path(s, image_id)
        w, h = int(clip.width or 0), int(clip.height or 0)
    size = (720, 1280) if h > w else (1280, 720)
    with Image.open(src) as im:
        im = ImageOps.fit(im.convert("RGB"), size, Image.LANCZOS)
        folder = PATHS.exports / "thumbnails"          # תיקייה שמותר להגיש ממנה קבצי קליפ
        folder.mkdir(parents=True, exist_ok=True)
        out = folder / f"clipthumb_{clip_id}_{image_id}.jpg"
        for q in (90, 82, 72, 60):
            im.save(out, "JPEG", quality=q, optimize=True)
            if out.stat().st_size <= THUMB_MAX_BYTES:
                break
    from ..models import ImagePlacement, ImageRole

    with session_scope() as s:
        clip = s.get(Clip, clip_id)
        clip.thumbnail_path = str(out)
        # תמונת שער אחת לקליפ
        (s.query(ImagePlacement).filter(ImagePlacement.clip_id == clip_id,
                                        ImagePlacement.role == ImageRole.THUMBNAIL).delete())
    pid = image_assets.add_placement(clip_id=clip_id, image_id=image_id, role="thumbnail")
    return {"clip_id": clip_id, "thumbnail_url": f"/api/clips/{clip_id}/thumbnail",
            "width": size[0], "height": size[1], "placement_id": pid}
