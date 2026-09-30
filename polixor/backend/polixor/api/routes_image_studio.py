"""
נתיבי API לסטודיו התמונות (שיחות, הודעות, העלאות, יכולות).

כל הבקשות שמשנות משהו דורשות את הכותרת X-Polixor-Request (הגנת CSRF),
וכל קריאה לספק נעשית בשרת – המפתח לא יוצא ממנו.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from .. import i18n
from ..config import SETTINGS
from ..db import session_scope
from ..errors import PolixorError
from ..models import GeneratedImage
from ..services import image_studio as studio
from .routes_images import image_to_out

router = APIRouter(prefix="/api/image-studio", tags=["image-studio"])


def _guard(request: Request) -> None:
    if request.headers.get("x-polixor-request") != "1":
        raise HTTPException(status_code=403, detail={
            "code": "csrf", "message": i18n.tr("publishing.error.csrf"), "hint": "", "detail": ""})


def _http(exc: PolixorError) -> HTTPException:
    d = exc.to_dict()
    key = (exc.message_key or "").split(".")[-1]
    d["code"] = key or exc.code
    status = 404 if key in ("thread_missing", "message_missing") else 400
    if exc.code in ("image_key_missing",):
        status = 503
    return HTTPException(status_code=status, detail=d)


def _images(ids: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not ids:
        return out
    with session_scope() as s:
        for row in s.query(GeneratedImage).filter(GeneratedImage.id.in_(set(ids))):
            out[row.id] = image_to_out(row).model_dump(mode="json")
    return out


def _with_images(thread: dict[str, Any]) -> dict[str, Any]:
    ids = [m["image_id"] for m in thread.get("messages", []) if m["image_id"]]
    ids += [a for m in thread.get("messages", []) for a in m["attachments"]]
    thread["images"] = _images(ids)
    return thread


class ThreadIn(BaseModel):
    job_id: str = Field(default="", max_length=32)
    title: str = Field(default="", max_length=200)


class RenameIn(BaseModel):
    title: str = Field(default="", max_length=200)


class ThumbnailIn(BaseModel):
    clip_id: str = Field(max_length=32)
    image_id: str = Field(max_length=32)


class MessageIn(BaseModel):
    text: str = Field(default="", max_length=4000)
    attachments: list[str] = Field(default_factory=list, max_length=16)
    aspect: str = Field(default="", max_length=8)
    mode: str = Field(default="auto", max_length=8)
    background: str = Field(default="auto", max_length=16)


@router.get("/capabilities")
def get_capabilities() -> dict[str, Any]:
    return studio.capabilities(SETTINGS.get())


@router.get("/threads")
def list_threads(job_id: str = "") -> dict[str, Any]:
    threads = studio.list_threads(job_id)
    covers = _images([t["cover_image_id"] for t in threads if t["cover_image_id"]])
    for t in threads:
        t["cover"] = covers.get(t["cover_image_id"])
    return {"threads": threads}


@router.post("/threads", status_code=201)
def create_thread(body: ThreadIn, request: Request) -> dict[str, Any]:
    _guard(request)
    return studio.create_thread(job_id=body.job_id, title=body.title)


@router.get("/threads/{thread_id}")
def get_thread(thread_id: str) -> dict[str, Any]:
    try:
        return _with_images(studio.get_thread(thread_id))
    except PolixorError as exc:
        raise _http(exc) from exc


@router.patch("/threads/{thread_id}")
def rename_thread(thread_id: str, body: RenameIn, request: Request) -> dict[str, Any]:
    _guard(request)
    try:
        return studio.rename_thread(thread_id, body.title)
    except PolixorError as exc:
        raise _http(exc) from exc


@router.delete("/threads/{thread_id}")
def delete_thread(thread_id: str, request: Request) -> dict[str, Any]:
    _guard(request)
    return {"deleted": studio.delete_thread(thread_id)}


@router.post("/threads/{thread_id}/messages", status_code=202)
def send_message(thread_id: str, body: MessageIn, request: Request) -> dict[str, Any]:
    _guard(request)
    try:
        out = studio.send(thread_id, body.text, attachments=body.attachments, aspect=body.aspect,
                          mode=body.mode, background=body.background, settings=SETTINGS.get())
    except PolixorError as exc:
        raise _http(exc) from exc
    out["images"] = _images([out["assistant"]["image_id"], *out["user"]["attachments"]])
    return out


@router.post("/messages/{message_id}/retry", status_code=202)
def retry_message(message_id: str, request: Request) -> dict[str, Any]:
    _guard(request)
    try:
        m = studio.retry(message_id, SETTINGS.get())
    except PolixorError as exc:
        raise _http(exc) from exc
    return {"message": m, "images": _images([m["image_id"]])}


@router.post("/uploads", status_code=201)
async def upload_image(request: Request, file: UploadFile = File(...),
                       job_id: Optional[str] = Form(default="")) -> dict[str, Any]:
    _guard(request)
    raw = await file.read(studio.MAX_UPLOAD_BYTES + 1)
    try:
        image_id = studio.upload(raw, file.filename or "image", job_id=job_id or "")
    except PolixorError as exc:
        raise _http(exc) from exc
    return _images([image_id])[image_id]


@router.post("/thumbnail")
def set_thumbnail(body: ThumbnailIn, request: Request) -> dict[str, Any]:
    _guard(request)
    try:
        return studio.set_clip_thumbnail(body.clip_id, body.image_id)
    except PolixorError as exc:
        status = 404 if exc.code in ("clip_not_found",) else 400
        d = exc.to_dict()
        raise HTTPException(status_code=status, detail=d) from exc
