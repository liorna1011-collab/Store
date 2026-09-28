"""
מקים שרת עם משימה אמיתית ומריץ עליו את הבדיקות שדורשות שרת.

קיים כדי שאפשר יהיה להריץ את הסוויטות האלה בפקודה אחת בלי להקים
שרת ידנית, ולוודא שהן עדיין עוברות אחרי שינויים.

הרצה:  python3 tests/run_server_suites.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# תיקייה אחת לנתונים המוכנים ולשרת. אם הוגדרה מבחוץ – משתמשים בה,
# אחרת השרת היה עולה על תיקייה אחרת מזו שהזריעה כתבה אליה.
DATA = os.environ.get("POLIXOR_DATA_DIR") or tempfile.mkdtemp(prefix="pxsrv_")
os.environ["POLIXOR_DATA_DIR"] = DATA
os.environ["POLIXOR_FIXTURE_TRANSCRIPT"] = \
    "/home/claude/testdata/polixor_test_stream.transcript.json"

from polixor.config import PATHS, SETTINGS, AppSettings          # noqa: E402

PATHS.ensure()

# בדיקות התמונות מייצרות תמונה דרך ה-API. בסביבה הזו אין מפתח
# OpenAI, ולכן משתמשים בספק המקומי — שמסומן במפורש כ„לא AI"
# בדיוק כפי שהבדיקות מצפות לראות.
SETTINGS.update({"image_provider": "placeholder"})

from polixor.db import init_db, session_scope                    # noqa: E402
from polixor.models import Clip, Job, JobStatus, new_id          # noqa: E402
from polixor.pipeline import run_job                             # noqa: E402

PORT = 8793
BASE = f"http://127.0.0.1:{PORT}"
VIDEO = Path("/home/claude/testdata/polixor_test_stream.mp4")
ROOT = Path(__file__).resolve().parents[1]

SUITES = ["e2e_images_in_render.py", "screenshot_new_features.py"]


def seed() -> None:
    init_db()
    base = AppSettings().to_dict()
    base.update({
        "transcript_provider": "fixture", "ai_mode": "heuristic",
        "visual_sample_fps": 2.0, "video_quality": "low",
        "subtitles_enabled": True, "long_enabled": True, "long_count": 1,
        "long_min_seconds": 20, "long_max_seconds": 60,
        "short_enabled": True, "short_count": 1,
        "short_min_seconds": 15, "short_max_seconds": 30,
    })
    settings = AppSettings.from_dict(base)
    job_id = new_id()
    with session_scope() as s:
        s.add(Job(id=job_id, title="בדיקות שרת", input_url="",
                  status=JobStatus.QUEUED,
                  settings_snapshot=settings.to_dict(),
                  artifacts={"source_path": str(VIDEO)},
                  completed_stages=[]))
    run_job(job_id, threading.Event())
    with session_scope() as s:
        n = s.query(Clip).filter(Clip.job_id == job_id).count()
    print(f"  נוצרו {n} קליפים")


def wait_up(timeout: float = 45.0) -> None:
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{BASE}/api/health", timeout=2)
            return
        except Exception:                               # noqa: BLE001
            time.sleep(0.7)
    raise RuntimeError("השרת לא עלה")


def main() -> int:
    print(f"\n{'=' * 72}\n▶ בדיקות שדורשות שרת\n{'=' * 72}\n— מכין נתונים —")
    seed()

    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "polixor.main:app",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=str(ROOT), env={**os.environ, "POLIXOR_DATA_DIR": DATA},
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    failures: list[str] = []
    try:
        wait_up()
        for suite in SUITES:
            print(f"\n{'─' * 72}\n▶ {suite}\n{'─' * 72}")
            res = subprocess.run(
                [sys.executable, str(ROOT / "tests" / suite), BASE],
                cwd=str(ROOT), env={**os.environ, "POLIXOR_DATA_DIR": DATA},
                capture_output=True, text=True, timeout=900)
            tail = [l for l in res.stdout.splitlines()
                    if "עברו" in l or "נכשל" in l or "✗" in l]
            print("\n".join(tail[-8:]) or res.stdout[-600:])
            if res.returncode != 0:
                failures.append(suite)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    print(f"\n{'=' * 72}")
    if failures:
        print("סוויטות שנכשלו: " + ", ".join(failures))
    else:
        print("כל הסוויטות שדורשות שרת עברו.")
    print("=" * 72)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
