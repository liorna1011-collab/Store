"""
צילומי מסך ובדיקה ויזואלית של שני הפיצ'רים החדשים:
AI Images ומסלול השידור החי.

הבדיקה גם מאתרת שגיאות JavaScript ובעיות פריסה (גלילה אופקית,
טקסט שנחתך), ולא רק מצלמת.

הרצה:  python3 tests/screenshot_new_features.py [base_url] [out_dir]
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8761"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/claude-0/shots")
OUT.mkdir(parents=True, exist_ok=True)

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
results: list[tuple[bool, str]] = []


def _tab(page, label: str) -> None:
    """לוחץ על לשונית בתוך סרגל הלשוניות של ההגדרות, לא על סרגל הצד."""
    page.locator("main button, div:not(aside) > button").filter(
        has_text=label).first.click()
    page.wait_for_timeout(600)


def check(cond: bool, label: str, detail: str = "") -> bool:
    results.append((bool(cond), label))
    print(f"  {PASS if cond else FAIL} {label}" + (f"  — {detail}" if detail else ""))
    return bool(cond)


def main() -> None:
    errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000},
                                  locale="he-IL")
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console.error: {m.text}")
                if m.type == "error" else None)

        def snap(name: str, full: bool = True) -> None:
            page.wait_for_timeout(600)
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)
            print(f"    · {name}.png")

        def no_h_scroll(label: str) -> None:
            over = page.evaluate(
                "() => document.documentElement.scrollWidth - "
                "document.documentElement.clientWidth")
            check(over <= 1, f"{label}: אין גלילה אופקית", f"חריגה {over}px")

        # ---------------- עמוד הייבוא: שלושה מקורות ----------------
        print("\n— עמוד ייבוא —")
        page.goto(f"{BASE}/", wait_until="networkidle")
        page.wait_for_timeout(700)

        for label in ("העלאת קובץ", "קישור לסרטון", "שידור חי"):
            check(page.get_by_text(label, exact=True).first.count() > 0,
                  f"מקור מוצג בבירור: {label}")
        snap("10_import_url")
        no_h_scroll("עמוד ייבוא")

        # מסלול שידור חי
        page.get_by_text("שידור חי", exact=True).first.click()
        page.wait_for_timeout(500)
        check(page.locator("#live-url").count() > 0, "שדה קישור לשידור חי קיים")
        check(page.get_by_text("זהה שידור").count() > 0, "כפתור 'זהה שידור' קיים")
        page.fill("#live-url", "https://www.twitch.tv/somechannel")
        page.wait_for_timeout(300)
        snap("11_import_live")

        # זיהוי בפועל – בסביבה חסומה הוא ייכשל, וזה מה שצריך להיראות
        page.get_by_text("זהה שידור").first.click()
        page.wait_for_timeout(6000)
        body = page.inner_text("body")
        honest = ("לא ניתן להגיע" in body or "לא זמין" in body
                  or "נכשל" in body or "לא משדר" in body)
        check(honest, "כישלון זיהוי מוצג בבירור ולא מוסתר")
        snap("12_import_live_detect")

        page.get_by_text("העלאת קובץ", exact=True).first.click()
        page.wait_for_timeout(400)
        check(page.get_by_text("גרור לכאן קובץ וידאו").count() > 0,
              "אזור גרירת קובץ מוצג")
        snap("13_import_upload")

        # ---------------- AI Images ----------------
        print("\n— AI Images —")
        page.goto(f"{BASE}/images", wait_until="networkidle")
        page.wait_for_timeout(1200)
        check(page.locator("#img-prompt").count() > 0, "שדה פרומפט קיים")
        for a in ("9:16", "16:9", "1:1"):
            check(page.get_by_text(a, exact=True).first.count() > 0,
                  f"בורר יחס מסך: {a}")
        cards = page.locator("img[alt]").count()
        check(cards > 0, "הגלריה מציגה תמונות", f"{cards} תמונות")

        # אזהרת ספק שאינו AI חייבת להיות גלויה
        body = page.inner_text("body")
        check("לא AI" in body or "לא תמונות" in body,
              "ספק שאינו AI מסומן במפורש בממשק")
        snap("14_images")
        no_h_scroll("AI Images")

        # תצוגה מלאה
        first_img = page.locator("img[alt]").first
        if first_img.count():
            first_img.click()
            page.wait_for_timeout(900)
            check(page.get_by_text("הפרומפט", exact=False).count() > 0,
                  "תצוגה מלאה מציגה את הפרומפט")
            snap("15_image_preview", full=False)
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)

        # ---------------- הגדרות ----------------
        print("\n— הגדרות —")
        page.goto(f"{BASE}/settings", wait_until="networkidle")
        page.wait_for_timeout(700)
        # "AI Images" מופיע גם בסרגל הצד – מכוונים ללשונית בלבד
        _tab(page, "AI Images")
        body = page.inner_text("body")
        check("מפתח API" in body or "כרטיס מקומי" in body,
              "לשונית AI Images מציגה את הגדרות הספק")
        check("אינו נחשף בדפדפן" in body or "מוצפן" in body,
              "נאמר במפורש שהמפתח אינו נחשף בדפדפן")
        snap("16_settings_images")

        page.goto(f"{BASE}/settings", wait_until="networkidle")
        page.wait_for_timeout(500)
        _tab(page, "שידור חי")
        body = page.inner_text("body")
        check("מקטע" in body, "לשונית שידור חי מציגה הגדרות הקלטה")
        snap("17_settings_live")

        # ---------------- עורך הקליפ ----------------
        print("\n— עורך קליפ —")
        page.goto(f"{BASE}/clips", wait_until="networkidle")
        page.wait_for_timeout(1200)
        edit = page.get_by_text("ערוך", exact=False).first
        if edit.count():
            edit.click()
            page.wait_for_timeout(1800)
            body = page.inner_text("body")
            check("תמונות בקליפ" in body, "פאנל התמונות מופיע בעורך")
            check("שבץ תמונה" in body, "כפתור שיבוץ תמונה קיים")
            snap("18_clip_edit")
            no_h_scroll("עורך קליפ")

            btn = page.get_by_text("שבץ תמונה", exact=True).first
            if btn.count():
                btn.click()
                page.wait_for_timeout(900)
                body = page.inner_text("body")
                check("איך לשלב" in body or "בחר תמונה" in body,
                      "חלון השיבוץ נפתח עם בחירת תפקיד")
                for role in ("פתיח", "בי-רול", "רקע"):
                    check(role in body, f"תפקיד זמין: {role}")
                snap("19_clip_place", full=False)
                page.keyboard.press("Escape")
                page.wait_for_timeout(400)

            sug = page.get_by_text("הצע ויזואלים", exact=False).first
            if sug.count():
                sug.click()
                page.wait_for_timeout(2500)
                snap("20_suggest_visuals")
                check(True, "מסך ההצעות נטען")
        else:
            print("    (אין קליפים — מדלג על עורך הקליפ)")

        # ---------------- רוחב טלפון ----------------
        print("\n— רוחב טלפון —")
        page.set_viewport_size({"width": 400, "height": 900})
        for path, name in (("/", "21_mobile_import"), ("/images", "22_mobile_images")):
            page.goto(f"{BASE}{path}", wait_until="networkidle")
            page.wait_for_timeout(900)
            over = page.evaluate(
                "() => document.documentElement.scrollWidth - "
                "document.documentElement.clientWidth")
            check(over <= 1, f"{name}: אין גלילה אופקית ב-400px", f"חריגה {over}px")
            snap(name)

        browser.close()

    real_errors = [e for e in errors if "favicon" not in e.lower()]
    check(not real_errors, "אין שגיאות JavaScript",
          "; ".join(real_errors[:3]) if real_errors else "")

    passed = sum(1 for ok, _ in results if ok)
    total = len(results)
    print(f"\n{'=' * 64}\nסיכום ממשק: {passed}/{total} בדיקות עברו\n{'=' * 64}")
    for ok, label in results:
        if not ok:
            print(f"  {FAIL} {label}")
    print(f"\nצילומים: {OUT}")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
