"""
סיור מלא באתר האמיתי, על תוכן אמיתי.

הסקריפט מקים נתונים דרך הפייפליין (לא Fixtures של ממשק), מפעיל את
השרת, ועובר מסך-מסך עם דפדפן אמיתי: מצלם, בודק שאין שגיאות
JavaScript, ובודק גלילה אופקית ברוחב טלפון.

המטרה אינה „להראות שזה יפה" אלא להראות **מה באמת עובד ומה חסר**.
לכן הסקריפט גם מדווח על מסכים שבהם פונקציונליות קיימת ב-API אבל
אין לה ממשק.

הרצה:  python3 tests/site_walkthrough.py [out_dir]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATA = os.environ.get("POLIXOR_DATA_DIR") or tempfile.mkdtemp(prefix="pxsite_")
os.environ["POLIXOR_DATA_DIR"] = DATA
os.environ["POLIXOR_FIXTURE_TRANSCRIPT"] = \
    "/home/claude/testdata/polixor_test_stream.transcript.json"

from polixor.config import PATHS, SETTINGS, AppSettings            # noqa: E402

PATHS.ensure()

from polixor.db import init_db, session_scope                      # noqa: E402
from polixor.models import (                                       # noqa: E402
    Clip, ClipStatus, Job, JobStatus, new_id,
)
from polixor.pipeline import run_job                               # noqa: E402

from playwright.sync_api import sync_playwright                    # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1
           else "/tmp/claude-0/-home-claude/"
                "dd040985-06b9-5d82-b9ac-3a4e3dfe5e3b/scratchpad/site")
OUT.mkdir(parents=True, exist_ok=True)
PORT = 8797
BASE = f"http://127.0.0.1:{PORT}"
VIDEO = Path("/home/claude/testdata/polixor_test_stream.mp4")
ROOT = Path(__file__).resolve().parents[1]

PASS, FAIL, WARN = "\033[92m✓\033[0m", "\033[91m✗\033[0m", "\033[93m!\033[0m"
results: list[tuple[str, str]] = []


def note(kind: str, label: str, detail: str = "") -> None:
    results.append((kind, label))
    mark = {"ok": PASS, "bad": FAIL, "gap": WARN}[kind]
    print(f"  {mark} {label}" + (f"  — {detail}" if detail else ""))


# ==========================================================================
def make_music_bed() -> Path:
    dst = Path(DATA) / "music_bed.wav"
    if dst.exists():
        return dst
    sr, dur = 48000, 120.0
    t = np.arange(int(sr * dur)) / sr
    x = (0.5 * np.sin(2 * np.pi * 440 * t)
         + 0.5 * np.sin(2 * np.pi * 660 * t)) * 0.28
    with wave.open(str(dst), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    return dst


def seed() -> dict:
    """מריץ משימה אמיתית דרך הפייפליין ומחזיר מזהים לסיור."""
    init_db()
    music = make_music_bed()
    # ספק התמונות המקומי — אין מפתח OpenAI בסביבה, והוא מסומן
    # במפורש כ„לא AI" גם בממשק.
    SETTINGS.update({"image_provider": "placeholder",
                     "music_enabled": True, "music_path": str(music),
                     "music_profile": "balanced"})

    base = AppSettings().to_dict()
    base.update({
        "transcript_provider": "fixture", "ai_mode": "heuristic",
        "visual_sample_fps": 2.0, "video_quality": "low",
        "subtitles_enabled": True,
        "long_enabled": True, "long_count": 1,
        "long_min_seconds": 20, "long_max_seconds": 60,
        "short_enabled": True, "short_count": 2,
        "short_min_seconds": 15, "short_max_seconds": 30,
        "image_provider": "placeholder",
        "music_enabled": True, "music_path": str(music),
        "director_enabled": True, "mastering_enabled": True,
    })
    settings = AppSettings.from_dict(base)

    job_id = new_id()
    with session_scope() as s:
        s.add(Job(id=job_id, title="סיור באתר", input_url="",
                  status=JobStatus.QUEUED,
                  settings_snapshot=settings.to_dict(),
                  artifacts={"source_path": str(VIDEO)},
                  completed_stages=[]))
    t0 = time.time()
    run_job(job_id, threading.Event())
    elapsed = time.time() - t0

    with session_scope() as s:
        clips = s.query(Clip).filter(Clip.job_id == job_id).all()
        rows = [(c.id, c.kind.value, c.status.value, c.title,
                 bool((c.render_params or {}).get("director")),
                 bool((c.render_params or {}).get("qa")),
                 bool((c.render_params or {}).get("music")))
                for c in clips]
    return {"job_id": job_id, "clips": rows, "elapsed": elapsed}


def make_image() -> str:
    """יוצר תמונה דרך ה-API כדי שגלריית התמונות לא תהיה ריקה."""
    import urllib.request

    body = json.dumps({
        "prompt": "כביש ריק בלילה, אורות רחוב, אווירה קולנועית",
        "aspect": "9:16",
    }).encode()
    req = urllib.request.Request(f"{BASE}/api/images", method="POST",
                                 data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        img = json.loads(urllib.request.urlopen(req, timeout=30).read())
    except Exception as exc:                            # noqa: BLE001
        note("bad", f"יצירת תמונה נכשלה: {exc}")
        return ""
    for _ in range(40):
        time.sleep(0.5)
        cur = json.loads(urllib.request.urlopen(
            f"{BASE}/api/images/{img['id']}", timeout=10).read())
        if cur["status"] in ("ready", "failed"):
            return cur["id"] if cur["status"] == "ready" else ""
    return ""


def wait_up(timeout: float = 60.0) -> None:
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{BASE}/api/health", timeout=2)
            return
        except Exception:                               # noqa: BLE001
            time.sleep(0.7)
    raise RuntimeError("השרת לא עלה")


# ==========================================================================
SCREENS = [
    ("home", "/", "מסך הבית — פרויקט חדש"),
    ("jobs", "/jobs", "רשימת המשימות"),
    ("clips", "/clips", "גלריית הקליפים"),
    ("images", "/images", "AI Images"),
    ("settings", "/settings", "הגדרות"),
]


def walk(state: dict) -> None:
    errors: list[str] = []
    job_id = state["job_id"]
    clip_id = state["clips"][0][0] if state["clips"] else ""

    screens = list(SCREENS)
    if job_id:
        screens.insert(2, ("job", f"/jobs/{job_id}", "פרטי משימה"))
    if clip_id:
        screens.append(("clip_edit", f"/clips/{clip_id}/edit",
                        "עריכת קליפ"))

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000},
                                  locale="he-IL",
                                  device_scale_factor=2)
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(f"{page.url}: {e}"))
        page.on("console", lambda m: errors.append(f"{page.url}: {m.text}")
                if m.type == "error" else None)

        for key, path, label in screens:
            print(f"\n— {label} —")
            try:
                page.goto(f"{BASE}{path}", wait_until="networkidle",
                          timeout=45000)
            except Exception as exc:                    # noqa: BLE001
                note("bad", f"{label}: הדף לא נטען ({exc})")
                continue
            page.wait_for_timeout(1400)

            body = page.inner_text("body")
            if len(body.strip()) < 40:
                note("bad", f"{label}: הדף נטען ריק")
            else:
                note("ok", f"{label}: נטען", f"{len(body)} תווים")

            shot = OUT / f"{key}.png"
            page.screenshot(path=str(shot), full_page=True)
            note("ok", f"{label}: צולם", shot.name)

            _audit(page, key, body)

            # רוחב טלפון
            page.set_viewport_size({"width": 400, "height": 900})
            page.wait_for_timeout(700)
            overflow = page.evaluate(
                "() => document.documentElement.scrollWidth - "
                "document.documentElement.clientWidth")
            if overflow > 1:
                note("bad", f"{label}: גלילה אופקית ברוחב 400px",
                     f"{overflow}px")
            else:
                note("ok", f"{label}: אין גלילה אופקית ב-400px")
            page.screenshot(path=str(OUT / f"{key}_mobile.png"),
                            full_page=True)
            page.set_viewport_size({"width": 1440, "height": 1000})

        browser.close()

    real = [e for e in errors if "favicon" not in e.lower()]
    if real:
        note("bad", "שגיאות JavaScript", " | ".join(real[:3]))
    else:
        note("ok", "אין שגיאות JavaScript באף מסך")


TAB_NAMES = [
    ("analysis", "ניתוח ותמלול"), ("editing", "סגנון עריכה"),
    ("director", "במאי AI ואודיו"), ("clips", "קליפים וייצוא"),
    ("subtitles", "כתוביות"), ("ai", "מנוע AI"), ("images", "AI Images"),
    ("live", "שידור חי"), ("system", "מערכת ואחסון"),
]


def _check_setting_persists(page) -> None:
    """
    משנה הגדרה דרך הממשק, שומר, טוען מחדש, ובודק שהיא שם.

    בלי זה אי אפשר לדעת אם הפקד באמת מחובר או רק נראה טוב.
    """
    import urllib.request

    before = json.loads(urllib.request.urlopen(
        f"{BASE}/api/settings", timeout=10).read())["values"]["director_style"]

    btn = page.locator("main button, div:not(aside) > button").filter(
        has_text="במאי AI ואודיו").first
    btn.click()
    page.wait_for_timeout(500)
    target = "סיפור קולנועי"
    label = page.locator("label").filter(has_text=target).first
    if not label.count():
        note("bad", "הגדרות: לא נמצאה אפשרות לבדיקת שמירה")
        return
    label.click()
    page.wait_for_timeout(300)

    save = page.locator("button").filter(has_text="שמור").first
    if not save.count():
        note("bad", "הגדרות: לא נמצא כפתור שמירה")
        return
    save.click()
    page.wait_for_timeout(1500)

    after = json.loads(urllib.request.urlopen(
        f"{BASE}/api/settings", timeout=10).read())["values"]["director_style"]
    if after == "cinematic_story" and after != before:
        note("ok", "הגדרות: שינוי נשמר בפועל",
             f"director_style: {before or 'ריק'} → {after}")
    else:
        note("bad", "הגדרות: השינוי לא נשמר",
             f"לפני {before!r} אחרי {after!r}")


def _slug(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "_",
                  text.replace("במאי AI ואודיו", "director")
                      .replace("סגנון עריכה", "editing").lower()).strip("_")


def _audit(page, key: str, body: str) -> None:
    """בודק שמה שאמור להיות במסך באמת שם — ומדווח על פערים."""
    if key == "clip_edit":
        for label, text in [
            ("בדיקת האיכות", "בדיקת איכות אחרי הרינדור"),
            ("תכנית העריכה", "תכנית העריכה של ה-AI"),
            ("סיכום האודיו", "מה נעשה לאודיו"),
            ("עריכת כתוביות", "כתוביות"),
            ("פריסה וייצוא", "פריסה וייצוא"),
        ]:
            if text in body:
                note("ok", f"עריכת קליפ: {label} מוצג")
            else:
                note("gap", f"עריכת קליפ: {label} חסר")
        # שליטה בתכנית העריכה — §32.
        # ספירת checkbox כללית נותנת „עבר" מזויף: במסך יש מקרא של
        # רצועת הביטים. הבדיקה חייבת לחפש פקד שמשנה **החלטה**.
        page.locator("button").filter(has_text="חיתוכים").first.click(
        ) if page.locator("button").filter(has_text="חיתוכים").count() else None
        page.wait_for_timeout(500)
        controls = page.locator(
            "[data-decision-toggle], button[aria-pressed]").count()
        note("ok" if controls else "gap",
             "עריכת קליפ: שליטה בהחלטות הבמאי (§32)",
             f"{controls} פקדים" if controls
             else "תצוגה בלבד — אי אפשר לבטל החלטה בודדת מהממשק")

    if key == "settings":
        # הביקורת חייבת ללחוץ על הלשוניות. בדיקה של לשונית ברירת
        # המחדל בלבד מדווחת „חסר" על כל מה שנמצא בלשונית אחרת.
        found: dict[str, bool] = {}
        for tab_label, needles in [
            ("סגנון עריכה", {"מוזיקת רקע": "מוזיקת רקע"}),
            ("במאי AI ואודיו", {"במאי ה-AI": "תכנית עריכה לפני הביצוע",
                                "סגנון קצב": "סגנון קצב",
                                "פריסט כתוביות": "פריסט כתוביות",
                                "מאסטרינג אודיו": "יעד עוצמה"}),
        ]:
            btn = page.locator("main button, div:not(aside) > button").filter(
                has_text=tab_label).first
            if not btn.count():
                note("gap", f"הגדרות: לשונית „{tab_label}” לא נמצאה")
                continue
            btn.click()
            page.wait_for_timeout(700)
            text = page.inner_text("body")
            for label, needle in needles.items():
                found[label] = needle in text
            page.screenshot(
                path=str(OUT / f"settings_{_slug(tab_label)}.png"),
                full_page=True)

        for label, ok in found.items():
            note("ok" if ok else "gap", f"הגדרות: {label}",
                 "" if ok else "קיים ב-API, אין ממשק")

        # כל לשונית חייבת להיות נגישה. תשע לשוניות שנחתכות בגלילה
        # אופקית בלי רמז ויזואלי הן לשוניות שאיש לא ימצא.
        unreachable = []
        for _, name in TAB_NAMES:
            btn = page.locator("main button, div:not(aside) > button").filter(
                has_text=name).first
            if not btn.count():
                unreachable.append(name)
                continue
            try:
                btn.click(timeout=3000)
                page.wait_for_timeout(250)
            except Exception:                           # noqa: BLE001
                unreachable.append(name)
        note("bad" if unreachable else "ok",
             "הגדרות: כל הלשוניות נגישות",
             "לא נגישות: " + ", ".join(unreachable) if unreachable
             else f"{len(TAB_NAMES)} לשוניות")

        # ההגדרה נשמרת בפועל — לא פקד דקורטיבי
        _check_setting_persists(page)


# ==========================================================================
def main() -> int:
    print(f"\n{'=' * 72}\n▶ סיור באתר Polixor\n{'=' * 72}")
    print(f"\nנתונים: {DATA}\nצילומים: {OUT}\n")

    print("— מריץ משימה אמיתית דרך הפייפליין —")
    state = seed()
    note("ok", f"הפייפליין הסתיים ({state['elapsed']:.0f} שניות)")
    for cid, kind, status, title, has_dir, has_qa, has_music in state["clips"]:
        flags = []
        if has_dir:
            flags.append("תכנית במאי")
        if has_qa:
            flags.append("בדיקת איכות")
        if has_music:
            flags.append("מוזיקה")
        note("ok" if status in ("ready", "needs_review") else "bad",
             f"קליפ {kind}: {title[:34]}",
             f"{status} · " + " · ".join(flags))

    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "polixor.main:app",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=str(ROOT), env={**os.environ, "POLIXOR_DATA_DIR": DATA},
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        wait_up()
        note("ok", "השרת עלה", BASE)
        if make_image():
            note("ok", "נוצרה תמונה בגלריה (ספק מקומי, מסומן „לא AI”)")
        walk(state)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    ok = sum(1 for k, _ in results if k == "ok")
    bad = [l for k, l in results if k == "bad"]
    gaps = [l for k, l in results if k == "gap"]
    print(f"\n{'=' * 72}")
    print(f"עברו: {ok} · תקלות: {len(bad)} · פערי ממשק: {len(gaps)}")
    if bad:
        print("\nתקלות:")
        for l in bad:
            print(f"  {FAIL} {l}")
    if gaps:
        print("\nפערי ממשק (קיים ב-API, אין מסך):")
        for l in gaps:
            print(f"  {WARN} {l}")
    print("=" * 72)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
