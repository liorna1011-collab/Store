"""
Polixor – נקודת הכניסה של השרת.

הרצה:
    python -m polixor.main
    uvicorn polixor.main:app --host 127.0.0.1 --port 8756
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import i18n
from .config import APP_NAME, APP_VERSION, PATHS, find_ffmpeg
from .db import init_db
from .errors import PolixorError
from .events import BUS

logging.basicConfig(
    level=os.environ.get("POLIXOR_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("polixor")

def _frontend_dist() -> Path:
    """
    מיקום הממשק הבנוי. ניתן לעקוף עם POLIXOR_FRONTEND_DIST
    (שימושי ב-Docker ובפריסות לא סטנדרטיות).
    """
    override = os.environ.get("POLIXOR_FRONTEND_DIST")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


FRONTEND_DIST = _frontend_dist()


@asynccontextmanager
async def lifespan(app: FastAPI):
    PATHS.ensure()
    init_db()
    BUS.bind_loop(asyncio.get_running_loop())

    # ייבוא הפייפליין רושם את ה-runner אצל מנהל המשימות
    from .pipeline import resume_interrupted_jobs

    interrupted = resume_interrupted_jobs()
    if interrupted:
        log.warning("%d jobs were interrupted by a previous shutdown", interrupted)

    if not find_ffmpeg():
        log.error("FFmpeg not found in PATH – video processing will fail.")

    log.info("%s %s ready · data: %s", APP_NAME, APP_VERSION, PATHS.data)
    try:
        yield
    finally:
        from .worker import MANAGER

        MANAGER.shutdown(wait=False)
        log.info("shutdown complete")


app = FastAPI(
    title=f"{APP_NAME} API",
    version=APP_VERSION,
    description="ניתוח שידורים ויצירת קליפים אוטומטית",
    lifespan=lifespan,
)

# הממשק רץ ב-dev על פורט 5173; בפרודקשן הוא מוגש מאותו שרת.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173",
                   "http://localhost:8756", "http://127.0.0.1:8756"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def request_language(scope: dict) -> str:
    """
    שפת הבקשה: `?lang=` (נסתר, לבדיקות ולתמיכה), הכותרת `X-Polixor-Lang`
    (הממשק שולח את השפה שזוהתה אוטומטית), כותרת מדינה מהימנה (ישראל →
    עברית), `Accept-Language`, ובסוף עברית. אין שימוש בכתובת IP.
    """
    from urllib.parse import parse_qs

    query = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
    for value in query.get("lang", []):
        code = i18n.normalize_lang(value)
        if code:
            return code
    headers = {k.decode("latin-1").lower(): v.decode("latin-1")
               for k, v in (scope.get("headers") or [])}
    code = i18n.normalize_lang(headers.get("x-polixor-lang"))
    if code:
        return code
    code = i18n.language_for_country(i18n.country_from_headers(headers))
    if code:
        return code
    code = i18n.parse_accept_language(headers.get("accept-language"))
    return code or i18n.DEFAULT_LANG


class LanguageMiddleware:
    """
    קובע את שפת הבקשה ב-contextvar לכל אורך הטיפול בה.

    ASGI טהור (לא BaseHTTPMiddleware), כדי שהערך יעבור גם לנקודות
    קצה סינכרוניות שרצות בתהליכון נפרד.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        lang = request_language(scope)
        token = i18n.set_lang(lang)

        async def send_with_language(message) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((b"content-language", lang.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_language)
        finally:
            i18n.reset_lang(token)


# שער הסיסמה רץ בתוך הקשר השפה (כדי שדף הכניסה והשגיאות יהיו בשפת
# הבקשה) ולפני כל נתיב. כבוי כשלא הוגדרה סיסמה.
from .access import AccessGate  # noqa: E402

app.add_middleware(AccessGate)
app.add_middleware(LanguageMiddleware)


@app.exception_handler(PolixorError)
async def polixor_error_handler(_request: Request, exc: PolixorError) -> JSONResponse:
    from .api.http import ERROR_STATUS

    status = ERROR_STATUS.get(exc.code, 400)
    return JSONResponse(status_code=status, content=exc.to_dict())


# --------------------------------------------------------------------------
# נתיבים
# --------------------------------------------------------------------------
from .api import (  # noqa: E402
    routes_clips, routes_images, routes_jobs, routes_live, routes_projects,
    routes_notifications, routes_subtitles, routes_system, ws,
)

app.include_router(routes_projects.router)
app.include_router(routes_jobs.router)
app.include_router(routes_clips.router)
app.include_router(routes_images.router)
app.include_router(routes_live.router)
app.include_router(routes_system.router)
app.include_router(routes_subtitles.router)
app.include_router(routes_notifications.router)
app.include_router(ws.router)


def _access_password() -> str:
    from .access import configured_password

    return configured_password()


@app.get("/api/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "app": APP_NAME,
        "version": APP_VERSION,
        "ffmpeg": bool(find_ffmpeg()),
        "ws_subscribers": BUS.subscriber_count,
        "lang": i18n.get_lang(),
        "access_protected": bool(_access_password()),
    }


@app.get("/api/locale")
def locale_signal(request: Request) -> dict[str, object]:
    """
    האות מהשרת לבחירת שפת הממשק: המדינה מכותרת מהימנה של CDN/פרוקסי
    (רק קוד מדינה; לא IP, לא עיר, ושום דבר לא נשמר). הממשק משלב אותו עם
    אזור הזמן והשפה של הדפדפן – ראו frontend/src/i18n/index.ts.
    """
    headers = {k.lower(): v for k, v in request.headers.items()}
    lang = i18n.language_for_country(i18n.country_from_headers(headers))
    return {"lang": lang, "dir": i18n.direction(lang) if lang else None,
            "source": "country" if lang else None,
            "country_known": i18n.country_from_headers(headers) is not None}


@app.get("/api/i18n/languages")
def i18n_languages() -> dict[str, object]:
    """השפות שהשרת מכיר, לצורך בורר השפה."""
    return {"languages": i18n.available_languages(),
            "default": i18n.DEFAULT_LANG, "current": i18n.get_lang()}


# --------------------------------------------------------------------------
# הגשת הממשק הבנוי
# --------------------------------------------------------------------------
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"),
              name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        """כל נתיב שאינו API מוחזר ל-index.html (ניתוב בצד הלקוח)."""
        if full_path.startswith("api/") or full_path == "ws":
            return JSONResponse(status_code=404,
                                content={"code": "not_found",
                                         "message": i18n.tr("errors.not_found.message"),
                                         "hint": ""})
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
else:
    @app.get("/")
    def no_frontend() -> dict[str, str]:
        return {
            "message": i18n.tr("api.no_frontend"),
            "hint": i18n.tr("api.no_frontend_hint"),
            "api_docs": "/docs",
        }


def _lan_addresses() -> list[str]:
    """כתובות ה-IP של המחשב ברשת המקומית, להצגה בלבד."""
    import socket

    found: list[str] = []
    try:
        # UDP „מחובר" לא שולח דבר; הוא רק בוחר את כרטיס הרשת של נתיב ברירת המחדל
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.168.255.255", 1))
            found.append(sock.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    return found or ["<this computer's IP address>"]


def main() -> None:
    import uvicorn

    host = os.environ.get("POLIXOR_HOST", "127.0.0.1")
    port = int(os.environ.get("POLIXOR_PORT", "8756"))
    reload = os.environ.get("POLIXOR_RELOAD", "").lower() in ("1", "true", "yes")

    if not find_ffmpeg():
        print("⚠  FFmpeg לא נמצא ב-PATH. עיבוד וידאו לא יעבוד.", file=sys.stderr)
        print("   התקנה ב-Windows: winget install Gyan.FFmpeg", file=sys.stderr)

    print(f"\n  {APP_NAME} {APP_VERSION}")
    print(f"  Open:  http://127.0.0.1:{port}")
    if host in ("0.0.0.0", "::"):
        # מצב טלפון: נגיש מכל מכשיר ברשת הביתית. אין כניסה עם סיסמה,
        # ולכן זה מיועד לרשת פרטית בלבד.
        for ip in _lan_addresses():
            print(f"  Phone / other devices on this Wi-Fi:  http://{ip}:{port}")
        print("  Anyone on this network can open Polixor. Use it only on a private network.")
    print(f"  Data:  {PATHS.data}\n")

    uvicorn.run("polixor.main:app" if reload else app,
                host=host, port=port, reload=reload, log_level="info")


if __name__ == "__main__":
    main()
