"""
Internal observability for failed user actions – no secrets, admin view only.

  build_id()          which interface build this server serves (dist/version.json, written by
                      the frontend build); sent on every response as X-Polixor-Build so a page
                      running older JS can tell it is stale ("A new version of Polixor is
                      available. Reload.")
  RequestMeta         ASGI middleware: a request id on every response (X-Request-Id, taken from the
                      browser's header when it sent one), the build id, and every failed API request
                      (status ≥ 400 or an exception) recorded with method, path, status, elapsed time
  client_event()      what the browser reports about a failed action: action/route, status, elapsed,
                      category (auth / timeout / payload_too_large / rate_limited / proxy / network /
                      validation / server / stale_build), request id, project id, a short message

Both go to in-memory ring buffers and to <data>/logs/events.jsonl (rotated at 5 MB). Nothing that
could hold a secret is stored: no headers, no bodies, no query strings, no cookies.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("polixor.observe")

_server_failures: deque = deque(maxlen=300)
_client_events: deque = deque(maxlen=300)
_lock = threading.Lock()
_build: Optional[str] = None
_SAFE = re.compile(r"[^A-Za-z0-9 ._:/@#%()\-–—,'\"!?֐-׿]")
MAX_LOG = 5 * 1024 * 1024


def build_id() -> str:
    global _build
    if _build is None:
        from ..config import APP_VERSION
        from ..main import FRONTEND_DIST

        bid = ""
        try:
            bid = str(json.loads((FRONTEND_DIST / "version.json").read_text("utf-8")).get("build") or "")
        except (OSError, ValueError, AttributeError):
            pass
        if not bid:
            try:
                bid = f"{APP_VERSION}-{int((FRONTEND_DIST / 'index.html').stat().st_mtime)}"
            except OSError:
                bid = APP_VERSION
        _build = bid[:64]
    return _build


def _clean(v: Any, n: int = 200) -> str:
    return _SAFE.sub("", str(v or ""))[:n]


def _write(kind: str, row: dict[str, Any]) -> None:
    try:
        from ..config import PATHS

        d = PATHS.data / "logs"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "events.jsonl"
        if p.exists() and p.stat().st_size > MAX_LOG:
            p.replace(d / "events.1.jsonl")
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": kind, **row}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def record_server_failure(row: dict[str, Any]) -> None:
    with _lock:
        _server_failures.append(row)
    _write("server", row)


CATEGORIES = {"auth", "timeout", "payload_too_large", "rate_limited", "proxy", "network", "validation",
              "server", "stale_build", "not_found", "conflict", "unknown"}


def client_event(data: dict[str, Any]) -> dict[str, Any]:
    cat = str(data.get("category") or "unknown")
    row = {"at": time.time(), "action": _clean(data.get("action"), 80), "route": _clean(data.get("route"), 160),
           "method": _clean(data.get("method"), 8), "status": int(data.get("status") or 0),
           "elapsed_ms": int(data.get("elapsed_ms") or 0), "category": cat if cat in CATEGORIES else "unknown",
           "code": _clean(data.get("code"), 60), "request_id": _clean(data.get("request_id"), 40),
           "project_id": _clean(data.get("project_id"), 40), "page": _clean(data.get("page"), 160),
           "message": _clean(data.get("message"), 240), "build": _clean(data.get("build"), 64),
           "server_build": build_id(), "detail": {k: v for k, v in (data.get("detail") or {}).items()
                                                  if k in ("request_bytes", "next_bytes", "attempt", "profile",
                                                           "concurrency", "upload_id", "offset")}}
    with _lock:
        _client_events.append(row)
    _write("client", row)
    return row


_loaded = False


def _load_tail() -> None:
    """After a restart the admin view still shows what happened before it (the log's tail)."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        from ..config import PATHS

        lines = (PATHS.data / "logs" / "events.jsonl").read_text("utf-8").splitlines()[-600:]
    except OSError:
        return
    with _lock:
        known_c, known_s = list(_client_events), list(_server_failures)
        _client_events.clear()
        _server_failures.clear()
        for ln in lines:
            try:
                row = json.loads(ln)
            except ValueError:
                continue
            kind = row.pop("kind", "")
            (_client_events if kind == "client" else _server_failures).append(row)
        _client_events.extend(r for r in known_c if r not in _client_events)
        _server_failures.extend(r for r in known_s if r not in _server_failures)


def recent() -> dict[str, Any]:
    _load_tail()
    with _lock:
        return {"build": build_id(), "client": list(_client_events)[::-1][:200],
                "server": list(_server_failures)[::-1][:200]}


_RID = re.compile(r"^[A-Za-z0-9-]{6,40}$")


class RequestMeta:
    """Outermost ASGI middleware: request id + build id on every response, failed API requests recorded."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        rid = headers.get("x-request-id", "")
        if not _RID.match(rid):
            rid = uuid.uuid4().hex[:12]
        path = scope.get("path") or ""
        t0 = time.time()
        status = [0]

        async def send_meta(message) -> None:
            if message.get("type") == "http.response.start":
                status[0] = int(message.get("status") or 0)
                h = list(message.get("headers") or [])
                h.append((b"x-request-id", rid.encode("latin-1")))
                h.append((b"x-polixor-build", build_id().encode("latin-1")))
                if path == "/" or path.endswith(".html") or not path.startswith(("/api", "/assets", "/ws")):
                    # the page itself is never cached: a reload always gets the current build's JS
                    h = [x for x in h if x[0].lower() != b"cache-control"]
                    h.append((b"cache-control", b"no-cache, must-revalidate"))
                elif path.startswith("/assets/"):
                    h.append((b"cache-control", b"public, max-age=31536000, immutable"))
                message = {**message, "headers": h}
            await send(message)

        try:
            await self.app(scope, receive, send_meta)
        except Exception:
            record_server_failure({"at": t0, "request_id": rid, "method": scope.get("method"), "path": path[:160],
                                   "status": 500, "elapsed_ms": int((time.time() - t0) * 1000),
                                   "error": "unhandled exception"})
            raise
        if status[0] >= 400 and path.startswith("/api") and path not in ("/api/client-events",):
            record_server_failure({"at": t0, "request_id": rid, "method": scope.get("method"), "path": path[:160],
                                   "status": status[0], "elapsed_ms": int((time.time() - t0) * 1000)})


__all__ = ["build_id", "RequestMeta", "client_event", "recent", "record_server_failure", "Path"]
