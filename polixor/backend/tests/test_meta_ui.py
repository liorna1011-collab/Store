"""
בדיקת דפדפן לממשק Meta (שלב 8): ממשק לפי יכולות הפלטפורמה.

השרת רץ עם POLIXOR_SCHEDULER=0 ושום בקשה לא נשלחת לפרסום – אין שום פנייה
ל-Meta. החשבונות נזרעים ישירות ב-DB (טוקנים מדומים, מוצפנים).

  * Instagram: שדה "פריים לכריכה" מוצג; אין בורר פרטיות (רק ציבורי);
    תזמון → "Polixor יפרסם – השרת חייב לפעול".
  * Facebook: אין שדה כריכה; תזמון → "Facebook מפרסם בעצמו"; מועד קרוב מדי
    (פחות מ-10 דקות) → הודעה ברורה.
  * YouTube-only הצהרות לא מוצגות ל-Meta.
  * חשבון Instagram מציג לאיזה עמוד הוא מקושר; עברית + מובייל בלי גלילה אופקית.

הרצה:  python3 tests/test_meta_ui.py
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
DATA = tempfile.mkdtemp(prefix="pxmetaui_")
os.environ["POLIXOR_DATA_DIR"] = DATA

from polixor.config import PATHS, SECRETS                         # noqa: E402
from polixor.db import init_db, session_scope                     # noqa: E402
from polixor.models import Clip, ClipKind, ClipStatus, Job, JobStatus, SocialAccount, new_id  # noqa: E402
from polixor.services.publishing import vault                    # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PORT = 8805
BASE = f"http://127.0.0.1:{PORT}"


def seed() -> str:
    PATHS.ensure()
    init_db()
    SECRETS.set("meta_app_id", "1234567890")
    SECRETS.set("meta_app_secret", "not-real")
    media = Path(DATA) / "clip.mp4"
    media.write_bytes(b"\x00" * 4096)
    jid, cid = new_id(), new_id()
    with session_scope() as s:
        s.add(SocialAccount(id=new_id(), platform="facebook", external_id="P1", display_name="My Page",
                            access_token_enc=vault.encrypt("fake-page"), refresh_token_enc=vault.encrypt("fake-user"),
                            meta={"kind": "page"}))
        s.add(SocialAccount(id=new_id(), platform="instagram", external_id="IG1", display_name="My Brand",
                            handle="@mybrand", access_token_enc=vault.encrypt("fake-page"),
                            refresh_token_enc=vault.encrypt("fake-user"),
                            meta={"kind": "instagram", "page_id": "P1", "page_name": "My Page"}))
        s.add(Job(id=jid, title="Stream", status=JobStatus.COMPLETED, artifacts={}, completed_stages=[],
                  phase="done", run_scope="all"))
        s.flush()
        s.add(Clip(id=cid, job_id=jid, kind=ClipKind.SHORT, status=ClipStatus.READY, title="Reel moment",
                   file_path=str(media), file_size=4096, duration=30.0, width=1080, height=1920))
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

    env = {**os.environ, "POLIXOR_DATA_DIR": DATA, "POLIXOR_SCHEDULER": "0"}
    env.pop("POLIXOR_ACCESS_PASSWORD", None)
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "polixor.main:app", "--host", "127.0.0.1",
         "--port", str(PORT), "--log-level", "warning"],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        wait_up()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(locale="en-US", viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            # כל בקשה החוצה ל-Meta נחסמת ונרשמת (לא אמורה להיות אף אחת)
            outside: list[str] = []
            page.route("**/*facebook.com/**", lambda r: (outside.append(r.request.url), r.abort()))

            page.goto(BASE + "/publishing?tab=accounts", wait_until="networkidle")
            rows = page.locator("[data-testid=account-row]").all_inner_texts()
            expect(any("linked to My Page" in r for r in rows), "Instagram account shows its linked Page")
            fb = page.locator("[data-testid=platform-facebook]").inner_text()
            expect("One Facebook sign-in connects" in fb, "Meta platforms explain the shared sign-in")

            page.goto(BASE + f"/clips/{cid}/edit", wait_until="networkidle")
            page.locator("[data-testid=publish-open]").first.click()
            page.wait_for_selector("[data-testid=publish-dialog]")
            accs = page.locator("[data-testid^=publish-account-]")
            # בוחרים רק את Instagram
            for i in range(accs.count()):
                if accs.nth(i).is_checked():
                    accs.nth(i).uncheck()
            ig = page.locator("li", has_text="Instagram").locator("input[type=checkbox]").first
            ig.check()
            page.wait_for_selector("[data-testid=publish-cover]")
            dialog = page.locator("[data-testid=publish-dialog]")
            expect(dialog.locator("select").count() == 0, "Instagram: no privacy selector (public only)")
            expect(page.locator("[data-testid=publish-declarations]").count() == 0,
                   "YouTube-only declarations are hidden for Meta")
            page.wait_for_selector("[data-testid=publish-ready]", timeout=10000)
            page.click("role=radio[name='Schedule']")
            page.wait_for_selector("text=Polixor publishes to Instagram at that time", timeout=10000)
            expect(True, "Instagram scheduling is done by Polixor (server must be running)")

            # Facebook במקום
            ig.uncheck()
            page.locator("li", has_text="Facebook").locator("input[type=checkbox]").first.check()
            page.wait_for_selector("text=Facebook publishes at that time itself", timeout=10000)
            expect(page.locator("[data-testid=publish-cover]").count() == 0, "Facebook: no cover-frame field")
            from datetime import datetime, timedelta
            soon = (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")
            page.fill("[data-testid=publish-when]", soon)
            page.wait_for_selector("text=at least 10 minutes ahead", timeout=10000)
            expect(page.locator("[data-testid=publish-submit]").is_disabled(),
                   "Facebook: a time under 10 minutes ahead is blocked with a clear reason")
            expect(not errors, f"no JavaScript errors {errors[:2]}")
            expect(not outside, f"no request left for Meta {outside[:2]}")
            ctx.close()

            ctx = browser.new_context(locale="he-IL", timezone_id="Asia/Jerusalem",
                                      viewport={"width": 390, "height": 844})
            page = ctx.new_page()
            page.goto(BASE + f"/clips/{cid}/edit", wait_until="networkidle")
            page.locator("[data-testid=publish-open]").first.click()
            page.wait_for_selector("[data-testid=publish-dialog]")
            ig = page.locator("li", has_text="Instagram").locator("input[type=checkbox]").first
            ig.check()
            page.wait_for_selector("[data-testid=publish-cover]")
            txt = page.inner_text("[data-testid=publish-dialog]")
            wide = page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
            expect("פריים לכריכה" in txt and "מקושר ל-My Page" in txt and not wide,
                   "Hebrew + mobile: Instagram options in Hebrew, no horizontal scroll")
            page.goto(BASE + "/publishing?tab=accounts", wait_until="networkidle")
            wide = page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
            expect(not wide and "כניסה אחת ל-Facebook" in page.inner_text("body"),
                   "Hebrew + mobile accounts tab")
            ctx.close()
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
    print(f"\n{'כל בדיקות ממשק Meta עברו' if not failures else str(len(failures)) + ' בדיקות נכשלו'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        print("skipped: Playwright is not installed")
        sys.exit(0)
    sys.exit(main())
