"""
בדיקות להתראות (שלב 5).

  * שירות: יצירה, קיבוץ (count), מצבים (דורש טיפול / חדש / קודם), סימון
    כנקרא, מחיקה, גיזום, סוג לא מוכר, כשל שלא מפיל את הפעולה.
  * עברית ואנגלית: לכל סוג יש כותרת וגוף בשתי השפות, בלי פרמטר שלא הוחלף;
    שגיאה נשמרת כרשומה ומתורגמת לשפת הצפייה.
  * אירועים אמיתיים: ה-worker (ניתוח הסתיים / קליפים מוכנים / חלקם דורשים
    בדיקה / אין קליפים / כשל), והודעת WebSocket לכל שינוי.
  * API.
  * דפדפן (Playwright, שרת אמיתי): פעמון עם מונה, לוח מקובץ בעברית ובאנגלית,
    לחיצה מנווטת ומסמנת כנקרא, עדכון חי, מובייל בלי גלילה אופקית.

הרצה:  python3 tests/test_notifications.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
DATA = os.environ.get("POLIXOR_DATA_DIR") or tempfile.mkdtemp(prefix="pxnotif_")
os.environ["POLIXOR_DATA_DIR"] = DATA

from polixor import i18n                                          # noqa: E402
from polixor.config import PATHS                                  # noqa: E402
from polixor.db import init_db, session_scope                     # noqa: E402
from polixor.errors import PolixorError                           # noqa: E402
from polixor.events import BUS                                    # noqa: E402
from polixor.models import (Clip, ClipStatus, Job, JobStatus,     # noqa: E402
                            Notification, RunScope, new_id)
from polixor.services import notifications as N                   # noqa: E402

PATHS.ensure()
init_db()
ROOT = Path(__file__).resolve().parents[1]
PORT = 8801
BASE = f"http://127.0.0.1:{PORT}"


def _clear() -> None:
    with session_scope() as s:
        s.query(Notification).delete()


def _render(lang: str) -> dict:
    with i18n.use_lang(lang):
        return N.listing()


# --------------------------------------------------------------------------
def test_every_kind_has_text_in_both_languages():
    sample = {"project": "P", "clips": 3, "review": 1, "clip": "C", "reason": "R",
              "title": "T", "platform": "YouTube", "account": "@a", "when": "10:00"}
    for kind in N.KINDS:
        for part in ("title", "body"):
            key = f"notifications.{kind}.{part}"
            he, en = (i18n.tr(key, lang="he", **sample), i18n.tr(key, lang="en", **sample))
            assert he and en and he != key and en != key, key
            assert "{" not in he and "{" not in en, (key, he, en)
            assert he != en, (key, he)
            assert not re.search(r"[א-ת]", en), (key, en)
            if part == "title":
                assert re.search(r"[א-ת]", he), (key, he)
    for g in ("needs_attention", "new", "earlier"):
        assert i18n.tr(f"notifications.group.{g}", lang="he") != i18n.tr(f"notifications.group.{g}", lang="en")


def test_notify_groups_and_states():
    _clear()
    a = N.notify("clips_ready", params={"project": "A", "clips": 2, "review": 0},
                 link="/projects/a", group_key="job:a")
    b = N.notify("clips_ready", params={"project": "A", "clips": 3, "review": 0},
                 link="/projects/a", group_key="job:a")
    assert a == b                                                   # קובץ – אותה התראה
    N.notify("render_failed", params={"clip": "X"}, group_key="clip:x")
    d = _render("en")
    assert d["unread"] == 2 and d["needs_attention"] == 1
    states = [g["state"] for g in d["groups"]]
    assert states == ["needs_attention", "new"], states
    ready = d["groups"][1]["items"][0]
    assert ready["count"] == 2 and "3 clips" in ready["body"]         # הפרמטרים האחרונים
    # אחרי קריאה – התראה חדשה עם אותו מפתח נפתחת מחדש (לא מתקבצת לישנה)
    assert N.mark_read([a]) == 1
    c = N.notify("clips_ready", params={"project": "A", "clips": 4, "review": 0}, group_key="job:a")
    assert c != a
    d = _render("he")
    assert [g["state"] for g in d["groups"]] == ["needs_attention", "new", "earlier"]
    assert d["groups"][0]["title"] == "דורש טיפול"


def test_read_all_delete_read_and_unknown_kind():
    _clear()
    for i in range(3):
        N.notify("render_complete", params={"clip": f"c{i}"})
    assert N.mark_read() == 3 and _render("en")["unread"] == 0
    N.notify("analysis_complete", params={"project": "p"})
    assert N.delete(read_only=True) == 3
    assert len(_render("en")["items"]) == 1
    try:
        N.notify("made_up_kind")
        raise AssertionError("unknown kind accepted")
    except ValueError:
        pass


def test_errors_are_translated_when_viewed():
    _clear()
    err = PolixorError(message_key="errors.unexpected.message").to_record()
    N.notify("processing_failed", params={"project": "Stream"}, error=err)
    he = _render("he")["items"][0]
    en = _render("en")["items"][0]
    assert he["body"] != en["body"]
    assert i18n.tr("errors.unexpected.message", lang="en") in en["body"]
    assert i18n.tr("errors.unexpected.message", lang="he") in he["body"]


def test_prune_keeps_the_table_bounded():
    _clear()
    real = N.MAX_ROWS
    N.MAX_ROWS = 5
    try:
        for i in range(9):
            N.notify("render_complete", params={"clip": str(i)})
        with session_scope() as s:
            assert s.query(Notification).count() == 5
    finally:
        N.MAX_ROWS = real


def test_failure_to_store_never_breaks_the_caller():
    real = N.session_scope

    def broken():
        raise RuntimeError("db is gone")
    N.session_scope = broken
    try:
        assert N.notify("render_complete", params={"clip": "x"}) is None
        N.job_finished("nope")
        N.job_failed("nope", {})
    finally:
        N.session_scope = real


def _job(scope: str, statuses: list[ClipStatus], title: str = "Stream") -> str:
    jid = new_id()
    with session_scope() as s:
        s.add(Job(id=jid, title=title, status=JobStatus.RUNNING, run_scope=scope,
                  phase="generating", artifacts={}, completed_stages=[]))
        s.flush()
        for st in statuses:
            s.add(Clip(job_id=jid, status=st, title="c"))
    return jid


def test_worker_creates_the_right_notification():
    from polixor.worker import JobManager

    _clear()
    cases = [
        (RunScope.ANALYZE.value, [], "analysis_complete"),
        (RunScope.GENERATE.value, [ClipStatus.READY, ClipStatus.READY], "clips_ready"),
        (RunScope.ALL.value, [ClipStatus.READY, ClipStatus.NEEDS_REVIEW], "clips_ready_review"),
        (RunScope.GENERATE.value, [ClipStatus.FAILED], "no_clips"),
    ]
    events: list = []
    real_emit = BUS.emit
    BUS.emit = lambda t, j="", **d: (events.append((t, d)), real_emit(t, j, **d))[1]
    try:
        for scope, statuses, want in cases:
            jid = _job(scope, statuses)
            JobManager._finish(jid, JobStatus.COMPLETED, "")
            with session_scope() as s:
                n = s.query(Notification).filter(Notification.job_id == jid).one()
                assert n.kind == want, (scope, n.kind)
                assert n.link == f"/projects/{jid}"
        jid = _job(RunScope.ALL.value, [])
        JobManager._fail(jid, PolixorError(message_key="errors.unexpected.message"))
        with session_scope() as s:
            n = s.query(Notification).filter(Notification.job_id == jid).one()
            assert n.kind == "processing_failed" and n.level == "error" and n.error.get("code")
        # ביטול בידי המשתמש – בלי התראה
        jid = _job(RunScope.ALL.value, [])
        JobManager._finish(jid, JobStatus.CANCELLED, "x")
        with session_scope() as s:
            assert s.query(Notification).filter(Notification.job_id == jid).count() == 0
    finally:
        BUS.emit = real_emit
    assert sum(1 for t, _ in events if t == "notification") == 5
    d = _render("he")
    assert d["needs_attention"] == 3                     # כשל + חלקם לבדיקה + אין קליפים


def test_api_roundtrip():
    from fastapi.testclient import TestClient

    from polixor.main import app

    _clear()
    N.notify("account_reconnect", params={"platform": "TikTok", "account": "@me"})
    N.notify("publish_scheduled", params={"title": "Clip", "platform": "YouTube",
                                          "account": "@me", "when": "18:00"})
    c = TestClient(app)
    r = c.get("/api/notifications", headers={"X-Polixor-Lang": "en"}).json()
    assert r["unread"] == 2 and r["needs_attention"] == 1
    assert r["groups"][0]["title"] == "Needs attention"
    r_he = c.get("/api/notifications", headers={"X-Polixor-Lang": "he"}).json()
    assert r_he["groups"][0]["title"] == "דורש טיפול"
    assert c.get("/api/notifications/summary").json() == {"unread": 2, "needs_attention": 1}
    first = r["items"][0]["id"]
    assert c.post("/api/notifications/read", json={"ids": [first]}).json() == {"updated": 1}
    assert c.post("/api/notifications/read", json={}).json() == {"updated": 1}
    assert c.post("/api/notifications/delete?read_only=true", json={}).json() == {"deleted": 2}
    assert c.post("/api/notifications/read", json={"ids": list(range(600))}).status_code == 422


# --------------------------------------------------------------------------
# דפדפן
# --------------------------------------------------------------------------
def _wait_up(timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{BASE}/api/health", timeout=2)
            return
        except Exception:                               # noqa: BLE001
            time.sleep(0.5)
    raise RuntimeError("server did not start")


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def browser_checks() -> list[str]:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def expect(cond: bool, label: str) -> None:
        print(f"  {'✓' if cond else '✗'} {label}")
        if not cond:
            failures.append(label)

    jid = _job(RunScope.GENERATE.value, [ClipStatus.READY])
    _clear()
    N.notify("clips_ready", params={"project": "Stream", "clips": 1, "review": 0},
             link=f"/projects/{jid}", job_id=jid)
    N.notify("publish_failed", params={"title": "Clip", "platform": "YouTube", "account": "@me"},
             error=PolixorError(message_key="errors.unexpected.message").to_record())
    N.notify("render_complete", params={"clip": "Old"})
    N.mark_read([n["id"] for n in _render("en")["items"] if n["kind"] == "render_complete"])

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for lang, groups in (("he", ["דורש טיפול", "חדש", "קודם"]),
                             ("en", ["Needs attention", "New", "Earlier"])):
            ctx = browser.new_context(locale="he-IL" if lang == "he" else "en-US",
                                      viewport={"width": 1280, "height": 900})
            ctx.add_init_script(f"sessionStorage.setItem('polixor.langOverride','{lang}')")
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(BASE + "/", wait_until="networkidle")
            badge = page.locator("[data-testid=notification-badge]")
            expect(badge.inner_text() == "2", f"[{lang}] bell badge shows 2 unread")
            page.click("[data-testid=notification-bell]")
            panel = page.locator("[data-testid=notification-panel]")
            panel.wait_for()
            heads = panel.locator("section h3").evaluate_all("els => els.map(e => e.textContent)")
            expect([h.strip() for h in heads] == groups, f"[{lang}] grouped: {heads}")
            text = panel.inner_text()
            has_he = bool(re.search(r"[א-ת]", text.replace("Stream", "")))
            expect(has_he == (lang == "he") or lang == "he", f"[{lang}] panel text in {lang}")
            expect(not errors, f"[{lang}] no JavaScript errors")
            ctx.close()

        # לחיצה: מסמנת כנקרא ומנווטת לפרויקט
        ctx = browser.new_context(locale="en-US", viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.goto(BASE + "/", wait_until="networkidle")
        page.click("[data-testid=notification-bell]")
        page.locator("[data-testid=notification-item]", has_text="Clips ready").click()
        page.wait_for_url(f"**/projects/{jid}")
        expect(page.url.endswith(f"/projects/{jid}"), "clicking a notification opens its project")
        page.wait_for_function("document.querySelector('[data-testid=notification-badge]')?.innerText === '1'")
        expect(page.locator("[data-testid=notification-badge]").inner_text() == "1",
               "clicked notification is marked read")
        # עדכון חי: שינוי בשרת (דרך ה-API) מגיע בלי רענון
        _post("/api/notifications/read", {})
        page.wait_for_function("!document.querySelector('[data-testid=notification-badge]')", timeout=10000)
        expect(page.locator("[data-testid=notification-badge]").count() == 0,
               "badge updates live over the WebSocket")
        ctx.close()

        # מובייל: גיליון ברוחב מלא, בלי גלילה אופקית, בעברית
        ctx = browser.new_context(locale="he-IL", viewport={"width": 390, "height": 844},
                                  timezone_id="Asia/Jerusalem")
        page = ctx.new_page()
        page.goto(BASE + "/", wait_until="networkidle")
        page.click("[data-testid=notification-bell]")
        panel = page.locator("[data-testid=notification-panel]")
        panel.wait_for()
        box = panel.bounding_box()
        wide = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
        expect(box is not None and box["width"] >= 385 and not wide,
               f"mobile: full-width sheet, no horizontal scroll ({box and round(box['width'])}px)")
        page.keyboard.press("Escape")
        expect(panel.count() == 0, "Escape closes the panel")
        ctx.close()
        browser.close()
    return failures


def test_browser_bell():
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        print("  (skipped: Playwright is not installed – the browser checks need it)")
        return
    env = {**os.environ, "POLIXOR_DATA_DIR": DATA}
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
    print(f"\n{passed}/{len(fns)} בדיקות התראות עברו")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
