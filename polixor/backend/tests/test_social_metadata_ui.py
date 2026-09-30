"""
בדיקת דפדפן להצעת מטא-דאטה לרשתות בחלון הפרסום (שלב 10).

השרת רץ באותו תהליך עם ספקי Meta מדומים (Facebook + Instagram) ומודל שפה
מדומה – אין פנייה לרשת. המתזמן כבוי: העבודות נוצרות בתור ולא מתפרסמות.

  * "הצעה" ממלאת עורך נפרד לכל פלטפורמה: כותרת רק ל-Facebook, כיתוב
    ל-Instagram, והאשטגים; מסומן אם ההצעה מהמודל או לפי כללים.
  * הכול ניתן לעריכה, והערכים הערוכים (לא ההצעה) הם שנשמרים בעבודות הפרסום.
  * "הצעה חדשה" מחליפה את ההצעה (עם מודל → "מבוסס AI").
  * עברית (RTL) ומובייל 390px בלי גלילה אופקית; בלי שגיאות JavaScript.

הרצה:  python3 tests/test_social_metadata_ui.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
DATA = tempfile.mkdtemp(prefix="pxmetaui_")
os.environ["POLIXOR_DATA_DIR"] = DATA
os.environ["POLIXOR_SCHEDULER"] = "0"
os.environ.pop("POLIXOR_ACCESS_PASSWORD", None)

from polixor.config import PATHS, SECRETS                            # noqa: E402
from polixor.db import init_db, session_scope                        # noqa: E402
from polixor.models import (Clip, ClipKind, ClipStatus, Job, JobStatus,  # noqa: E402
                            PublishJob, SocialAccount, new_id)
from polixor.services import llm                                     # noqa: E402
from polixor.services.publishing import vault                        # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_meta import FakeMeta, _register                            # noqa: E402

PORT = 8811
BASE = f"http://127.0.0.1:{PORT}"
SEGMENTS = [
    (0.0, 6.0, "שלום לכולם."),
    (10.0, 16.0, "היום אני מראה לכם איך לבשל שקשוקה מושלמת בעשר דקות."),
    (16.0, 22.0, "הסוד של השקשוקה הוא עגבניות טריות ופלפל חריף."),
]


def seed() -> str:
    PATHS.ensure()
    init_db()
    SECRETS.set("meta_app_id", "1234567890")
    SECRETS.set("meta_app_secret", "not-real")
    media = Path(DATA) / "clip.mp4"
    media.write_bytes(b"\x00" * 4096)
    tp = Path(DATA) / "transcript.json"
    tp.write_text(json.dumps({"language": "he", "segments": [
        {"start": a, "end": b, "text": t} for a, b, t in SEGMENTS]}, ensure_ascii=False), "utf-8")
    jid, cid = new_id(), new_id()
    with session_scope() as s:
        s.add(SocialAccount(id="accfb", platform="facebook", external_id="P1", display_name="My Page",
                            access_token_enc=vault.encrypt("fake-page"), refresh_token_enc=vault.encrypt("fake-user"),
                            meta={"kind": "page"}))
        s.add(SocialAccount(id="accig", platform="instagram", external_id="IG1", display_name="My Brand",
                            handle="@mybrand", access_token_enc=vault.encrypt("fake-page"),
                            refresh_token_enc=vault.encrypt("fake-user"),
                            meta={"kind": "instagram", "page_id": "P1", "page_name": "My Page"}))
        s.add(Job(id=jid, title="Stream", status=JobStatus.COMPLETED, content_language="he",
                  artifacts={"transcript_path": str(tp)}, completed_stages=[], phase="done", run_scope="all"))
        s.flush()
        s.add(Clip(id=cid, job_id=jid, kind=ClipKind.SHORT, status=ClipStatus.READY, title="שקשוקה בעשר דקות",
                   file_path=str(media), file_size=4096, duration=12.0, width=1080, height=1920,
                   source_start=10.0, source_end=22.0))
    return cid


def main() -> int:
    import uvicorn
    from playwright.sync_api import sync_playwright

    from polixor.main import app

    cid = seed()
    _register(FakeMeta())
    llm_on = {"v": False}
    llm.is_llm_enabled = lambda settings: llm_on["v"]
    llm.call_model = lambda system, user, settings: json.dumps({"platforms": {
        p: {"title": "איך מבשלים שקשוקה מושלמת", "text": "הסוד: עגבניות טריות ופלפל חריף.",
            "hashtags": ["שקשוקה", "מתכון"]} for p in json.loads(user)["platforms"]}}, ensure_ascii=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
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
            page.route("**/*.facebook.com/**", lambda r: (outside.append(r.request.url), r.abort()))
            page.goto(BASE + f"/clips/{cid}/edit", wait_until="networkidle")
            page.locator("[data-testid=publish-open]").first.click()
            page.wait_for_selector("[data-testid=publish-dialog]")
            for aid in ("accfb", "accig"):
                box = page.locator(f"[data-testid=publish-account-{aid}]")
                if not box.is_checked():
                    box.check()
            page.wait_for_selector("[data-testid=metadata-box]")
            expect(page.locator("[data-testid=publish-title]").count() == 1,
                   "shared fields shown before a suggestion")
            page.click("[data-testid=metadata-suggest]")
            page.wait_for_selector("[data-testid=metadata-instagram]")
            src = page.inner_text("[data-testid=metadata-source]")
            expect("No language model" in src, f"rules-based suggestion is labelled as such ({src[:60]})")
            fb_title = page.input_value("[data-testid=metadata-facebook-title]")
            expect(fb_title == "שקשוקה בעשר דקות", f"Facebook title from the clip ({fb_title})")
            expect(page.locator("[data-testid=metadata-instagram-title]").count() == 0,
                   "Instagram has a caption only (no title field)")
            ig_text = page.input_value("[data-testid=metadata-instagram-text]")
            expect("שקשוקה" in ig_text and "שלום לכולם" not in ig_text,
                   "caption grounded in the clip's own transcript only")
            tags = page.input_value("[data-testid=metadata-instagram-hashtags]")
            expect(tags.startswith("#") and "שקשוקה" in tags, f"hashtags from transcript keywords ({tags})")
            expect(page.locator("[data-testid=publish-title]").count() == 0,
                   "shared fields hidden once every platform has its own variant")

            page.fill("[data-testid=metadata-facebook-title]", "My edited title")
            page.fill("[data-testid=metadata-facebook-text]", "Edited description")
            page.fill("[data-testid=metadata-instagram-text]", "Edited caption")
            page.fill("[data-testid=metadata-instagram-hashtags]", "#one, two #three")
            page.wait_for_selector("[data-testid=publish-ready]", timeout=15000)
            page.click("[data-testid=publish-submit]")
            page.wait_for_selector("text=Sent to the publishing queue", timeout=15000)
            with session_scope() as s:
                jobs = {j.platform: (j.title, j.description, list(j.tags or []))
                        for j in s.query(PublishJob).filter(PublishJob.clip_id == cid)}
            expect(jobs.get("facebook", ("",))[0] == "My edited title" and
                   jobs["facebook"][1] == "Edited description",
                   f"edited Facebook title/description saved ({jobs.get('facebook')})")
            expect(jobs.get("instagram", ("", "", []))[1] == "Edited caption" and
                   jobs["instagram"][2] == ["one", "two", "three"],
                   f"edited Instagram caption and hashtags saved ({jobs.get('instagram')})")
            expect(not outside, "no request left for a real platform")

            llm_on["v"] = True
            page.goto(BASE + f"/clips/{cid}/edit", wait_until="networkidle")
            page.locator("[data-testid=publish-open]").first.click()
            page.wait_for_selector("[data-testid=publish-dialog]")
            page.locator("[data-testid=publish-account-accfb]").check()
            page.click("[data-testid=metadata-suggest]")
            page.wait_for_selector("[data-testid=metadata-facebook]")
            page.click("[data-testid=metadata-suggest]")                 # "הצעה חדשה" – עם מודל
            page.wait_for_function(
                "document.querySelector('[data-testid=metadata-facebook-title]').value.includes('איך')",
                timeout=10000)
            expect("AI" in page.inner_text("[data-testid=metadata-source]"),
                   "new suggestion from the language model, labelled AI")
            expect(not errors, f"no JavaScript errors ({errors[:2]})")
            ctx.close()

            # ---- עברית + מובייל ----
            ctx = browser.new_context(locale="he-IL", timezone_id="Asia/Jerusalem",
                                      viewport={"width": 390, "height": 844})
            page = ctx.new_page()
            page.goto(BASE + f"/clips/{cid}/edit", wait_until="networkidle")
            page.locator("[data-testid=publish-open]").first.click()
            page.wait_for_selector("[data-testid=publish-dialog]")
            page.locator("[data-testid=publish-account-accig]").check()
            page.click("[data-testid=metadata-suggest]")
            page.wait_for_selector("[data-testid=metadata-instagram]")
            dlg = page.inner_text("[data-testid=metadata-box]")
            st = page.evaluate("() => ({dir: document.documentElement.dir, wide: document.documentElement.scrollWidth > innerWidth + 1})")
            expect(st["dir"] == "rtl" and not st["wide"], "Hebrew RTL, no horizontal scroll at 390px")
            expect("כיתוב" in dlg and "האשטגים" in dlg, "metadata editor labelled in Hebrew")
            ctx.close()
            browser.close()
    finally:
        server.should_exit = True
    print(f"\n{'כל' if not failures else len(failures)} {'בדיקות ממשק המטא-דאטה עברו' if not failures else 'בדיקות נכשלו'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        print("skipped: Playwright is not installed")
        sys.exit(0)
    sys.exit(main())
