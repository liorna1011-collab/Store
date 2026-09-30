"""
בדיקות לבחירה האוטומטית של שפת הממשק (שלב 4).

  * השרת: שרשרת השפה (?lang= ← X-Polixor-Lang ← כותרת מדינה ←
    Accept-Language), /api/locale, כותרות מדינה לא תקינות, כיבוי.
  * הדפדפן (Playwright, שרת אמיתי): אין בורר שפה; ישראל (כותרת מדינה /
    אזור זמן / שפת דפדפן) → עברית RTL; אחרת אנגלית LTR; בחירה ידנית ישנה
    נמחקת ולא משפיעה; ?lang= נסתר עובד ללשונית בלבד; מובייל בלי גלילה
    אופקית.

הרצה:  python3 tests/test_ui_language.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxlang_"))

ROOT = Path(__file__).resolve().parents[1]
PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"


# --------------------------------------------------------------------------
# שרת
# --------------------------------------------------------------------------
def _scope(query: str = "", **headers: str) -> dict:
    return {"query_string": query.encode(),
            "headers": [(k.replace("_", "-").lower().encode(), v.encode())
                        for k, v in headers.items()]}


def test_request_language_chain():
    from polixor.main import request_language

    assert request_language(_scope("lang=en", x_polixor_lang="he", cf_ipcountry="IL")) == "en"
    assert request_language(_scope(x_polixor_lang="en", cf_ipcountry="IL")) == "en"
    assert request_language(_scope(cf_ipcountry="IL", accept_language="en-US")) == "he"
    assert request_language(_scope(cf_ipcountry="il")) == "he"
    # מדינה אחרת – לפי הדפדפן
    assert request_language(_scope(cf_ipcountry="US", accept_language="he-IL")) == "he"
    assert request_language(_scope(cf_ipcountry="US", accept_language="en-US")) == "en"
    # לא ידוע / Tor / זבל – מתעלמים
    for bad in ("XX", "T1", "ISR", "1L", ""):
        assert request_language(_scope(cf_ipcountry=bad, accept_language="en")) == "en", bad


def test_country_headers_can_be_restricted_or_disabled():
    from polixor import i18n

    try:
        os.environ["POLIXOR_COUNTRY_HEADERS"] = "none"
        assert i18n.country_from_headers({"cf-ipcountry": "IL"}) is None
        os.environ["POLIXOR_COUNTRY_HEADERS"] = "X-My-Country"
        assert i18n.country_from_headers({"cf-ipcountry": "IL"}) is None
        assert i18n.country_from_headers({"x-my-country": "IL"}) == "IL"
    finally:
        os.environ.pop("POLIXOR_COUNTRY_HEADERS", None)
    assert i18n.country_from_headers({"cloudfront-viewer-country": "IL"}) == "IL"


def test_locale_endpoint_is_public_and_reveals_no_location():
    from fastapi.testclient import TestClient

    from polixor import access
    from polixor.main import app

    os.environ["POLIXOR_ACCESS_PASSWORD"] = "test-only-password"
    try:
        c = TestClient(app)
        r = c.get("/api/locale", headers={"CF-IPCountry": "IL"})
        assert r.status_code == 200, r.status_code
        assert r.json() == {"lang": "he", "dir": "rtl", "source": "country", "country_known": True}
        r = c.get("/api/locale", headers={"CF-IPCountry": "DE"})
        assert r.json() == {"lang": None, "dir": None, "source": None, "country_known": True}
        assert "DE" not in r.text                                   # לא מחזירים את המדינה עצמה
        assert c.get("/api/locale").json()["country_known"] is False
        assert "/api/locale" in access.PUBLIC_PATHS
        # דף הכניסה: בשפה שזוהתה, בלי קישור להחלפת שפה; ?lang= הנסתר עדיין עובד
        page = c.get("/login", headers={"CF-IPCountry": "IL", "Accept-Language": "en-US"}).text
        assert 'lang="he"' in page and 'dir="rtl"' in page
        assert "lang=en" not in page and "lang=he" not in page
        assert 'lang="en"' in c.get("/login", headers={"Accept-Language": "en-US"}).text
        assert 'lang="en"' in c.get("/login?lang=en", headers={"CF-IPCountry": "IL"}).text
    finally:
        os.environ.pop("POLIXOR_ACCESS_PASSWORD", None)


# --------------------------------------------------------------------------
# דפדפן
# --------------------------------------------------------------------------
def _wait_up(timeout: float = 60.0) -> None:
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{BASE}/api/health", timeout=2)
            return
        except Exception:                               # noqa: BLE001
            time.sleep(0.5)
    raise RuntimeError("server did not start")


def _page_state(p, path: str = "/", *, locale: str = "en-US", tz: str = "UTC",
                headers: dict | None = None, init: str = "", viewport=(1280, 900)):
    browser = p.chromium.launch()
    try:
        ctx = browser.new_context(locale=locale, timezone_id=tz,
                                  viewport={"width": viewport[0], "height": viewport[1]},
                                  extra_http_headers=headers or {})
        if init:
            ctx.add_init_script(init)
        page = ctx.new_page()
        page.goto(BASE + path, wait_until="networkidle")
        page.wait_for_selector("header")
        st = page.evaluate("""() => ({
            lang: document.documentElement.lang, dir: document.documentElement.dir,
            legacy: localStorage.getItem('polixor.lang'),
            selects: [...document.querySelectorAll('select')].map(s => [...s.options].map(o => o.value).join(',')),
            wide: document.documentElement.scrollWidth > window.innerWidth + 1,
        })""")
        return page, st, browser
    except Exception:
        browser.close()
        raise


def browser_checks() -> list[str]:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def expect(cond: bool, label: str) -> None:
        print(f"  {'✓' if cond else '✗'} {label}")
        if not cond:
            failures.append(label)

    cases = [
        ("English browser, no country → English LTR", {}, ("en", "ltr")),
        ("Israeli time zone → Hebrew RTL", {"tz": "Asia/Jerusalem"}, ("he", "rtl")),
        ("Hebrew browser language → Hebrew RTL", {"locale": "he-IL"}, ("he", "rtl")),
        ("Country header IL (CDN) → Hebrew RTL", {"headers": {"CF-IPCountry": "IL"}}, ("he", "rtl")),
        ("Country US + Hebrew browser → Hebrew", {"headers": {"CF-IPCountry": "US"}, "locale": "he-IL"},
         ("he", "rtl")),
        ("Country US + English browser → English", {"headers": {"CF-IPCountry": "US"}}, ("en", "ltr")),
        ("Old saved manual choice is ignored and removed",
         {"init": "localStorage.setItem('polixor.lang','he')"}, ("en", "ltr")),
        ("Old saved choice doesn't beat Israel either",
         {"init": "localStorage.setItem('polixor.lang','en')", "tz": "Asia/Jerusalem"}, ("he", "rtl")),
    ]
    with sync_playwright() as p:
        for label, kw, (lang, dirn) in cases:
            page, st, browser = _page_state(p, "/", **kw)
            expect(st["lang"] == lang and st["dir"] == dirn, f"{label} ({st['lang']}/{st['dir']})")
            expect(st["legacy"] is None, f"{label}: legacy key removed")
            browser.close()

        # אין בורר שפה – לא בסרגל ולא בהגדרות
        for lang_kw in ({}, {"tz": "Asia/Jerusalem"}):
            page, st, browser = _page_state(p, "/settings", **lang_kw)
            page.wait_for_timeout(300)
            selects = page.evaluate("() => [...document.querySelectorAll('select')].map(s => [...s.options].map(o => o.value).join(','))")
            expect(not any(set(v.split(",")) >= {"he", "en"} for v in selects),
                   f"no language selector on /settings ({st['lang']})")
            browser.close()

        # ?lang= נסתר: עוקף, נשמר ללשונית, ?lang=auto מבטל
        page, st, browser = _page_state(p, "/?lang=he")
        expect(st["lang"] == "he", "hidden ?lang=he overrides an English browser")
        page.goto(BASE + "/settings", wait_until="networkidle")
        expect(page.evaluate("document.documentElement.lang") == "he",
               "?lang= stays for the tab after navigating without it")
        page.goto(BASE + "/?lang=auto", wait_until="networkidle")
        expect(page.evaluate("document.documentElement.lang") == "en", "?lang=auto returns to automatic")
        ctx2 = browser.new_context(locale="en-US", timezone_id="UTC")
        p2 = ctx2.new_page()
        p2.goto(BASE + "/", wait_until="networkidle")
        expect(p2.evaluate("document.documentElement.lang") == "en",
               "?lang= does not leak into a new browser session")
        browser.close()

        # מובייל, עברית: RTL ובלי גלילה אופקית
        for path in ("/", "/settings", "/new"):
            page, st, browser = _page_state(p, path, tz="Asia/Jerusalem", viewport=(390, 844))
            expect(st["dir"] == "rtl" and not st["wide"], f"mobile {path}: RTL, no horizontal scroll")
            browser.close()

        # הודעות השרת בשפת הממשק שזוהתה
        page, st, browser = _page_state(p, "/", headers={"CF-IPCountry": "IL"})
        cl = page.evaluate("fetch('/api/system').then(r => r.headers.get('content-language'))")
        expect(cl == "he", f"server replies in the detected language (content-language={cl})")
        browser.close()
    return failures


def test_browser_language_behaviour():
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        print("  (skipped: Playwright is not installed – the browser checks need it)")
        return
    env = {**os.environ}
    env.pop("POLIXOR_ACCESS_PASSWORD", None)
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "polixor.main:app",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        _wait_up()
        failures = browser_checks()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
    assert not failures, failures


# --------------------------------------------------------------------------
def _run_all() -> int:
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    passed, failed = 0, []
    for name, fn in fns:
        try:
            fn()
            print(f"  \033[92m✓\033[0m {name}")
            passed += 1
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"  \033[91m✗\033[0m {name}: {type(exc).__name__}: {exc}")
            failed.append(name)
    print(f"\n{passed}/{len(fns)} בדיקות שפת ממשק עברו")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
