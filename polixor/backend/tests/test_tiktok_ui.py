"""
בדיקת דפדפן למסך הפרסום ל-TikTok (שלב 9), לפי הנחיות שיתוף התוכן של TikTok.

השרת רץ באותו תהליך (uvicorn בתהליכון) עם ספק TikTok מדומה – אין שום
פנייה ל-TikTok. המתזמן כבוי ושום דבר לא נשלח לפרסום.

  * הכינוי של היוצר מוצג ("פרסום ל-TikTok בתור …") מפרטים עדכניים.
  * פרטיות: אין ברירת מחדל; רק האפשרויות שהוחזרו (לא מבוקר → רק "רק אני");
    אי אפשר לשלוח לפני שבוחרים.
  * תגובות/דואט/סטיץ' לא מסומנים מראש; דואט מושבת כשהיוצר כיבה אותו.
  * גילוי תוכן מסחרי: חובה לבחור; "תוכן ממומן" → "רק אני" מושבת ותווית
    "Paid partnership"; שורת אישור השימוש במוזיקה (ומדיניות תוכן ממומן).
  * עברית + מובייל בלי גלילה אופקית; בלי שגיאות JavaScript.

הרצה:  python3 tests/test_tiktok_ui.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
DATA = tempfile.mkdtemp(prefix="pxttui_")
os.environ["POLIXOR_DATA_DIR"] = DATA
os.environ["POLIXOR_SCHEDULER"] = "0"
os.environ.pop("POLIXOR_ACCESS_PASSWORD", None)

import httpx                                                         # noqa: E402

from polixor.config import PATHS, SECRETS                            # noqa: E402
from polixor.db import init_db, session_scope                        # noqa: E402
from polixor.models import Clip, ClipKind, ClipStatus, Job, JobStatus, SocialAccount, new_id  # noqa: E402
from polixor.services.publishing import registry, vault             # noqa: E402
from polixor.services.publishing.tiktok import TikTokProvider       # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_tiktok import FakeTikTok                                   # noqa: E402

PORT = 8807
BASE = f"http://127.0.0.1:{PORT}"


def seed() -> str:
    PATHS.ensure()
    init_db()
    SECRETS.set("tiktok_client_key", "k")
    SECRETS.set("tiktok_client_secret", "s")
    media = Path(DATA) / "clip.mp4"
    media.write_bytes(b"\x00" * 4096)
    jid, cid = new_id(), new_id()
    with session_scope() as s:
        s.add(SocialAccount(id=new_id(), platform="tiktok", external_id="OPEN1", display_name="Lior",
                            access_token_enc=vault.encrypt("act.fake"), refresh_token_enc=vault.encrypt("rft.fake")))
        s.add(Job(id=jid, title="Stream", status=JobStatus.COMPLETED, artifacts={}, completed_stages=[],
                  phase="done", run_scope="all"))
        s.flush()
        s.add(Clip(id=cid, job_id=jid, kind=ClipKind.SHORT, status=ClipStatus.READY, title="TikTok moment",
                   file_path=str(media), file_size=4096, duration=30.0, width=1080, height=1920))
    return cid


def main() -> int:
    import uvicorn
    from playwright.sync_api import sync_playwright

    from polixor.main import app

    cid = seed()
    fake = FakeTikTok()
    registry.register(TikTokProvider(http=httpx.Client(transport=httpx.MockTransport(fake.handler))))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(120):
        try:
            urllib.request.urlopen(f"{BASE}/api/health", timeout=1)
            break
        except Exception:                               # noqa: BLE001
            time.sleep(0.25)

    failures: list[str] = []

    def expect(cond: bool, label: str) -> None:
        print(f"  {'✓' if cond else '✗'} {label}")
        if not cond:
            failures.append(label)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(locale="en-US", viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            outside: list[str] = []
            page.route("**/*tiktok*.com/**", lambda r: (outside.append(r.request.url), r.abort()))
            page.goto(BASE + f"/clips/{cid}/edit", wait_until="networkidle")
            page.locator("[data-testid=publish-open]").first.click()
            page.wait_for_selector("[data-testid=tiktok-panel]")
            panel = page.locator("[data-testid=tiktok-panel]")
            expect("Posting to TikTok as Lior" in panel.inner_text(), "creator nickname shown from live creator info")
            sel = page.locator("[data-testid=tiktok-privacy]")
            opts = sel.locator("option").evaluate_all("els => els.map(e => e.value)")
            expect(sel.input_value() == "" and opts == ["", "SELF_ONLY"],
                   f"privacy has no default; unaudited app offers only Only me ({opts})")
            expect(page.locator("[data-testid=tiktok-allow-comment]").is_checked() is False and
                   page.locator("[data-testid=tiktok-allow-stitch]").is_checked() is False,
                   "interactions start unticked")
            expect(page.locator("[data-testid=tiktok-allow-duet]").is_disabled(),
                   "duet disabled because the creator turned it off")
            page.wait_for_selector("text=Choose who can watch the video.", timeout=10000)
            expect(page.locator("[data-testid=publish-submit]").is_disabled(),
                   "cannot publish before choosing privacy")
            sel.select_option("SELF_ONLY")
            page.wait_for_selector("[data-testid=publish-ready]", timeout=10000)
            expect(True, "ready after choosing privacy")
            consent = page.inner_text("[data-testid=tiktok-consent]")
            expect("Music Usage Confirmation" in consent, "music usage confirmation shown before posting")
            page.click("[data-testid=tiktok-commercial]")
            page.wait_for_selector("text=choose “Your brand” and/or “Branded content”", timeout=10000)
            expect(page.locator("[data-testid=publish-submit]").is_disabled(),
                   "commercial disclosure requires a choice")
            page.locator("text=Branded content").first.click()
            page.wait_for_selector("[data-testid=tiktok-label-note]")
            expect("Paid partnership" in page.inner_text("[data-testid=tiktok-label-note]"),
                   "branded content shows the Paid partnership label")
            expect(page.locator("[data-testid=tiktok-privacy] option[value=SELF_ONLY]").is_disabled(),
                   "Only me is disabled for branded content")
            expect("Branded Content Policy" in page.inner_text("[data-testid=tiktok-consent]"),
                   "branded content adds the Branded Content Policy line")
            expect("Contains realistic AI-generated" in page.inner_text("[data-testid=publish-declarations]")
                   and "Made for kids" not in page.inner_text("[data-testid=publish-declarations]"),
                   "AI label offered, YouTube-only made-for-kids hidden")
            page.click("role=radio[name='Schedule']")
            page.wait_for_selector("text=Polixor publishes to TikTok at that time", timeout=10000)
            expect(True, "TikTok scheduling is done by Polixor")
            expect(not errors, f"no JavaScript errors {errors[:2]}")
            expect(not outside, f"no request from the browser to TikTok {outside[:2]}")
            expect(not fake.inits, "nothing was posted")
            ctx.close()

            ctx = browser.new_context(locale="he-IL", timezone_id="Asia/Jerusalem",
                                      viewport={"width": 390, "height": 844})
            page = ctx.new_page()
            page.goto(BASE + f"/clips/{cid}/edit", wait_until="networkidle")
            page.locator("[data-testid=publish-open]").first.click()
            page.wait_for_selector("[data-testid=tiktok-panel]")
            txt = page.inner_text("[data-testid=tiktok-panel]")
            wide = page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
            expect("פרסום ל-TikTok בתור Lior" in txt and "מי יכול לצפות" in txt and "אישור השימוש במוזיקה" in txt
                   and not wide, "Hebrew + mobile TikTok panel, no horizontal scroll")
            ctx.close()
            browser.close()
    finally:
        server.should_exit = True
        th.join(timeout=10)
    print(f"\n{'כל בדיקות ממשק TikTok עברו' if not failures else str(len(failures)) + ' בדיקות נכשלו'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        print("skipped: Playwright is not installed")
        sys.exit(0)
    sys.exit(main())
