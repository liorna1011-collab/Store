"""
בדיקת דפדפן לסטודיו התמונות (שלב 11). השרת רץ באותו תהליך, ו-OpenAI מדומה
בצד השרת (httpx.MockTransport). הדפדפן חסום מלפנות ל-api.openai.com.

  * "תמונות AI" נפתח בסטודיו: כותבים → תמונה; בקשת שינוי → "גרסה ערוכה"
    (/images/edits על התמונה הנוכחית).
  * צירוף קובץ → תצוגה מקדימה; נשלח כייחוס (כמה תמונות קלט בבקשה אחת).
  * "שימוש כייחוס" מוסיף תמונה מהשיחה לצירופים.
  * "הוספה לסרטון": B-roll בקליפ ותמונת שער – נשמרים בשרת.
  * היסטוריה: טעינה מחדש מציגה את השיחה; "שיחה חדשה".
  * הגלריה הקיימת עדיין זמינה בלשונית.
  * עברית (RTL) ומובייל 390px בלי גלילה אופקית; בלי שגיאות JavaScript.

הרצה:  python3 tests/test_image_studio_ui.py
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
DATA = tempfile.mkdtemp(prefix="pxstudioui_")
os.environ["POLIXOR_DATA_DIR"] = DATA
os.environ["POLIXOR_SCHEDULER"] = "0"
os.environ.pop("POLIXOR_ACCESS_PASSWORD", None)

import httpx                                                         # noqa: E402

from polixor.config import PATHS, SECRETS, SETTINGS                  # noqa: E402
from polixor.db import init_db, session_scope                        # noqa: E402
from polixor.models import (Clip, ClipKind, ClipStatus, ImagePlacement, Job,  # noqa: E402
                            JobStatus, new_id)
from polixor.services import images                                  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_image_studio import FAKE_KEY, FakeOpenAI, png              # noqa: E402

PORT = 8813
BASE = f"http://127.0.0.1:{PORT}"


def seed() -> str:
    PATHS.ensure()
    init_db()
    SECRETS.set("openai_api_key", FAKE_KEY)
    SETTINGS.update({"image_provider": "openai", "image_model": "gpt-image-2.5-sunburst",
                     "image_quality": "high", "image_retries": 0})
    media = Path(DATA) / "clip.mp4"
    media.write_bytes(b"\x00" * 4096)
    jid, cid = new_id(), new_id()
    with session_scope() as s:
        s.add(Job(id=jid, title="Stream", status=JobStatus.COMPLETED, artifacts={}, completed_stages=[],
                  phase="done", run_scope="all"))
        s.flush()
        s.add(Clip(id=cid, job_id=jid, kind=ClipKind.SHORT, status=ClipStatus.READY, title="Best moment",
                   file_path=str(media), file_size=4096, duration=20.0, width=1080, height=1920))
    return cid


def main() -> int:
    import uvicorn
    from playwright.sync_api import sync_playwright

    from polixor.main import app

    cid = seed()
    fake = FakeOpenAI()
    images.OpenAIImageProvider.transport = httpx.MockTransport(fake.handler)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(120):
        try:
            urllib.request.urlopen(f"{BASE}/api/health", timeout=1)
            break
        except Exception:                               # noqa: BLE001
            time.sleep(0.25)
    ref = Path(DATA) / "logo.png"
    ref.write_bytes(png(200, 120, (20, 120, 220)))

    failures: list[str] = []

    def expect(cond: bool, label: str) -> None:
        print(f"  {'✓' if cond else '✗'} {label}")
        if not cond:
            failures.append(label)

    def ready_count(page) -> int:
        return page.locator("[data-testid=studio-assistant-msg][data-status=ready]").count()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(locale="en-US", viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            outside: list[str] = []
            page.route("**/api.openai.com/**", lambda r: (outside.append(r.request.url), r.abort()))
            page.goto(BASE + "/images", wait_until="networkidle")
            page.wait_for_selector("[data-testid=image-studio]")
            expect("gpt-image-2.5-sunburst" in page.inner_text("[data-testid=image-studio]"),
                   "studio opens by default and shows the active model")
            expect(page.locator("[data-testid=studio-attach]").count() == 1,
                   "attach is offered (model supports reference images)")
            expect(page.locator("[data-testid=studio-background]").count() == 1,
                   "transparent background offered (model supports it)")

            page.fill("[data-testid=studio-input]", "A bold neon title card for a gaming stream")
            page.click("[data-testid=studio-send]")
            page.wait_for_function("document.querySelectorAll('[data-testid=studio-assistant-msg][data-status=ready]').length >= 1",
                                   timeout=20000)
            expect(page.locator("[data-testid=studio-image]").count() == 1, "first image appears in the conversation")
            expect(fake.calls[-1]["path"] == "/v1/images/generations", "first request creates a new image")

            page.set_input_files("[data-testid=studio-file]", str(ref))
            page.wait_for_selector("[data-testid=studio-attachments] img")
            expect(True, "attached file previewed before sending")
            expect("Edit the current image" in page.inner_text("[data-testid=studio-mode]"),
                   "follow-up edits the current image by default")
            page.fill("[data-testid=studio-input]", "Add the attached logo in the corner")
            page.click("[data-testid=studio-send]")
            page.wait_for_function("document.querySelectorAll('[data-testid=studio-assistant-msg][data-status=ready]').length >= 2",
                                   timeout=20000)
            last = fake.calls[-1]
            expect(last["path"] == "/v1/images/edits" and last["images"] == 2,
                   f"edit sent with current image + reference ({last.get('images')})")
            expect("Edited version" in page.inner_text("[data-testid=studio-messages]"), "edited version labelled")
            expect(page.locator("[data-testid=studio-attachments]").count() == 0, "attachments cleared after sending")

            page.locator("[data-testid=studio-use-ref]").first.click()
            expect(page.locator("[data-testid=studio-attachments] img").count() == 1,
                   "an earlier image can be reused as a reference")
            page.locator("[data-testid=studio-attachments] button").first.click()

            # ---- הוספה לסרטון ----
            page.locator("[data-testid=studio-place]").last.click()
            page.wait_for_selector("[data-testid=studio-place-modal]")
            expect("Best moment" in page.inner_text("[data-testid=studio-place-clip]"), "clip list offered")
            page.click("[data-testid=studio-role-broll]")
            page.click("[data-testid=studio-place-confirm]")
            page.wait_for_selector("[data-testid=studio-placed]", timeout=10000)
            page.keyboard.press("Escape")
            page.locator("[data-testid=studio-place]").last.click()
            page.wait_for_selector("[data-testid=studio-place-modal]")
            page.click("[data-testid=studio-role-thumbnail]")
            page.click("[data-testid=studio-place-confirm]")
            page.wait_for_selector("[data-testid=studio-placed]", timeout=10000)
            page.keyboard.press("Escape")
            with session_scope() as s:
                roles = sorted(pl.role.value for pl in s.query(ImagePlacement).filter(ImagePlacement.clip_id == cid))
                thumb = s.get(Clip, cid).thumbnail_path
            expect(roles == ["broll", "thumbnail"] and thumb.endswith(".jpg"),
                   f"B-roll placement and clip thumbnail saved ({roles})")

            # ---- היסטוריה ----
            page.reload(wait_until="networkidle")
            page.wait_for_function("document.querySelectorAll('[data-testid=studio-assistant-msg]').length >= 2",
                                   timeout=10000)
            expect(page.locator("[data-testid=studio-user-msg]").count() == 2, "conversation history restored after reload")
            page.click("[data-testid=studio-new]")
            page.wait_for_function("document.querySelectorAll('[data-testid=studio-assistant-msg]').length === 0")
            expect(page.locator("[data-testid=studio-thread]").count() == 1,
                   "new conversation starts empty and isn't saved until the first message")
            page.fill("[data-testid=studio-input]", "A calm lake at dawn")
            page.click("[data-testid=studio-send]")
            page.wait_for_function("document.querySelectorAll('[data-testid=studio-thread]').length === 2", timeout=10000)
            page.wait_for_function("document.querySelectorAll('[data-testid=studio-assistant-msg][data-status=ready]').length === 1",
                                   timeout=20000)
            expect(page.locator("[data-testid=studio-user-msg]").count() == 1,
                   "second conversation saved on its first message, separate history")
            page.click("[data-testid=images-tab-gallery]")
            page.wait_for_selector("#img-prompt")
            expect(page.locator("text=Gallery").count() >= 1, "gallery tab still available")
            expect(not outside, "the browser never contacted OpenAI")
            expect(not errors, f"no JavaScript errors ({errors[:2]})")
            ctx.close()

            # ---- עברית + מובייל ----
            ctx = browser.new_context(locale="he-IL", timezone_id="Asia/Jerusalem",
                                      viewport={"width": 390, "height": 844})
            page = ctx.new_page()
            page.goto(BASE + "/images", wait_until="networkidle")
            page.wait_for_selector("[data-testid=image-studio]")
            page.wait_for_selector("[data-testid=studio-image]", timeout=10000)
            st = page.evaluate("() => ({dir: document.documentElement.dir, wide: document.documentElement.scrollWidth > innerWidth + 1})")
            expect(st["dir"] == "rtl" and not st["wide"], "Hebrew RTL, no horizontal scroll at 390px")
            txt = page.inner_text("[data-testid=image-studio]")
            expect("שליחה" in txt and "הוספה לסרטון" in txt, "studio labels in Hebrew")
            expect(not page.locator("[data-testid=studio-threads]").is_visible(), "conversation list folded on mobile")
            page.click("[data-testid=studio-toggle-list]")
            expect(page.locator("[data-testid=studio-threads]").is_visible(), "conversation list opens on mobile")
            page.locator("[data-testid=studio-thread]").first.click()
            page.fill("[data-testid=studio-input]", "תהפוך את הרקע לכחול")
            page.click("[data-testid=studio-send]")
            page.wait_for_function("document.querySelectorAll('[data-testid=studio-assistant-msg][data-status=ready]').length >= 1",
                                   timeout=20000)
            wide = page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
            expect(not wide, "Hebrew request works on mobile, still no horizontal scroll")
            page.locator("[data-testid=studio-place]").last.click()
            page.wait_for_selector("[data-testid=studio-place-modal]")
            wide = page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
            expect(not wide and "תמונת שער" in page.inner_text("[data-testid=studio-place-modal]"),
                   "add-to-video dialog in Hebrew on mobile")
            ctx.close()
            browser.close()
    finally:
        server.should_exit = True
    print(f"\n{'כל' if not failures else len(failures)} {'בדיקות ממשק הסטודיו עברו' if not failures else 'בדיקות נכשלו'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        print("skipped: Playwright is not installed")
        sys.exit(0)
    sys.exit(main())
