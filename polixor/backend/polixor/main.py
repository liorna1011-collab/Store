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


@app.exception_handler(PolixorError)
async def polixor_error_handler(_request: Request, exc: PolixorError) -> JSONResponse:
    status = {
        "job_not_found": 404, "clip_not_found": 404, "invalid_url": 400,
        "private_or_unavailable": 403, "drm_protected": 403, "restricted": 403,
        "disk_space": 507, "ffmpeg_missing": 503, "model_unavailable": 503,
    }.get(exc.code, 400)
    return JSONResponse(status_code=status, content=exc.to_dict())


# --------------------------------------------------------------------------
# נתיבים
# --------------------------------------------------------------------------
from .api import (  # noqa: E402
    routes_clips, routes_images, routes_jobs, routes_live, routes_system, ws,
)

app.include_router(routes_jobs.router)
app.include_router(routes_clips.router)
app.include_router(routes_images.router)
app.include_router(routes_live.router)
app.include_router(routes_system.router)
app.include_router(ws.router)


@app.get("/api/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "app": APP_NAME,
        "version": APP_VERSION,
        "ffmpeg": bool(find_ffmpeg()),
        "ws_subscribers": BUS.subscriber_count,
    }


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
                                         "message": "נתיב לא קיים."})
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
else:
    @app.get("/")
    def no_frontend() -> dict[str, str]:
        return {
            "message": "השרת פועל, אך הממשק טרם נבנה.",
            "hint": "הרץ בתיקיית frontend: npm install && npm run build, "
                    "או הפעל שרת פיתוח: npm run dev",
            "api_docs": "/docs",
        }


def main() -> None:
    import uvicorn

    host = os.environ.get("POLIXOR_HOST", "127.0.0.1")
    port = int(os.environ.get("POLIXOR_PORT", "8756"))
    reload = os.environ.get("POLIXOR_RELOAD", "").lower() in ("1", "true", "yes")

    if not find_ffmpeg():
        print("⚠  FFmpeg לא נמצא ב-PATH. עיבוד וידאו לא יעבוד.", file=sys.stderr)
        print("   התקנה ב-Windows: winget install Gyan.FFmpeg", file=sys.stderr)

    print(f"\n  {APP_NAME} {APP_VERSION}")
    print(f"  ממשק:  http://{host}:{port}")
    print(f"  נתונים: {PATHS.data}\n")

    uvicorn.run("polixor.main:app" if reload else app,
                host=host, port=port, reload=reload, log_level="info")


if __name__ == "__main__":
    main()
