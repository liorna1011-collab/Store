"""
בדיקת דפדפן לזרימת הפרסום (שלב 6), עם שרת אמיתי וספק ארגז החול בלבד –
שום דבר לא מתפרסם באמת.

  1. חיבור חשבון: פרסום → חשבונות → "חיבור" → דף האישור של ארגז החול →
     אישור → חזרה עם הודעת הצלחה והחשבון ברשימה.
  2. פרסום עכשיו מכרטיס קליפ: חלון הפרסום, בדיקה מוקדמת "מוכן", שליחה, והפוסט
     מופיע ב"פורסם" במרכז הפרסום (המתזמן בשרת מבצע).
  3. תזמון: אזהרה ש-Polixor חייב לפעול בזמן הזה; הפריט ב"מתוזמן" עם ביטול.
  4. עברית (RTL) ומובייל (390px) בלי גלילה אופקית; בלי שגיאות JavaScript.

הרצה:  python3 tests/test_publishing_ui.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
DATA = tempfile.mkdtemp(prefix="pxpubui_")
os.environ["POLIXOR_DATA_DIR"] = DATA

from polixor.config import PATHS, SETTINGS                        # noqa: E402
from polixor.db import init_db, session_scope                     # noqa: E402
from polixor.models import Clip, ClipKind, ClipStatus, Job, JobStatus, new_id  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PORT = 8803
BASE = f"http://127.0.0.1:{PORT}"
VIDEO = Path("/home/claude/testdata/polixor_test_stream.mp4")


def seed() -> str:
    PATHS.ensure()
    init_db()
    SETTINGS.update({"publish_sandbox": True})
    media = Path(DATA) / "clip.mp4"
    if VIDEO.exists():
        media.write_bytes(VIDEO.read_bytes())
    else:
        media.write_bytes(b"\x00" * 4096)
    jid, cid = new_id(), new_id()
    with session_scope() as s:
        s.add(Job(id=jid, title="Stream", status=JobStatus.COMPLETED, artifacts={},
                  completed_stages=[], phase="done", run_scope="all"))
        s.flush()
        s.add(Clip(id=cid, job_id=jid, kind=ClipKind.SHORT, status=ClipStatus.READY,
                   title="The best moment", description="Watch this", file_path=str(media),
                   file_size=media.stat().st_size, duration=20.0, width=1080, height=1920))
    return cid


def wait_up(timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{BASE}/api/health", timeout=2)
            return
        except Exception:                               # noqa: BLE001
            time.sleep(0.5)
    raise RuntimeError("server did not start")


def main() -> int:
    from playwright.sync_api import sync_playwright

    cid = seed()
    failures: list[str] = []

    def expect(cond: bool, label: str) -> None:
        print(f"  {'✓' if cond else '✗'} {label}")
        if not cond:
            failures.append(label)

    env = {**os.environ, "POLIXOR_DATA_DIR": DATA}
    env.pop("POLIXOR_ACCESS_PASSWORD", None)
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "polixor.main:app",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        wait_up()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(locale="en-US", viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))

            # ---- 1. חיבור חשבון ----
            page.goto(BASE + "/publishing?tab=accounts", wait_until="networkidle")
            expect(page.locator("text=never asks for your social media password").count() == 1,
                   "accounts tab states that no password is ever asked")
            tt = page.locator("[data-testid=platform-tiktok]")
            expect("Coming in stage 9" in tt.inner_text() and
                   page.locator("[data-testid=connect-tiktok]").is_disabled(),
                   "TikTok shown as coming in stage 9, connect disabled")
            yt = page.locator("[data-testid=platform-youtube]")
            expect("Needs developer app details" in yt.inner_text() and
                   page.locator("[data-testid=connect-youtube]").is_disabled(),
                   "YouTube available but asks for developer app details first")
            page.click("[data-testid=connect-sandbox]")
            page.wait_for_selector("#approve")
            expect("Nothing is really published" in page.inner_text("body"),
                   "sandbox consent page explains nothing is published")
            page.click("#approve")
            page.wait_for_url("**/publishing?tab=accounts**")
            page.wait_for_selector("[data-testid=account-row]")
            expect(page.locator("[data-testid=account-row]").count() == 1, "account connected and listed")
            expect("connected=" not in page.url, "return parameters cleaned from the address")

            # ---- 2. פרסום עכשיו מכרטיס קליפ ----
            page.goto(BASE + "/clips", wait_until="networkidle")
            page.locator("[data-testid=publish-open]").first.click()
            page.wait_for_selector("[data-testid=publish-dialog]")
            expect(page.input_value("[data-testid=publish-title]") == "The best moment",
                   "dialog pre-fills the clip title")
            page.wait_for_selector("[data-testid=publish-ready]", timeout=10000)
            expect(True, "preflight says ready")
            page.fill("[data-testid=publish-title]", "")
            page.wait_for_selector("text=A title is required.", timeout=10000)
            expect(page.locator("[data-testid=publish-submit]").is_disabled(),
                   "empty title blocks publishing with a clear message")
            page.fill("[data-testid=publish-title]", "My clip")
            page.wait_for_selector("[data-testid=publish-ready]", timeout=10000)
            page.click("[data-testid=publish-submit]")
            page.wait_for_selector("text=Sent to the publishing queue")
            page.goto(BASE + "/publishing", wait_until="networkidle")
            page.wait_for_selector("[data-group=published]", timeout=40000)
            item = page.locator("[data-group=published] [data-testid=publish-item]").first
            expect("My clip" in item.inner_text() and "View post" in item.inner_text(),
                   "post appears under Published with a link")

            # ---- 3. תזמון ----
            page.goto(BASE + f"/clips/{cid}/edit", wait_until="networkidle")
            page.locator("[data-testid=publish-open]").first.click()
            page.wait_for_selector("[data-testid=publish-dialog]")
            page.click("role=radio[name='Schedule']")
            page.wait_for_selector("text=the Polixor server must be running", timeout=10000)
            expect(True, "scheduling explains that the server must be running")
            page.wait_for_selector("[data-testid=publish-ready]", timeout=10000)
            page.click("[data-testid=publish-submit]")
            page.wait_for_selector("text=Sent to the publishing queue")
            page.goto(BASE + "/publishing", wait_until="networkidle")
            sched = page.locator("[data-group=scheduled] [data-testid=publish-item]").first
            sched.wait_for()
            t = sched.inner_text()
            expect("Scheduled for" in t and "must be running" in t and "Cancel" in t,
                   "scheduled item shows time, server warning and cancel")
            sched.locator("text=Cancel").click()
            page.wait_for_selector("[data-group=cancelled]", timeout=10000)
            expect(True, "cancelling moves it to Cancelled")
            expect(not errors, f"no JavaScript errors ({errors[:2]})")
            ctx.close()

            # ---- 4. עברית + מובייל ----
            ctx = browser.new_context(locale="he-IL", timezone_id="Asia/Jerusalem",
                                      viewport={"width": 390, "height": 844})
            page = ctx.new_page()
            page.goto(BASE + "/publishing", wait_until="networkidle")
            page.wait_for_selector("[data-testid=publish-queue]")
            st = page.evaluate("() => ({dir: document.documentElement.dir, wide: document.documentElement.scrollWidth > innerWidth + 1, text: document.body.innerText})")
            expect(st["dir"] == "rtl" and not st["wide"], "Hebrew RTL publishing page, no horizontal scroll at 390px")
            expect("פורסם" in st["text"] and "תור והיסטוריה" in st["text"], "publishing page texts in Hebrew")
            page.goto(BASE + "/clips", wait_until="networkidle")
            page.locator("[data-testid=publish-open]").first.click()
            page.wait_for_selector("[data-testid=publish-dialog]")
            wide = page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
            expect(not wide and "חשבונות" in page.inner_text("[data-testid=publish-dialog]"),
                   "publish dialog in Hebrew on mobile, no horizontal scroll")
            page.goto(BASE + "/publishing?tab=accounts", wait_until="networkidle")
            wide = page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
            expect(not wide, "accounts tab on mobile, no horizontal scroll")
            ctx.close()
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
    print(f"\n{'כל' if not failures else len(failures)} {'בדיקות ממשק הפרסום עברו' if not failures else 'בדיקות נכשלו'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        print("skipped: Playwright is not installed")
        sys.exit(0)
    sys.exit(main())
