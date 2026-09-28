"""
בדיקה ויזואלית של הפאנלים החדשים במסך עריכת הקליפ:
תכנית העריכה של ה-AI, בדיקת האיכות, וסיכום האודיו.

הבדיקה מריצה פייפליין אמיתי, מפעילה שרת, ובודקת שהפאנלים באמת
מוצגים עם תוכן — לא רק שהקוד עולה.

הרצה:  python3 tests/screenshot_director.py [out_dir]
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

DATA = tempfile.mkdtemp(prefix="pxshot_")
os.environ.setdefault("POLIXOR_DATA_DIR", DATA)
os.environ["POLIXOR_FIXTURE_TRANSCRIPT"] = \
    "/home/claude/testdata/polixor_test_stream.transcript.json"

from polixor.config import PATHS, AppSettings                    # noqa: E402

PATHS.ensure()

from polixor.db import init_db, session_scope                    # noqa: E402
from polixor.models import Clip, Job, JobStatus, new_id          # noqa: E402
from polixor.pipeline import run_job                             # noqa: E402

from playwright.sync_api import sync_playwright                  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1
           else "/tmp/claude-0/-home-claude/dd040985-06b9-5d82-b9ac-3a4e3dfe5e3b/"
                "scratchpad/shots")
OUT.mkdir(parents=True, exist_ok=True)
PORT = 8791
BASE = f"http://127.0.0.1:{PORT}"
VIDEO = Path("/home/claude/testdata/polixor_test_stream.mp4")

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
results: list[tuple[bool, str]] = []


def check(cond: bool, label: str, detail: str = "") -> bool:
    results.append((bool(cond), label))
    print(f"  {PASS if cond else FAIL} {label}" + (f"  — {detail}" if detail else ""))
    return bool(cond)


def build_clip() -> str:
    init_db()
    base = AppSettings().to_dict()
    base.update({
        "transcript_provider": "fixture", "ai_mode": "heuristic",
        "visual_sample_fps": 2.0, "video_quality": "low",
        "subtitles_enabled": True, "long_enabled": False,
        "short_enabled": True, "short_count": 1,
        "short_min_seconds": 20, "short_max_seconds": 45,
    })
    settings = AppSettings.from_dict(base)
    job_id = new_id()
    with session_scope() as s:
        s.add(Job(id=job_id, title="בדיקת פאנלים", input_url="",
                  status=JobStatus.QUEUED,
                  settings_snapshot=settings.to_dict(),
                  artifacts={"source_path": str(VIDEO)},
                  completed_stages=[]))
    run_job(job_id, threading.Event())
    with session_scope() as s:
        clip = s.query(Clip).filter(Clip.job_id == job_id).first()
        return clip.id if clip else ""


def main() -> int:
    print(f"\n{'=' * 72}\n▶ פאנלים חדשים במסך עריכת הקליפ\n{'=' * 72}")
    print("\n— מריץ פייפליין —")
    clip_id = build_clip()
    if not check(bool(clip_id), "נוצר קליפ עם תכנית במאי"):
        return _summary()

    with session_scope() as s:
        params = dict((s.get(Clip, clip_id).render_params or {}))
    check(bool(params.get("director")), "התכנית נשמרה ב-render_params",
          f"{len(params.get('director') or [])} מקטעים")
    check("qa" in params, "דוח בדיקת האיכות נשמר")
    check("audio" in params, "סיכום האודיו נשמר")

    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "polixor.main:app",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env={**os.environ, "POLIXOR_DATA_DIR": DATA},
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        _wait_for_server()
        return _shoot(clip_id)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


def _wait_for_server(timeout: float = 45.0) -> None:
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{BASE}/api/health", timeout=2)
            return
        except Exception:                               # noqa: BLE001
            time.sleep(0.7)
    raise RuntimeError("השרת לא עלה")


def _shoot(clip_id: str) -> int:
    errors: list[str] = []
    print("\n— הממשק —")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 1100},
                                  locale="he-IL")
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console: {m.text}")
                if m.type == "error" else None)

        page.goto(f"{BASE}/clips/{clip_id}/edit", wait_until="networkidle")
        page.wait_for_timeout(1500)

        body = page.inner_text("body")
        check("תכנית העריכה של ה-AI" in body, "פאנל תכנית העריכה מוצג")
        check("בדיקת איכות אחרי הרינדור" in body, "פאנל בדיקת האיכות מוצג")
        check("מה נעשה לאודיו" in body, "פאנל האודיו מוצג")

        # פותחים את קבוצת החיתוכים ומוודאים שיש הסברים
        btn = page.locator("button").filter(has_text="חיתוכים").first
        if btn.count():
            btn.click()
            page.wait_for_timeout(700)
            opened = page.inner_text("body")
            check(len(opened) > len(body),
                  "לחיצה על קבוצה חושפת את ההחלטות")
            check("%" in opened, "מוצג ציון ביטחון לכל החלטה")
        else:
            check(False, "לא נמצאה קבוצת החלטות ללחיצה")

        page.screenshot(path=str(OUT / "director_plan.png"), full_page=True)
        check((OUT / "director_plan.png").exists(), "צילום מסך נשמר",
              str(OUT / "director_plan.png"))

        # גלישה אופקית ברוחב טלפון
        page.set_viewport_size({"width": 400, "height": 900})
        page.wait_for_timeout(800)
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - "
            "document.documentElement.clientWidth")
        check(overflow <= 1, "אין גלילה אופקית ברוחב 400px",
              f"{overflow}px")
        page.screenshot(path=str(OUT / "director_plan_mobile.png"),
                        full_page=True)

        browser.close()

    real = [e for e in errors if "favicon" not in e.lower()]
    check(not real, "אין שגיאות JavaScript", " | ".join(real[:2]))
    return _summary()


def _summary() -> int:
    passed = sum(1 for ok, _ in results if ok)
    print(f"\n{'=' * 72}\nסיכום: {passed}/{len(results)} בדיקות עברו")
    bad = [l for ok, l in results if not ok]
    if bad:
        print("נכשלו: " + ", ".join(bad))
    print("=" * 72)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
