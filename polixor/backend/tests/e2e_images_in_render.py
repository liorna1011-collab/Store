"""
בדיקת קצה-אל-קצה: תמונה שנוצרה נכנסת בפועל לווידאו המיוצא.

המסלול הנבדק הוא המסלול האמיתי דרך ה-API, בדיוק כמו שהממשק עושה:

    יצירת תמונה → שיבוץ בקליפ → ייצוא מחדש → אימות הפריימים

האימות אינו מסתמך על הודעת הצלחה: הוא מחלץ פריימים מהווידאו
המוגמר ומשווה אותם לקובץ התמונה. אם התמונה לא באמת שם, הבדיקה
נכשלת.

דורש שרת פעיל. הרצה:
    python3 tests/e2e_images_in_render.py [base_url]
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8761"
WORK = Path("/tmp/polixor_img_e2e")
WORK.mkdir(parents=True, exist_ok=True)

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
results: list[tuple[bool, str]] = []

# מתחת לסף הזה הפריים זהה לתמונה שהוכנסה.
# פריימים של התמונה נמדדים סביב 1.5, ופריימים של וידאו מעל 30,
# ולכן 12 נותן מרווח בטוח לשני הכיוונים.
SAME_IMAGE = 12.0


def check(cond: bool, label: str, detail: str = "") -> bool:
    results.append((bool(cond), label))
    print(f"  {PASS if cond else FAIL} {label}" + (f"  — {detail}" if detail else ""))
    return bool(cond)


def req(path: str, method: str = "GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=600) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            payload = json.loads(raw)
            return e.code, payload.get("detail", payload)
        except Exception:
            return e.code, raw.decode("utf-8", "replace")[:300]


def wait_image(image_id: str, timeout: float = 180.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, img = req(f"/api/images/{image_id}")
        if img["status"] in ("ready", "failed", "cancelled"):
            return img
        time.sleep(0.5)
    return {"status": "timeout", "error": "פג הזמן בהמתנה ליצירת התמונה"}


def download(path: str, dest: Path) -> Path:
    with urllib.request.urlopen(BASE + path, timeout=300) as r:
        dest.write_bytes(r.read())
    return dest


def frame_at(video: Path, t: float) -> Path:
    out = WORK / f"{video.stem}_{t:.1f}.png"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                    str(out)], check=True)
    return out


def diff_to(frame: Path, image: Path) -> float:
    """הפרש ממוצע בין פריים לתמונה. נמוך => זה אותו תוכן."""
    import numpy as np
    from PIL import Image

    a = Image.open(frame).convert("RGB")
    b = Image.open(image).convert("RGB").resize(a.size, Image.LANCZOS)
    return float(np.abs(np.asarray(a, dtype=np.float32)
                        - np.asarray(b, dtype=np.float32)).mean())


def duration_of(video: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", str(video)],
                       capture_output=True, text=True)
    try:
        return float((r.stdout or "0").strip())
    except ValueError:
        return 0.0


def main() -> None:
    print(f"\n{'=' * 72}\n▶ תמונה שנוצרה → קליפ מיוצא\n{'=' * 72}")

    status, clips = req("/api/clips")
    if status != 200 or not clips:
        print("אין קליפים בשרת. הרץ תחילה משימה כדי שיהיה על מה לבדוק.")
        sys.exit(2)

    clip = next((c for c in clips if c.get("status") == "ready"
                 and c.get("duration", 0) > 6), clips[0])
    print(f"קליפ: {clip['title'][:50]} · {clip['duration']:.1f}s "
          f"· {clip['aspect']}")

    # מצב התחלה נקי: מסירים שיבוצים קודמים ומייצאים מחדש, כדי
    # שהמדידות יהיו מול קליפ ללא תמונות.
    _, old = req(f"/api/clips/{clip['id']}/images")
    if isinstance(old, list) and old:
        for pl in old:
            req(f"/api/clips/{clip['id']}/images/{pl['id']}", "DELETE")
        print(f"    (נוקו {len(old)} שיבוצים מריצה קודמת)")
    # תמיד מייצאים מחדש: גם אם אין שיבוצים, הקובץ על הדיסק עלול
    # להיות תוצר של ריצה קודמת שכללה תמונות.
    req(f"/api/clips/{clip['id']}/reexport", "POST", {})

    before = download(f"/api/clips/{clip['id']}/file", WORK / "before.mp4")
    dur_before = duration_of(before)
    check(dur_before > 1.0, "הקליפ המקורי ניתן להורדה ולקריאה",
          f"{dur_before:.2f}s")

    # ---- 1. יצירת תמונה דרך ה-API ----
    print("\n— יצירת תמונה —")
    aspect = "9:16" if clip["aspect"] == "9:16" else "16:9"
    st, img = req("/api/images", "POST", {
        "prompt": "A lonely empty road at night, single street lamp, "
                  "cinematic, moody",
        "aspect": aspect, "job_id": clip["job_id"],
    })
    check(st == 202, "בקשת יצירה התקבלה", f"HTTP {st}")
    img = wait_image(img["id"])
    if not check(img["status"] == "ready", "התמונה נוצרה",
                 f"{img['status']} {img.get('error', '')}"):
        _summary()
        return

    check(img["width"] > 0 and img["height"] > 0, "לתמונה יש מידות",
          f"{img['width']}×{img['height']}")
    if not img["is_ai"]:
        print(f"    ℹ ספק שאינו AI: {img['note']}")

    image_file = download(f"/api/images/{img['id']}/file", WORK / "img.png")

    # ---- 2. שיבוץ: פתיח + הכנסה באמצע ----
    print("\n— שיבוץ בקליפ —")
    mid = round(dur_before / 2, 1)
    st1, p1 = req(f"/api/clips/{clip['id']}/images", "POST", {
        "image_id": img["id"], "role": "intro", "duration": 2.0})
    check(st1 == 201, "פתיח שובץ", f"HTTP {st1}")
    st2, p2 = req(f"/api/clips/{clip['id']}/images", "POST", {
        "image_id": img["id"], "role": "insert",
        "at_time": mid, "duration": 3.0})
    check(st2 == 201, f"הכנסה שובצה ב-{mid}s", f"HTTP {st2}")

    st3, plist = req(f"/api/clips/{clip['id']}/images")
    check(st3 == 200 and len(plist) == 2, "השיבוצים נשמרו ונקראים חזרה",
          f"{len(plist) if isinstance(plist, list) else '?'} שיבוצים")
    check(all(p["extends_timeline"] for p in plist),
          "שני השיבוצים מסומנים כמאריכים את ציר הזמן")

    # ---- 3. ייצוא מחדש ----
    print("\n— ייצוא מחדש —")
    t0 = time.time()
    st4, out = req(f"/api/clips/{clip['id']}/reexport", "POST", {})
    if not check(st4 == 200, "הייצוא הצליח",
                 f"HTTP {st4} {str(out)[:160]}"):
        _summary()
        return
    print(f"    ({time.time() - t0:.1f} שניות)")

    params = out.get("render_params") or {}
    check(not params.get("images_error"), "לא נרשמה שגיאת תמונות",
          params.get("images_error", ""))
    check(bool(params.get("images_note")), "נרשם סיכום שילוב התמונות",
          params.get("images_note", ""))

    after = download(f"/api/clips/{clip['id']}/file", WORK / "after.mp4")
    dur_after = duration_of(after)
    expected = dur_before + 5.0
    check(abs(dur_after - expected) < 1.2,
          "אורך הקליפ גדל בדיוק במשך התמונות",
          f"{dur_before:.2f}s → {dur_after:.2f}s (צפוי {expected:.2f}s)")

    # ---- 4. אימות הפריימים: התמונה באמת שם ----
    print("\n— אימות פריימים —")
    # פתיח 0–2 ; וידאו 2–(2+mid) ; הכנסה (2+mid)–(5+mid) ; וידאו עד הסוף
    insert_at = 2.0 + mid
    probes = [
        (1.0, True, "פתיח"),
        (2.0 + mid / 2, False, "וידאו לפני ההכנסה"),
        (insert_at + 1.5, True, "הכנסה באמצע"),
        (min(dur_after - 0.6, insert_at + 4.0), False, "וידאו אחרי ההכנסה"),
    ]
    for t, want_image, label in probes:
        if t <= 0 or t >= dur_after:
            continue
        d = diff_to(frame_at(after, t), image_file)
        is_image = d < SAME_IMAGE
        check(is_image == want_image,
              f"t={t:.1f}s — {label}: "
              f"{'תמונה' if want_image else 'וידאו'}",
              f"diff={d:.1f}")

    # ---- 4ב. הכתוביות זזו יחד עם הווידאו ----
    print("\n— סנכרון כתוביות —")
    st_c, cues = req(f"/api/clips/{clip['id']}/cues")
    if st_c == 200 and cues:
        check(all(c["end"] > c["start"] for c in cues),
              "כל תזמוני הכתוביות תקינים", f"{len(cues)} שורות")
        check(max(c["end"] for c in cues) <= dur_after + 1.0,
              "אין כתובית שחורגת מגבול הקליפ החדש",
              f"אחרונה {max(c['end'] for c in cues):.1f}s מתוך {dur_after:.1f}s")
        # שורה שהייתה אחרי נקודת ההכנסה חייבת לזוז קדימה
        late = [c for c in cues if c["start"] > insert_at]
        check(bool(late) or dur_before < mid + 2,
              "יש כתוביות אחרי נקודת ההכנסה שהוזזו",
              f"{len(late)} שורות")
    else:
        print("    (אין כתוביות בקליפ הזה)")

    # ---- 4ג. ייצוא חוזר אינו מצטבר ----
    print("\n— ייצוא חוזר עם אותם שיבוצים —")
    st_r, _ = req(f"/api/clips/{clip['id']}/reexport", "POST", {})
    check(st_r == 200, "ייצוא חוזר הצליח", f"HTTP {st_r}")
    again = download(f"/api/clips/{clip['id']}/file", WORK / "again.mp4")
    dur_again = duration_of(again)
    check(abs(dur_again - dur_after) < 0.4,
          "האורך לא גדל שוב – השיבוצים אינם מצטברים",
          f"{dur_after:.2f}s → {dur_again:.2f}s")
    st_c2, cues2 = req(f"/api/clips/{clip['id']}/cues")
    if st_c2 == 200 and cues2:
        check(max(c["end"] for c in cues2) <= dur_again + 1.0,
              "הכתוביות לא זחלו בייצוא החוזר",
              f"אחרונה {max(c['end'] for c in cues2):.1f}s "
              f"מתוך {dur_again:.1f}s")

    # ---- 5. בידוד: כשל בתמונות לא שובר את העורך ----
    print("\n— בידוד הפיצ'ר —")
    st5, _ = req(f"/api/clips/{clip['id']}/images/{plist[0]['id']}", "DELETE")
    check(st5 == 200, "הסרת שיבוץ עובדת")
    st6, out2 = req(f"/api/clips/{clip['id']}/reexport", "POST", {})
    check(st6 == 200, "ייצוא מחדש עובד גם אחרי הסרת שיבוץ", f"HTTP {st6}")
    after2 = download(f"/api/clips/{clip['id']}/file", WORK / "after2.mp4")
    d2 = duration_of(after2)
    check(abs(d2 - (dur_before + 3.0)) < 1.2,
          "האורך תואם לשיבוץ שנותר",
          f"{d2:.2f}s (צפוי {dur_before + 3.0:.2f}s)")

    # ניקוי: מסירים את השיבוץ האחרון ומחזירים את הקליפ למצבו
    req(f"/api/clips/{clip['id']}/images/{plist[1]['id']}", "DELETE")

    _summary()


def _summary() -> None:
    passed = sum(1 for ok, _ in results if ok)
    total = len(results)
    print(f"\n{'=' * 72}\nסיכום: {passed}/{total} בדיקות עברו\n{'=' * 72}")
    for ok, label in results:
        if not ok:
            print(f"  {FAIL} {label}")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
