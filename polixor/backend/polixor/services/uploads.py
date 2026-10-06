"""
Resumable, chunked upload of source videos (multi-GB sources through a browser and a proxy).

A session per file:

    <data>/uploads/<upload_id>/session.json   state (atomic writes)
    <data>/uploads/<upload_id>/data.part      the file being assembled, preallocated (sparse)

Each chunk is streamed from the request straight to its offset in data.part (pwrite):
no chunk is held in memory, nothing is assembled or copied at the end. A chunk counts
as received only after all of its bytes were written (and its SHA-256 matched, when the
browser sent one), so a broken request never leaves a hole marked as done. Chunks may
arrive in any order, in parallel, and more than once.

complete(): every chunk present → size checked → ffprobe → atomic rename into the sources
folder. Only then does the file exist as a source; a half upload never does. complete()
is idempotent: the same session always resolves to the same source.

Disk safety: a session is refused up front when the free space minus what other open
sessions still need would drop below SAFETY_MARGIN; a full disk during an upload is
reported as such. Abandoned sessions expire after SESSION_TTL without activity; cleanup
never touches finished sources (they live outside the uploads folder).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from ..config import PATHS
from ..util.fs import safe_filename, unique_path

CHUNK_SIZE = int(os.environ.get("POLIXOR_UPLOAD_CHUNK_BYTES", 16 * 1024 * 1024))
SAFETY_MARGIN = int(os.environ.get("POLIXOR_UPLOAD_MARGIN_BYTES", 2 * 1024 ** 3))
SESSION_TTL = 48 * 3600            # an abandoned incomplete upload is kept this long (resumable)
DONE_TTL = 7 * 24 * 3600           # metadata of finished sessions (the source itself stays)
MAX_SIZE = 200 * 1024 ** 3
# a proxy in front (Codespaces, nginx defaults) refuses or stalls very large request bodies:
# a chunk never exceeds 32 MB, and is never so small that per-request overhead dominates
CHUNK_MIN = 4 * 1024 * 1024
CHUNK_MAX = 32 * 1024 * 1024
RANGE_MAX = 64 * 1024 * 1024       # one range request at most (the browser stays far below behind a proxy)
WRITE_PIECE = 1024 * 1024          # bytes hashed + written per worker-thread hop
VERIFY_STALE = 15 * 60             # a "verifying" session idle this long was interrupted
_ID = re.compile(r"^[0-9a-f]{32}$")
_LOCK = threading.Lock()


class UploadError(Exception):
    def __init__(self, code: str, status: int = 400, **params: Any) -> None:
        super().__init__(code)
        self.code, self.status, self.params = code, status, params


def root() -> Path:
    d = PATHS.data / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _dir(upload_id: str) -> Path:
    if not _ID.match(upload_id or ""):
        raise UploadError("upload_not_found", 404)          # also blocks path traversal
    return root() / upload_id


def _load(upload_id: str) -> dict[str, Any]:
    p = _dir(upload_id) / "session.json"
    try:
        return json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        raise UploadError("upload_not_found", 404) from None


def _save(s: dict[str, Any]) -> None:
    s["updated_at"] = time.time()
    p = _dir(s["upload_id"]) / "session.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False), "utf-8")
    os.replace(tmp, p)


def total_chunks(size: int, chunk: int) -> int:
    return max(1, -(-size // chunk))


def chunk_length(s: dict[str, Any], index: int) -> int:
    start = index * s["chunk_size"]
    return max(0, min(s["chunk_size"], s["size"] - start))


def public(s: dict[str, Any]) -> dict[str, Any]:
    """What the browser sees: never a filesystem path."""
    n = s["total_chunks"]
    rs = ranges_of(s)
    rec = _received_chunks(s, rs) if "ranges" in s else sorted(set(s["received"]))
    got = sum(b - a for a, b in rs)
    return {"upload_id": s["upload_id"], "filename": s["filename"], "size": s["size"],
            "chunk_size": s["chunk_size"], "total_chunks": n, "received": rec,
            "missing": [i for i in range(n) if i not in set(rec)][:2000],
            "ranges": rs[:5000], "bytes_received": got, "status": s["status"], "error": s.get("error") or "",
            "result": s.get("result"), "created_at": s["created_at"], "updated_at": s["updated_at"]}


def _pending_bytes(exclude: str = "") -> int:
    """Bytes the other open sessions still need (so two big uploads don't both pass the check)."""
    total = 0
    for d in root().iterdir():
        if d.name == exclude or not (d / "session.json").exists():
            continue
        try:
            s = json.loads((d / "session.json").read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if s.get("status") in ("uploading",):
            total += max(0, s["size"] - covered(s))
    return total


def check_space(need: int, exclude: str = "") -> None:
    free = shutil.disk_usage(root()).free
    avail = free - _pending_bytes(exclude) - SAFETY_MARGIN
    if need > avail:
        raise UploadError("upload_no_space", 507, need=_gb(need), free=_gb(max(0, free - SAFETY_MARGIN)))


def _gb(n: int) -> str:
    return f"{n / 1024 ** 3:.1f} GB"


def pick_chunk_size(requested: int = 0) -> int:
    """
    The chunk size of a new session: what the browser asks for (it measured its own link)
    within CHUNK_MIN..CHUNK_MAX, whole MB. Fixed for the session's life – a resume after a
    refresh needs the same indexes – so adapting happens per upload, never inside one.
    """
    if not requested:
        return CHUNK_SIZE
    mb = 1024 * 1024
    return max(CHUNK_MIN, min(CHUNK_MAX, (int(requested) // mb) * mb))


def create(filename: str, size: int, *, fingerprint: str = "", allowed_ext: set[str],
           chunk_size: int = 0) -> dict[str, Any]:
    name = safe_filename(filename or "video.mp4", max_length=120)
    ext = Path(name).suffix.lower()
    if ext not in allowed_ext:
        raise UploadError("unsupported_format", 400, ext=ext or "(-)", formats=", ".join(sorted(allowed_ext)))
    if size <= 0:
        raise UploadError("empty_file", 400)
    if size > MAX_SIZE:
        raise UploadError("upload_too_large", 413, max=_gb(MAX_SIZE))
    cleanup()
    fp = (fingerprint or "")[:300]
    with _LOCK:
        if fp:
            # the same file again (page refreshed, browser reopened): continue that upload
            for d in root().iterdir():
                try:
                    s = json.loads((d / "session.json").read_text("utf-8"))
                except (OSError, ValueError):
                    continue
                if s.get("fingerprint") == fp and s.get("size") == size and \
                        s.get("status") in ("uploading", "complete") and \
                        (s["status"] == "complete" or (d / "data.part").exists()):
                    if s["status"] == "complete" and not (PATHS.sources / s["result"]["upload_token"]).exists():
                        continue
                    return public(s)
        check_space(size)
        uid = uuid.uuid4().hex
        d = root() / uid
        d.mkdir(parents=True)
        with (d / "data.part").open("wb") as f:
            f.truncate(size)                     # sparse: no space is used until bytes arrive
        now = time.time()
        cs = pick_chunk_size(chunk_size)
        s = {"upload_id": uid, "filename": name, "size": size, "fingerprint": fp,
             "chunk_size": cs, "total_chunks": total_chunks(size, cs), "received": [],
             "status": "uploading", "error": "", "result": None, "created_at": now, "updated_at": now}
        _save(s)
        return public(s)


def get(upload_id: str) -> dict[str, Any]:
    return public(_load(upload_id))


async def write_chunk(upload_id: str, index: int, body: AsyncIterator[bytes], *,
                      sha256: str = "") -> dict[str, Any]:
    """One chunk of the session's fixed geometry (kept for older clients): a byte range."""
    import anyio

    s = await anyio.to_thread.run_sync(_load, upload_id)
    if s["status"] == "complete":
        return public(s)
    if not 0 <= index < s["total_chunks"]:
        raise UploadError("upload_bad_chunk", 400, index=index)
    return await write_range(upload_id, index * s["chunk_size"], chunk_length(s, index), body, sha256=sha256)


async def write_range(upload_id: str, offset: int, length: int, body: AsyncIterator[bytes], *,
                      sha256: str = "") -> dict[str, Any]:
    """
    Streams one byte range [offset, offset+length) of the file to its place. The request size
    is the browser's choice (it shrinks it when a proxy refuses big requests – HTTP 413 – and
    grows it only after a size was proven); the server only checks that the range lies inside
    the file and is at most RANGE_MAX. A range counts only after all of its bytes were written
    (and its SHA-256 matched): a broken request never leaves a hole marked as done. Ranges may
    overlap, arrive in any order, in parallel, and more than once.

    Holds at most ~1 MB in memory; hashing and writing run in a worker thread, so the event
    loop (which serves every other request) never waits for them.
    """
    import anyio

    s = await anyio.to_thread.run_sync(_load, upload_id)
    if s["status"] == "complete":
        return public(s)                         # a retry after completion: nothing to do
    if s["status"] != "uploading":
        raise UploadError("upload_closed", 409, status=s["status"])
    if offset < 0 or length <= 0 or offset + length > s["size"] or length > RANGE_MAX:
        raise UploadError("upload_bad_range", 400, offset=offset, length=length)
    expected = length
    part = _dir(upload_id) / "data.part"
    h = hashlib.sha256() if sha256 else None
    written = 0
    buf: list[bytes] = []
    buffered = 0

    def flush(data: bytes, at: int) -> None:
        if h is not None:
            h.update(data)
        os.pwrite(fd, data, at)

    async def drain() -> None:
        nonlocal buf, buffered, written
        if not buffered:
            return
        data, at = b"".join(buf), offset + written
        buf, buffered = [], 0
        try:
            await anyio.to_thread.run_sync(flush, data, at)
        except OSError as exc:
            if exc.errno == 28:                  # ENOSPC
                raise UploadError("upload_no_space", 507, need=_gb(s["size"]), free="0.0 GB") from exc
            raise UploadError("upload_write_failed", 500) from exc
        written += len(data)

    fd = os.open(part, os.O_WRONLY)
    try:
        async for piece in body:
            if not piece:
                continue
            if written + buffered + len(piece) > expected:
                raise UploadError("upload_bad_chunk_size", 400, offset=offset, expected=expected)
            buf.append(piece)
            buffered += len(piece)
            if buffered >= WRITE_PIECE:
                await drain()
        await drain()
    except UploadError as exc:
        await anyio.to_thread.run_sync(_note, upload_id, "failed_chunks")
        raise exc
    finally:
        os.close(fd)
    if written != expected:
        # an interrupted request: the range is not marked received – the browser sends it again
        await anyio.to_thread.run_sync(_note, upload_id, "failed_chunks")
        raise UploadError("upload_bad_chunk_size", 400, offset=offset, expected=expected, got=written)
    if h is not None and h.hexdigest() != sha256.lower():
        await anyio.to_thread.run_sync(_note, upload_id, "failed_chunks")
        raise UploadError("upload_checksum", 400, offset=offset)
    return await anyio.to_thread.run_sync(_mark_range, upload_id, offset, offset + length)


def merge_ranges(ranges: list[list[int]]) -> list[list[int]]:
    out: list[list[int]] = []
    for a, b in sorted([int(x[0]), int(x[1])] for x in ranges if int(x[1]) > int(x[0])):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def ranges_of(s: dict[str, Any]) -> list[list[int]]:
    """The confirmed byte ranges (sessions from before ranges existed: from their chunk list)."""
    if "ranges" in s:
        return merge_ranges(s["ranges"])
    return merge_ranges([[i * s["chunk_size"], i * s["chunk_size"] + chunk_length(s, i)]
                         for i in set(s.get("received") or [])])


def covered(s: dict[str, Any]) -> int:
    return sum(b - a for a, b in ranges_of(s))


def _received_chunks(s: dict[str, Any], rs: list[list[int]]) -> list[int]:
    out = []
    for i in range(s["total_chunks"]):
        a, b = i * s["chunk_size"], i * s["chunk_size"] + chunk_length(s, i)
        if any(x <= a and b <= y for x, y in rs):
            out.append(i)
    return out


def _mark_range(upload_id: str, a: int, b: int) -> dict[str, Any]:
    with _LOCK:
        s = _load(upload_id)
        t = s.setdefault("telemetry", {})
        now = time.time()
        t.setdefault("first_chunk_at", now)
        t["last_chunk_at"] = now
        rs = ranges_of(s)
        if any(x <= a and b <= y for x, y in rs):
            t["duplicate_chunks"] = int(t.get("duplicate_chunks", 0)) + 1   # a retry of a range that was in
        rs = merge_ranges(rs + [[a, b]])
        s["ranges"] = rs
        s["received"] = _received_chunks(s, rs)
        t["max_request_ok"] = max(int(t.get("max_request_ok") or 0), b - a)
        _save(s)
    return public(s)


def _mark_received(upload_id: str, index: int) -> dict[str, Any]:
    s = _load(upload_id)
    a = index * s["chunk_size"]
    return _mark_range(upload_id, a, a + chunk_length(s, index))


def _note(upload_id: str, key: str) -> None:
    with _LOCK:
        try:
            s = _load(upload_id)
        except UploadError:
            return
        t = s.setdefault("telemetry", {})
        t[key] = int(t.get(key, 0)) + 1
        _save(s)


def complete(upload_id: str, *, verify) -> dict[str, Any]:
    """
    All chunks → size → ffprobe (`verify(path)` returns the probe result or raises) → atomic
    rename into the sources folder. Idempotent.
    """
    with _LOCK:
        s = _load(upload_id)
        if s["status"] == "complete":
            return public(s)
        if s["status"] == "verifying" and not _stale_verify(s):
            raise UploadError("upload_busy", 409)
        have = covered(s)
        if have < s["size"]:
            missing = [i for i in range(s["total_chunks"]) if i not in set(public(s)["received"])]
            raise UploadError("upload_incomplete", 409, count=len(missing) or 1,
                              first=missing[0] if missing else 0)
        part = _dir(upload_id) / "data.part"
        if not part.exists() or part.stat().st_size != s["size"]:
            raise UploadError("upload_size_mismatch", 409)
        s["status"] = "verifying"
        _save(s)
    t0 = time.time()
    try:
        info = verify(part)
    except Exception as exc:                    # noqa: BLE001
        with _LOCK:
            s = _load(upload_id)
            s["status"], s["error"] = "failed", "invalid_video"
            _save(s)
        part.unlink(missing_ok=True)            # unreadable bytes are not kept
        raise UploadError("upload_invalid_video", 422, reason=str(getattr(exc, "message", exc))[:200]) from exc
    PATHS.sources.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        dest = unique_path(PATHS.sources / s["filename"])
        os.replace(part, dest)                  # atomic: same filesystem (<data>/uploads → <data>/sources)
        s = _load(upload_id)
        s["status"] = "complete"
        s["result"] = {"upload_token": dest.name, **info}
        t = s.setdefault("telemetry", {})
        t["finalize_seconds"] = round(time.time() - t0, 2)
        t["completed_at"] = time.time()
        _save(s)
    return public(s)


def _stale_verify(s: dict[str, Any]) -> bool:
    """A verification interrupted by a server stop: it may be started again (never stuck)."""
    return time.time() - float(s.get("updated_at") or 0) > VERIFY_STALE


def record_client_telemetry(upload_id: str, data: dict[str, Any]) -> None:
    """What only the browser knows (retries, peak speed, resumes, concurrency). Admin-only data."""
    allowed = {"retries": int, "resumes": int, "concurrency": int, "peak_mbps": float, "avg_mbps": float,
               "client_seconds": float, "paused_seconds": float, "hash_worker": bool}
    clean: dict[str, Any] = {}
    for k, typ in allowed.items():
        if k in data:
            try:
                clean[k] = typ(data[k])
            except (TypeError, ValueError):
                continue
    with _LOCK:
        s = _load(upload_id)
        s.setdefault("telemetry", {}).setdefault("client", {}).update(clean)
        _save(s)


def telemetry(s: dict[str, Any]) -> dict[str, Any]:
    """One upload's numbers for the admin view."""
    t = s.get("telemetry") or {}
    c = t.get("client") or {}
    first, last = t.get("first_chunk_at"), t.get("last_chunk_at")
    secs = (last - first) if first and last and last > first else None
    received = sum(chunk_length(s, i) for i in set(s.get("received") or []))
    return {"upload_id": s["upload_id"], "size": s["size"], "status": s["status"],
            "chunk_size": s["chunk_size"], "total_chunks": s["total_chunks"],
            "transfer_seconds": round(secs, 1) if secs else None,
            "avg_mbps": round(received * 8 / secs / 1e6, 1) if secs else c.get("avg_mbps"),
            "peak_mbps": c.get("peak_mbps"), "retries": c.get("retries", 0),
            "duplicate_chunks": t.get("duplicate_chunks", 0), "failed_chunks": t.get("failed_chunks", 0),
            "resumes": c.get("resumes", 0), "concurrency": c.get("concurrency"),
            "finalize_seconds": t.get("finalize_seconds"), "created_at": s.get("created_at"),
            "completed_at": t.get("completed_at")}


def all_telemetry(limit: int = 100) -> list[dict[str, Any]]:
    out = []
    for d in root().iterdir():
        try:
            out.append(telemetry(json.loads((d / "session.json").read_text("utf-8"))))
        except (OSError, ValueError, KeyError):
            continue
    return sorted(out, key=lambda x: -(x.get("created_at") or 0))[:limit]


def cancel(upload_id: str) -> None:
    d = _dir(upload_id)
    s = _load(upload_id)
    if s["status"] == "verifying":
        raise UploadError("upload_busy", 409)
    # a completed session's source is in the sources folder: only the session is removed
    shutil.rmtree(d, ignore_errors=True)


def cleanup(now: Optional[float] = None) -> dict[str, int]:
    """Expired sessions only: incomplete ones after SESSION_TTL idle, finished ones after DONE_TTL."""
    now = now or time.time()
    removed = 0
    freed = 0
    for d in list(root().iterdir()):
        if not d.is_dir() or not _ID.match(d.name):
            continue
        try:
            s = json.loads((d / "session.json").read_text("utf-8"))
            age = now - float(s.get("updated_at") or 0)
            ttl = DONE_TTL if s.get("status") == "complete" else SESSION_TTL
            if (s.get("status") == "verifying" and not _stale_verify(s)) or age < ttl:
                continue
        except (OSError, ValueError):
            if now - d.stat().st_mtime < SESSION_TTL:
                continue
        part = d / "data.part"
        freed += part.stat().st_blocks * 512 if part.exists() else 0
        shutil.rmtree(d, ignore_errors=True)
        removed += 1
    return {"removed": removed, "freed_bytes": freed}
