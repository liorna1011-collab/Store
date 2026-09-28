"""
בדיקות לזיהוי פריסה (facecam / מצלמה / מסך) ולקומפוזיציית תגובה.

רצות על וידאו התגובה הסינתטי (tests/make_reaction_video.py) שבו הפריסה
ידועה מראש (reaction_stream.truth.json): מצלמה בפינה ימנית-תחתונה,
מצלמה מלאה, מסך בלבד, מצלמה בפינה שמאלית-עליונה, וחצי מסך מצלמה.

נבדק:
  * הסוג הנכון לאורך הזמן, וגבולות מדויקים עד שנייה.
  * מלבן ה-facecam מול האמת (IoU).
  * בתכנון 9:16 הפנים נשארות שלמות בתוך פאנל המצלמה.
  * ברינדור אמיתי של קליפ תגובה: שני פאנלים, והפנים מזוהות בפאנל המצלמה.
  * משך הניתוח סביר.

הרצה:  python3 tests/test_layout.py [path/to/reaction_stream.mp4]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxlayout_"))
DATA = Path(os.environ["POLIXOR_DATA_DIR"])

import numpy as np                                                  # noqa: E402
from PIL import Image                                               # noqa: E402

from polixor.config import PATHS, AppSettings                       # noqa: E402

PATHS.ensure()

from polixor.services import composition, reframe, render           # noqa: E402
from polixor.services import layout_detect as ld                    # noqa: E402
from polixor.util.ffmpeg import ffmpeg_bin                          # noqa: E402


def _video() -> Path:
    arg = next((a for a in sys.argv[1:] if a.endswith(".mp4")), None)
    for cand in ([Path(arg)] if arg else []) + [
            Path("/home/claude/testdata/reaction_stream.mp4"),
            DATA / "testdata" / "reaction_stream.mp4"]:
        if cand.exists() and cand.with_suffix(".truth.json").exists():
            return cand
    out = DATA / "testdata"
    subprocess.run([sys.executable, str(HERE / "make_reaction_video.py"), str(out)],
                   check=True, capture_output=True)
    return out / "reaction_stream.mp4"


VIDEO = _video()
TRUTH = json.loads(VIDEO.with_suffix(".truth.json").read_text("utf-8"))
PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
results: list[tuple[bool, str]] = []


def check(cond: bool, label: str, detail: str = "") -> bool:
    results.append((bool(cond), label))
    print(f"  {PASS if cond else FAIL} {label}" + (f"  — {detail}" if detail else ""))
    return bool(cond)


def truth_at(t: float) -> dict:
    return next(x for x in TRUTH["layouts"] if x["start"] <= t < x["end"])


def main() -> int:
    if not ld.detectors_available():
        print("OpenCV Haar cascades לא זמינים – אין אפשרות לבדוק זיהוי פריסה.")
        return 1
    w, h, dur = TRUTH["width"], TRUTH["height"], TRUTH["duration"]

    print("\n▶ זיהוי")
    t0 = time.time()
    tl = ld.detect_layouts(VIDEO, duration=dur, src_w=w, src_h=h,
                           cancel_event=threading.Event())
    elapsed = time.time() - t0
    check(tl.analyzed and tl.segments, f"ניתוח הושלם ({elapsed:.1f}s)")
    check(elapsed < 60, "משך הניתוח סביר (< 60 שניות ל-80 שניות 720p)", f"{elapsed:.1f}s")

    ts = np.arange(0.25, dur, 0.5)
    correct = sum(1 for t in ts if (tl.at(t) and tl.at(t).kind == truth_at(t)["kind"]))
    check(correct / len(ts) >= 0.95, "הסוג הנכון לאורך הזמן (≥95%)",
          f"{100 * correct / len(ts):.1f}%")

    got_bounds = [s.end for s in tl.segments[:-1]]
    for tb in [x["end"] for x in TRUTH["layouts"][:-1]]:
        near = min((abs(b - tb) for b in got_bounds), default=99)
        check(near <= 1.0, f"גבול ב-{tb:.0f}s נמצא", f"סטייה {near:.2f}s")

    for x in TRUTH["layouts"]:
        if not x["facecam"]:
            continue
        mid = (x["start"] + x["end"]) / 2
        seg = tl.at(mid)
        truth = ld.Box(**x["facecam"])
        iou = truth.iou(seg.facecam) if seg and seg.facecam else 0.0
        check(iou >= 0.8, f"חלון מצלמה {x['start']:.0f}–{x['end']:.0f}s (IoU ≥ 0.8)",
              f"IoU {iou:.2f}")

    print("\n▶ תכנון 9:16")
    for x in TRUTH["layouts"]:
        seg = tl.at((x["start"] + x["end"]) / 2)
        if x["kind"] != "reaction" or seg is None:
            continue
        piece = composition.plan_reaction(seg, out_w=1080, out_h=1920, src_w=w, src_h=h)
        ok = piece is not None and piece.cam is not None and seg.face is not None
        if ok:
            f, cam = seg.face, piece.cam
            ok = (cam.x <= f.x + 1e-3 and cam.y <= f.y + 1e-3 and
                  cam.x1 >= f.x1 - 1e-3 and cam.y1 >= f.y1 - 1e-3)
        check(ok, f"הפנים שלמות בפאנל המצלמה ({x['start']:.0f}–{x['end']:.0f}s)")

    print("\n▶ רינדור קליפ תגובה")
    s = AppSettings.from_dict({**AppSettings().to_dict(), "video_quality": "low",
                               "short_layout": "auto", "subtitles_enabled": False})
    start, end = 4.0, 12.0                             # מצלמה בפינה ימנית-תחתונה
    plan = reframe.plan_reframe(None, clip_start=start, clip_end=end, layout="auto",
                                layouts=tl, target_aspect=9 / 16, out_size=(720, 1280),
                                src_size=(w, h), segments=[(start, end)])
    check(plan.layout in ("reaction", "auto") and any(
        p.kind == "reaction" for p in getattr(plan, "pieces", []) or []),
        "התכנית היא פריסת תגובה", plan.note)
    out = DATA / "reaction_clip.mp4"
    req = render.build_request(
        source=VIDEO, output=out, segments=[(start, end)], vertical=True, settings=s,
        reframe=plan, subtitle_path=None,
        source_info={"width": w, "height": h, "duration": dur, "has_audio": True},
        transitions=False, work_dir=DATA, resolution="720x1280")
    res = render.render_clip(req, cancel_event=threading.Event())
    check(res.width == 720 and res.height == 1280, "פלט 720×1280", f"{res.width}×{res.height}")
    png = DATA / "reaction_frame.png"
    subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-ss", "4",
                    "-i", str(out), "-frames:v", "1", str(png)], check=True)
    gray = np.asarray(Image.open(png).convert("L"))
    faces = ld.detect_faces(gray)
    check(bool(faces), "פנים מזוהות בפריים של הקליפ", str(len(faces)))
    # פאנל המצלמה והתוכן: הפנים בפאנל אחד בלבד, לא חתוכות בקצה
    if faces:
        f = max(faces, key=lambda b: b.area)
        check(f.y > 0.02 and f.y1 < 0.98 and f.x > 0.02 and f.x1 < 0.98,
              "הפנים לא נחתכות בשולי הפריים", f"{f}")

    ok = sum(1 for r in results if r[0])
    print(f"\n{ok}/{len(results)} בדיקות פריסה עברו")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
