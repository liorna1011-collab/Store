"""
צילומי מסך של הממשק דרך Chromium (Playwright) – בדיקה ויזואלית
שכל המסכים נטענים ומציגים נתונים אמיתיים מהשרת.

הרצה:  python3 tests/screenshot_ui.py [base_url] [out_dir]
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8756"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "/home/claude/shots")
OUT.mkdir(parents=True, exist_ok=True)


def main() -> None:
    errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 950},
                                  locale="he-IL", device_scale_factor=1)
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" else None)

        def shot(name: str, path: str, wait: str | None = None,
                 full: bool = False, delay: int = 900) -> None:
            page.goto(f"{BASE}{path}", wait_until="networkidle")
            if wait:
                try:
                    page.wait_for_selector(wait, timeout=8000)
                except Exception:
                    errors.append(f"{name}: selector not found: {wait}")
            page.wait_for_timeout(delay)
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)
            print(f"  ✓ {name}.png  ({path})")

        print("מצלם מסכים:")
        shot("01_home", "/", wait="text=התחלת ניתוח")

        # הדבקת קישור כדי להראות זיהוי פלטפורמה
        page.goto(f"{BASE}/", wait_until="networkidle")
        page.fill("#stream-url", "https://www.twitch.tv/videos/123456789")
        page.wait_for_timeout(1400)
        page.screenshot(path=str(OUT / "02_home_detected.png"))
        print("  ✓ 02_home_detected.png  (זיהוי פלטפורמה)")

        shot("03_jobs", "/jobs", wait="text=משימות")

        # פרטי המשימה האחרונה
        page.goto(f"{BASE}/jobs", wait_until="networkidle")
        page.wait_for_timeout(700)
        first = page.locator("div.card button").first
        if first.count():
            first.click()
            page.wait_for_timeout(1600)
            page.screenshot(path=str(OUT / "04_job_detail.png"))
            print("  ✓ 04_job_detail.png  (שלבים + מפת עניין)")
            job_url = page.url
            # לשונית קליפים
            page.get_by_text("קליפים", exact=False).first.click()
            page.wait_for_timeout(900)
            page.screenshot(path=str(OUT / "05_job_clips.png"))
            print("  ✓ 05_job_clips.png")
            # לשונית תמלול
            page.goto(job_url, wait_until="networkidle")
            page.get_by_text("תמלול", exact=True).first.click()
            page.wait_for_timeout(1200)
            page.screenshot(path=str(OUT / "06_job_transcript.png"))
            print("  ✓ 06_job_transcript.png")
        else:
            errors.append("no job rows found on /jobs")

        shot("07_clips", "/clips", wait="text=גלריית קליפים")

        # תצוגה מקדימה
        page.goto(f"{BASE}/clips", wait_until="networkidle")
        page.wait_for_timeout(1200)
        play = page.locator('button[aria-label="נגן"]').first
        if play.count():
            play.click(force=True)
            page.wait_for_timeout(2200)
            page.screenshot(path=str(OUT / "08_clip_preview.png"))
            print("  ✓ 08_clip_preview.png  (נגן וידאו)")
            page.keyboard.press("Escape")
        else:
            errors.append("no playable clip card found")

        # מסך עריכה
        page.goto(f"{BASE}/clips", wait_until="networkidle")
        page.wait_for_timeout(1000)
        edit = page.get_by_text("ערוך", exact=True).first
        if edit.count():
            edit.click()
            page.wait_for_timeout(2400)
            page.screenshot(path=str(OUT / "09_clip_edit.png"))
            page.screenshot(path=str(OUT / "09b_clip_edit_full.png"), full_page=True)
            print("  ✓ 09_clip_edit.png  (עריכה + כתוביות)")
        else:
            errors.append("no edit button found")

        shot("10_settings", "/settings", wait="text=הגדרות")

        for tab, name in (("קליפים וייצוא", "11_settings_clips"),
                          ("כתוביות", "12_settings_subs"),
                          ("מנוע AI", "13_settings_ai"),
                          ("מערכת ואחסון", "14_settings_system")):
            page.goto(f"{BASE}/settings", wait_until="networkidle")
            page.wait_for_timeout(600)
            page.get_by_text(tab, exact=True).first.click()
            page.wait_for_timeout(900)
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
            print(f"  ✓ {name}.png  ({tab})")

        browser.close()

    real = [e for e in errors if "favicon" not in e.lower()
            and "ERR_" not in e and "404" not in e]
    print(f"\nשגיאות דפדפן: {len(real)}")
    for e in real[:12]:
        print(f"  ! {e}")
    print(f"\nצילומים נשמרו ב: {OUT}")
    sys.exit(1 if real else 0)


if __name__ == "__main__":
    main()
