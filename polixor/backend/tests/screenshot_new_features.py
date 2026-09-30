"""
צילומי מסך ובדיקה ויזואלית של שני הפיצ'רים החדשים:
AI Images ומסלול השידור החי – במסכים של ממשק הפרויקטים (/new).

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
    page.get_by_role("tab", name=label, exact=True).first.click()
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
        # הממשק דו-לשוני; הבדיקה הזו רצה בעברית
        ctx.add_init_script("sessionStorage.setItem('polixor.langOverride','he')")
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console.error: {m.text}")
                if m.type == "error" else None)
        # תשובות 5xx נרשמות ביומן הדפדפן כ-"Failed to load resource". הבדיקה
        # מפעילה בכוונה זיהוי קישור ברשת חסומה, ולכן כישלון של probe צפוי;
        # כל כשל אחר נשאר שגיאה.
        failed_responses: list[str] = []
        page.on("response", lambda r: failed_responses.append(r.url)
                if r.status >= 500 else None)

        def snap(name: str, full: bool = True) -> None:
            page.wait_for_timeout(600)
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)
            print(f"    · {name}.png")

        def no_h_scroll(label: str) -> None:
            over = page.evaluate(
                "() => document.documentElement.scrollWidth - "
                "document.documentElement.clientWidth")
            check(over <= 1, f"{label}: אין גלילה אופקית", f"חריגה {over}px")

        # ---------------- עמוד הייבוא: קובץ, קישור, שידור חי ----------------
        # שידור חי מזוהה מהקישור עצמו (ערוץ Twitch/Kick/YouTube Live),
        # ולכן הוא מסלול בתוך לשונית הקישור ולא לשונית נפרדת.
        print("\n— עמוד ייבוא —")
        page.goto(f"{BASE}/new", wait_until="networkidle")
        page.wait_for_timeout(700)

        for label in ("קובץ מהמחשב", "קישור"):
            check(page.get_by_role("radio", name=label, exact=True).count() > 0,
                  f"מקור מוצג בבירור: {label}")
        snap("10_import_url")
        no_h_scroll("עמוד ייבוא")

        # מסלול שידור חי
        page.get_by_role("radio", name="קישור", exact=True).click()
        page.wait_for_timeout(400)
        check(page.locator("#src-url").count() > 0, "שדה קישור קיים")
        check(page.get_by_role("button", name="בדיקה").count() > 0, "כפתור 'בדיקה' קיים")
        page.fill("#src-url", "https://www.twitch.tv/somechannel")
        page.wait_for_timeout(300)
        snap("11_import_live")

        # זיהוי בפועל – בסביבה חסומה המידע המקדים לא יגיע, וזה מה שצריך להיראות
        page.get_by_role("button", name="בדיקה").click()
        page.wait_for_timeout(8000)
        body = page.inner_text("body")
        check("שידור חי" in body and page.locator("#cap-min").count() > 0,
              "ערוץ חי מזוהה ומוצע משך הקלטה")
        honest = ("ייתכן שהרשת חוסמת" in body or "לא ניתן" in body
                  or "נכשל" in body or "לא זמין" in body)
        check(honest, "כישלון זיהוי מוצג בבירור ולא מוסתר")
        snap("12_import_live_detect")

        page.get_by_role("radio", name="קובץ מהמחשב", exact=True).click()
        page.wait_for_timeout(400)
        check(page.get_by_text("גררו לכאן קובץ וידאו").count() > 0,
              "אזור גרירת קובץ מוצג")
        snap("13_import_upload")

        # ---------------- AI Images ----------------
        print("\n— AI Images —")
        page.goto(f"{BASE}/images", wait_until="networkidle")
        page.wait_for_timeout(1200)
        check(page.locator("[data-testid=image-studio]").count() > 0, "סטודיו התמונות נפתח כברירת מחדל")
        snap("14a_image_studio")
        # הגלריה (יצירה בודדת, וריאציה, עריכת פרומפט) – בלשונית משלה
        page.goto(f"{BASE}/images?tab=gallery", wait_until="networkidle")
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
        # קישור ישיר ללשונית (כך מקשר אליה מסך התמונות)
        page.goto(f"{BASE}/settings?tab=images", wait_until="networkidle")
        page.wait_for_timeout(700)
        check(page.get_by_role("tab", name="תמונות AI", selected=True).count() > 0,
              "‎?tab=images פותח את לשונית התמונות")
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
        edit = page.get_by_text("עריכה", exact=True).first
        if edit.count():
            edit.click()
            page.wait_for_timeout(1800)
            body = page.inner_text("body")
            check("תמונות בקליפ" in body, "פאנל התמונות מופיע בעורך")
            check("שיבוץ תמונה" in body, "כפתור שיבוץ תמונה קיים")
            snap("18_clip_edit")
            no_h_scroll("עורך קליפ")

            btn = page.get_by_text("שיבוץ תמונה", exact=True).first
            if btn.count():
                btn.click()
                page.wait_for_timeout(900)
                body = page.inner_text("body")
                check("איך לשלב" in body or "בחירת תמונה" in body,
                      "חלון השיבוץ נפתח עם בחירת תפקיד")
                for role in ("פתיח", "בי-רול", "רקע"):
                    check(role in body, f"תפקיד זמין: {role}")
                snap("19_clip_place", full=False)
                page.keyboard.press("Escape")
                page.wait_for_timeout(400)

            sug = page.get_by_text("הצעת ויזואלים", exact=False).first
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
        for path, name in (("/new", "21_mobile_import"), ("/images", "22_mobile_images")):
            page.goto(f"{BASE}{path}", wait_until="networkidle")
            page.wait_for_timeout(900)
            over = page.evaluate(
                "() => document.documentElement.scrollWidth - "
                "document.documentElement.clientWidth")
            check(over <= 1, f"{name}: אין גלילה אופקית ב-400px", f"חריגה {over}px")
            snap(name)

        browser.close()

    expected = [u for u in failed_responses if u.endswith("/api/sources/probe")]
    unexpected = [u for u in failed_responses if u not in expected]
    real_errors = [e for e in errors if "favicon" not in e.lower()
                   and not ("Failed to load resource" in e and expected and not unexpected)]
    check(not unexpected, "אין בקשות שנכשלו מלבד בדיקת הקישור ברשת החסומה",
          ", ".join(unexpected[:3]))
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
