"""
Resumable chunked upload of source videos (services/uploads.py).

  POST   /api/uploads                        start (or resume, same file) – disk space checked first
  GET    /api/uploads/{id}                   state: received / missing chunks, bytes, status
  PUT    /api/uploads/{id}/chunks/{index}    one chunk, raw bytes (optional X-Chunk-Sha256)
  POST   /api/uploads/{id}/complete          all chunks → size → ffprobe → source (idempotent)
  POST   /api/uploads/{id}/telemetry         the browser's own numbers (admin view only)
  DELETE /api/uploads/{id}                   cancel (a finished source is never deleted)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..services import uploads
from ..services.ingest import ALLOWED_UPLOAD_EXT
from .http import api_error

router = APIRouter(prefix="/api", tags=["uploads"])


class CreateUpload(BaseModel):
    filename: str
    size: int
    fingerprint: str = ""          # name|size|lastModified from the browser: the same file resumes
    chunk_size: int = 0            # the browser's pick from its measured link (clamped server-side)


class UploadTelemetry(BaseModel):
    retries: int = 0
    resumes: int = 0
    concurrency: int = 0
    peak_mbps: float = 0.0
    avg_mbps: float = 0.0
    client_seconds: float = 0.0
    paused_seconds: float = 0.0
    hash_worker: bool = False


def _err(exc: uploads.UploadError):
    return api_error(exc.code, exc.status, **{k: str(v) for k, v in exc.params.items()})


def _verify(path: Path) -> dict[str, Any]:
    """ffprobe on the assembled bytes (the browser's MIME type is not trusted)."""
    from ..util.ffmpeg import probe

    info = probe(path)
    if not info.duration or info.duration <= 0 or not (info.width and info.height):
        raise ValueError("no playable video stream")
    return {"duration": round(float(info.duration), 3), "width": int(info.width), "height": int(info.height),
            "file_size": path.stat().st_size}


@router.post("/uploads")
def create_upload(body: CreateUpload) -> dict[str, Any]:
    try:
        from ..services import hardware

        out = uploads.create(body.filename, int(body.size), fingerprint=body.fingerprint,
                             allowed_ext=set(ALLOWED_UPLOAD_EXT), chunk_size=int(body.chunk_size or 0))
        # the browser adapts its parallel chunk requests between 2 and this
        return {**out, "concurrency_max": hardware.upload_concurrency_max()}
    except uploads.UploadError as exc:
        raise _err(exc) from None


@router.get("/uploads/{upload_id}")
def get_upload(upload_id: str) -> dict[str, Any]:
    try:
        return uploads.get(upload_id)
    except uploads.UploadError as exc:
        raise _err(exc) from None


@router.put("/uploads/{upload_id}/chunks/{index}")
async def put_chunk(upload_id: str, index: int, request: Request) -> dict[str, Any]:
    try:
        s = await uploads.write_chunk(upload_id, index, request.stream(),
                                      sha256=request.headers.get("x-chunk-sha256", ""))
    except uploads.UploadError as exc:
        raise _err(exc) from None
    # the reply is small: the browser only needs to know the chunk is in
    return {"upload_id": upload_id, "index": index, "bytes_received": s["bytes_received"],
            "received_count": len(s["received"]), "total_chunks": s["total_chunks"], "status": s["status"]}


@router.post("/uploads/{upload_id}/complete")
def complete_upload(upload_id: str) -> dict[str, Any]:
    try:
        return uploads.complete(upload_id, verify=_verify)
    except uploads.UploadError as exc:
        raise _err(exc) from None


@router.post("/uploads/{upload_id}/telemetry", status_code=204)
def upload_telemetry(upload_id: str, body: UploadTelemetry) -> None:
    """The browser reports what only it measured; it is stored for the admin view, never shown back."""
    try:
        uploads.record_client_telemetry(upload_id, body.model_dump())
    except uploads.UploadError as exc:
        raise _err(exc) from None


@router.delete("/uploads/{upload_id}")
def cancel_upload(upload_id: str) -> dict[str, Any]:
    try:
        uploads.cancel(upload_id)
    except uploads.UploadError as exc:
        raise _err(exc) from None
    return {"cancelled": True}
