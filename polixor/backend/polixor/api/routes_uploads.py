"""
Resumable chunked upload of source videos (services/uploads.py).

  POST   /api/uploads                        start (or resume, same file) – disk space checked first
  GET    /api/uploads/{id}                   state: received / missing chunks, bytes, status
  PUT    /api/uploads/{id}/chunks/{index}    one chunk, raw bytes (optional X-Chunk-Sha256) – older clients
  PUT    /api/uploads/{id}/range?offset=N    one byte range of any size the connection takes (X-Upload-Length)
  GET    /api/uploads/transport              request size / parallelism for this deployment path
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
    hash_seconds: float = 0.0
    hash_bytes: int = 0
    request_seconds: float = 0.0
    request_bytes: int = 0
    queue_wait_seconds: float = 0.0
    requests: int = 0
    request_bytes_max: int = 0


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
def create_upload(body: CreateUpload, request: Request) -> dict[str, Any]:
    try:
        from ..services import hardware

        out = uploads.create(body.filename, int(body.size), fingerprint=body.fingerprint,
                             allowed_ext=set(ALLOWED_UPLOAD_EXT), chunk_size=int(body.chunk_size or 0))
        # the browser adapts its parallel chunk requests between 2 and this
        prof = transport_profile(request)
        return {**out, "concurrency_max": min(prof["concurrency_max"], hardware.upload_concurrency_max()),
                "transport": prof}
    except uploads.UploadError as exc:
        raise _err(exc) from None


class CreateDirect(BaseModel):
    filename: str
    size: int
    fingerprint: str = ""
    content_type: str = "video/mp4"


class SignParts(BaseModel):
    parts: list[int]


def _mp():
    from ..services.object_storage import S3Multipart

    try:
        return S3Multipart()
    except RuntimeError:
        raise api_error("storage_not_configured", 503) from None


def _verify_url(url: str) -> dict[str, Any]:
    """ffprobe reads the stored object through a short-lived signed URL (range requests, not a download)."""
    import json as _json
    import subprocess

    from ..util.ffmpeg import ffprobe_bin

    r = subprocess.run([ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", url],
                       capture_output=True, text=True, timeout=180)
    data = _json.loads(r.stdout or "{}")
    v = next((x for x in data.get("streams") or [] if x.get("codec_type") == "video"), None)
    dur = float((data.get("format") or {}).get("duration") or 0)
    if r.returncode != 0 or not v or dur <= 0:
        raise ValueError("no playable video stream")
    return {"duration": round(dur, 3), "width": int(v.get("width") or 0), "height": int(v.get("height") or 0)}


@router.post("/uploads/direct")
def create_direct(body: CreateDirect) -> dict[str, Any]:
    """Production: a multipart upload the browser sends DIRECTLY to object storage (no bytes through here)."""
    from ..services.object_storage import provider_name

    if provider_name() != "s3":
        raise api_error("storage_not_configured", 503)
    try:
        return uploads.create_direct(body.filename, int(body.size), fingerprint=body.fingerprint,
                                     allowed_ext=set(ALLOWED_UPLOAD_EXT), mp=_mp())
    except uploads.UploadError as exc:
        raise _err(exc) from None


@router.post("/uploads/direct/{upload_id}/sign")
def sign_parts(upload_id: str, body: SignParts) -> dict[str, Any]:
    """Short-lived (15 min) pre-signed PUT URLs for these part numbers – scoped to this one object."""
    try:
        s = uploads.direct_session(upload_id)
    except uploads.UploadError as exc:
        raise _err(exc) from None
    nums = sorted({int(n) for n in body.parts if 1 <= int(n) <= s["parts_total"]})[:100]
    urls = _mp().sign_parts(s["key"], s["multipart_id"], nums)
    return {"urls": {str(k): v for k, v in urls.items()}, "expires_in": 900}


@router.get("/uploads/direct/{upload_id}/parts")
def direct_parts(upload_id: str) -> dict[str, Any]:
    """The parts the storage already holds – the browser resumes from this, after anything."""
    try:
        s = uploads.direct_session(upload_id)
    except uploads.UploadError as exc:
        raise _err(exc) from None
    if s["status"] == "complete":
        return {**uploads.direct_public(s), "parts": []}
    parts = _mp().list_parts(s["key"], s["multipart_id"])
    return {**uploads.direct_public(s), "parts": [{"part_number": p["part_number"], "size": p["size"]} for p in parts]}


@router.post("/uploads/direct/{upload_id}/complete")
def complete_direct(upload_id: str) -> dict[str, Any]:
    try:
        return uploads.complete_direct(upload_id, _mp(), verify_url=_verify_url)
    except uploads.UploadError as exc:
        raise _err(exc) from None


@router.delete("/uploads/direct/{upload_id}")
def abort_direct(upload_id: str) -> dict[str, Any]:
    try:
        uploads.abort_direct(upload_id, _mp())
    except uploads.UploadError as exc:
        raise _err(exc) from None
    return {"cancelled": True}


BENCH_FILE = "upload_bench.json"


def _require_admin(request: Request) -> None:
    from .routes_admin import is_admin

    if not is_admin(request):
        raise api_error("admin_required", 403)


@router.put("/admin/upload-bench/sink")
async def bench_sink(request: Request) -> dict[str, Any]:
    """
    Upload path benchmark (admin): the RAW path – the same proxy / forwarder / server, but the bytes
    are read and dropped (no session, no hash, no disk). What the Polixor path gets compared with.
    """
    import time

    _require_admin(request)
    t0, n = time.perf_counter(), 0
    async for part in request.stream():
        n += len(part)
    return {"bytes": n, "server_seconds": round(time.perf_counter() - t0, 4)}


class BenchResult(BaseModel):
    rows: list[dict[str, Any]]
    best: dict[str, Any]
    protocol: str = ""
    host: str = ""


@router.post("/admin/upload-bench/result")
def bench_result(body: BenchResult, request: Request) -> dict[str, Any]:
    """Keeps the measured grid; the transport profile starts from its best stable setting."""
    import json
    import time

    from ..config import PATHS

    _require_admin(request)
    data = {"at": time.time(), **body.model_dump()}
    (PATHS.data / BENCH_FILE).write_text(json.dumps(data), "utf-8")
    return {"saved": True}


@router.get("/admin/upload-bench/result")
def bench_last(request: Request) -> dict[str, Any]:
    _require_admin(request)
    return _bench() or {}


def _bench() -> dict[str, Any] | None:
    import json

    from ..config import PATHS

    p = PATHS.data / BENCH_FILE
    try:
        return json.loads(p.read_text("utf-8")) if p.exists() else None
    except (OSError, ValueError):
        return None


@router.get("/uploads/transport")
def transport(request: Request) -> dict[str, Any]:
    """The request sizes and parallelism this deployment path is known to take (see transport_profile)."""
    return transport_profile(request)


def transport_profile(request: Request) -> dict[str, Any]:
    """
    Deployment-aware upload transport. Behind the GitHub Codespaces port forwarder
    (*.app.github.dev) big request bodies are refused with HTTP 413 before they reach Polixor,
    so there the browser starts small (4 MiB), may grow only after a size succeeded, never past
    8 MiB, and keeps few requests in flight. Elsewhere it starts at 8 MiB and may grow to 32 MiB.
    The env POLIXOR_UPLOAD_MAX_REQUEST_BYTES caps both (another proxy in front, e.g. nginx).
    The browser shrinks further by itself on any 413.
    """
    import os

    from ..services import hardware

    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(":")[0].lower()
    codespaces = host.endswith(".app.github.dev") or host.endswith(".github.dev") \
        or os.environ.get("CODESPACES", "").lower() == "true"
    mib = 1024 * 1024
    if codespaces:
        prof = {"profile": "codespaces", "start_bytes": 4 * mib, "max_bytes": 8 * mib,
                "min_bytes": 1 * mib, "concurrency_start": 3, "concurrency_max": 6, "request_timeout_s": 300}
    else:
        prof = {"profile": "default", "start_bytes": 8 * mib, "max_bytes": 32 * mib, "min_bytes": 1 * mib,
                "concurrency_start": 2, "concurrency_max": hardware.upload_concurrency_max(),
                "request_timeout_s": 600}
    best = (_bench() or {}).get("best") or {}
    if best.get("request_bytes") and best.get("concurrency") and best.get("polixor_MBps"):
        # measured on this deployment (admin → Measure upload path): start at the best stable setting
        rb = int(best["request_bytes"])
        prof["start_bytes"] = max(prof["min_bytes"], min(prof["max_bytes"], rb))
        prof["concurrency_start"] = max(1, min(int(prof["concurrency_max"]), int(best["concurrency"])))
        prof["profile"] += "+measured"
    from ..services.object_storage import provider_name

    prof["storage"] = "s3_multipart" if provider_name() == "s3" else "local_resumable"
    cap = os.environ.get("POLIXOR_UPLOAD_MAX_REQUEST_BYTES", "").strip()
    if cap.isdigit() and int(cap) >= mib:
        prof["max_bytes"] = min(prof["max_bytes"], int(cap))
        prof["start_bytes"] = min(prof["start_bytes"], prof["max_bytes"])
        prof["profile"] += "+capped"
    return prof


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


@router.put("/uploads/{upload_id}/range")
async def put_range(upload_id: str, offset: int, request: Request) -> dict[str, Any]:
    """
    One byte range of the file, raw bytes. Its length comes from X-Upload-Length (or
    Content-Length): the browser picks the request size for the connection it is on.
    """
    raw = request.headers.get("x-upload-length") or request.headers.get("content-length") or ""
    try:
        length = int(raw)
    except ValueError:
        raise _err(uploads.UploadError("upload_bad_range", 400, offset=offset, length=raw or "?")) from None
    try:
        s = await uploads.write_range(upload_id, offset, length, request.stream(),
                                      sha256=request.headers.get("x-chunk-sha256", ""))
    except uploads.UploadError as exc:
        raise _err(exc) from None
    return {"upload_id": upload_id, "offset": offset, "length": length, "bytes_received": s["bytes_received"],
            "status": s["status"]}


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
