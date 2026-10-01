"""
שימוש חוזר בניתוח: עריכת כתוביות, עיצוב, יחס מסך וגבולות קליפ – ויצירה
מחדש – לא מתמללים ולא מנתחים שוב. בודק על הפייפליין האמיתי (API + worker).

נשמרים ונעשה בהם שימוש חוזר: האודיו שחולץ, התמלול וזמני המילים, ציר הזמן
(אותות), המועמדים והציונים, תוצאות ההגהה, והניתוח החזותי (גם בחלונות).

הרצה:  python3 tests/test_caching.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxcache_"))
VIDEO = Path("/home/claude/testdata/polixor_test_stream.mp4")
FIXTURE = VIDEO.with_suffix(".transcript.json")
os.environ["POLIXOR_FIXTURE_TRANSCRIPT"] = str(FIXTURE)

from fastapi.testclient import TestClient                            # noqa: E402

from polixor.config import PATHS                                      # noqa: E402
from polixor.db import session_scope                                  # noqa: E402
from polixor.main import app                                          # noqa: E402
from polixor.models import Job, StageTiming                           # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
results: list[bool] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    results.append(bool(cond))
    print(f"  {PASS if cond else FAIL} {label}" + (f"  — {detail}" if detail and not cond else ""))


def wait(c: TestClient, pid: str, phases: tuple[str, ...], timeout: float = 600) -> dict:
    t0 = time.time()
    p: dict = {}
    while time.time() - t0 < timeout:
        p = c.get(f"/api/projects/{pid}").json()
        if p["phase"] in phases or p["status"] in ("failed", "cancelled"):
            return p
        time.sleep(0.5)
    return p


def artifacts(pid: str) -> dict:
    with session_scope() as s:
        return dict(s.get(Job, pid).artifacts or {})


def stage_rows(pid: str) -> list[str]:
    with session_scope() as s:
        return [t.stage for t in s.query(StageTiming).filter(StageTiming.job_id == pid)
                .order_by(StageTiming.id)]


CACHED = ("audio_path", "transcript_path", "timeline_full_path", "audio_features_path",
          "silences_path", "visual_path", "faces_path", "layouts_path")


def fingerprints(pid: str) -> dict[str, float]:
    arts = artifacts(pid)
    out = {}
    for k in CACHED + ("candidates_path", "corrections_path", "clip_review_path"):
        p = arts.get(k)
        if p and Path(p).exists():
            out[k] = Path(p).stat().st_mtime_ns
    return out


def coverage(pid: str) -> list[tuple[float, float]]:
    from polixor.services import analysis_store

    arts = artifacts(pid)
    vf = analysis_store.load_visual(Path(arts["visual_path"]) if arts.get("visual_path") else None,
                                    Path(arts["faces_path"]) if arts.get("faces_path") else None)
    return list(vf.coverage) if vf is not None else []


def total(cov: list[tuple[float, float]]) -> float:
    return sum(b - a for a, b in cov)


def run_case(c: TestClient, *, windowed: bool) -> None:
    label = "FAST, מקור ארוך (חלונות)" if windowed else "רגיל"
    print(f"\n▶ {label}")
    c.put("/api/settings", json={
        "transcript_provider": "fixture", "ai_mode": "heuristic", "video_quality": "low",
        "performance_profile": "fast", "long_source_minutes": 1 if windowed else 20})
    with VIDEO.open("rb") as f:
        tok = c.post("/api/upload", files={"file": (VIDEO.name, f, "video/mp4")}).json()["upload_token"]
    p = c.post("/api/projects", json={"source": {"type": "upload", "upload_token": tok},
                                       "ui_language": "he", "content_language": "he"}).json()
    pid = p["id"]
    wait(c, pid, ("configure",))
    cfg = {"mode": "short", "aspect_ratio": "9:16", "clip_count": 3, "clip_min_seconds": 10,
           "clip_max_seconds": 45, "layout": "auto"}
    c.post(f"/api/projects/{pid}/generate", json={"config": cfg})
    p = wait(c, pid, ("done",))
    check(p["phase"] == "done", "יצירה ראשונה הושלמה", str(p.get("error")))
    clips = c.get(f"/api/projects/{pid}/clips").json()
    check(len(clips) >= 1, f"נוצרו {len(clips)} קליפים")
    if not clips:
        return
    stages0 = stage_rows(pid)
    fp0 = fingerprints(pid)
    check(all(k in fp0 for k in ("audio_path", "transcript_path", "timeline_full_path")),
          "האודיו, התמלול וציר הזמן נשמרו לדיסק", str(sorted(fp0)))
    if windowed:
        check(artifacts(pid).get("visual_mode") == "windows", "מצב חלונות פעיל")
    clip = clips[0]
    cid = clip["id"]
    cov0 = coverage(pid)

    # 1. טקסט כתוביות
    cues = c.get(f"/api/clips/{cid}/cues").json()
    if cues:
        body = [{"id": q["id"], "start": q["start"], "end": q["end"], "text": q["text"]} for q in cues]
        body[0]["text"] += " !"
        check(c.put(f"/api/clips/{cid}/cues", json=body).status_code == 200, "תיקון טקסט נשמר")
    # 2. עיצוב, 3. יחס מסך, 4. גבולות – כל אחד ייצוא מחדש
    style = dict(clip.get("subtitle_style") or {})
    style["size"] = int(style.get("size", 60)) + 6
    for what, payload in (
            ("עיצוב כתוביות", {"subtitle_style": style}),
            ("יחס מסך", {"aspect": "1:1"}),
            ("גבולות קליפ", {"source_start": max(0.0, clip["source_start"] - 3.0),
                             "source_end": min(90.0, clip["source_end"] + 3.0)})):
        t0 = time.monotonic()
        r = c.post(f"/api/clips/{cid}/reexport", json=payload)
        wall = time.monotonic() - t0
        check(r.status_code == 200 and r.json()["status"] in ("ready", "needs_review"),
              f"ייצוא מחדש אחרי שינוי {what}", r.text[:200])
        # רוב הזמן הוא הקידוד עצמו – אין עבודה לא קשורה (ניתוח, תמלול) בייצוא
        rs = (c.get(f"/api/clips/{cid}").json().get("render_params") or {}).get("render_stats") or {}
        check(rs.get("encode_seconds", 0) > 0 and rs.get("encoder") and rs.get("size")
              and wall - rs["encode_seconds"] < max(6.0, 0.5 * wall),
              f"נתוני רינדור נשמרו; מחוץ לקידוד {wall - rs.get('encode_seconds', 0):.1f} ש׳ מתוך {wall:.1f}",
              str(rs))

    fp1 = fingerprints(pid)
    unchanged = [k for k in CACHED if k in fp0 and fp1.get(k) == fp0[k]]
    changed = [k for k in CACHED if k in fp0 and fp1.get(k) != fp0[k]]
    if windowed:
        # הרחבת הגבולות מעבר לחלון שנותח מוסיפה *רק* את החלק החסר לניתוח החזותי
        allowed = {"visual_path", "faces_path", "layouts_path"}
        check(set(changed) <= allowed, "עריכות לא נגעו באודיו, בתמלול ובציר הזמן", str(changed))
        cov1 = coverage(pid)
        a, b = max(0.0, clip["source_start"] - 3.0), min(90.0, clip["source_end"] + 3.0)
        covered = sum(max(0.0, min(b, y) - max(a, x)) for x, y in cov1) >= (b - a) * 0.9
        grew = total(cov1) - total(cov0)
        check(covered and grew <= 3.0 * 2 + 4.0 + 0.5,
              f"הניתוח החזותי הורחב רק לחלק החסר (+{grew:.1f} ש׳)", f"{cov0} → {cov1}")
    else:
        check(not changed, "עריכות לא שינו אף קובץ ניתוח שמור", str(changed))
    check(stage_rows(pid) == stages0, "אף שלב לא רץ שוב (אין תמלול או ניתוח חדשים)",
          f"{stages0} → {stage_rows(pid)}")

    # 5. יצירה מחדש: רק בחירה ורינדור, בלי תמלול/ניתוח
    c.post(f"/api/projects/{pid}/generate", json={"config": {**cfg, "clip_count": 2}})
    p = wait(c, pid, ("done",))
    new_rows = stage_rows(pid)[len(stages0):]
    check(p["phase"] == "done" and new_rows and set(new_rows) <= {"select", "render_short"},
          "יצירה מחדש: רק בחירה ורינדור", str(new_rows))
    fp2 = fingerprints(pid)
    check(all(fp2.get(k) == fp1.get(k) for k in ("audio_path", "transcript_path",
                                                 "timeline_full_path", "audio_features_path")),
          "יצירה מחדש לא תמללה ולא ניתחה שוב")
    subs = [i["name"] for run in (artifacts(pid).get("substage_timings") or [])[-1:]
            for i in run["items"]]
    check("analyze.audio" not in subs and "analyze.visual" not in subs,
          "בתתי-השלבים של היצירה מחדש אין ניתוח אודיו/וידאו", str(subs))
    perf = c.get(f"/api/projects/{pid}").json().get("performance") or {}
    check(bool(perf.get("stages")) and all("rtf" in s for s in perf["stages"]),
          "זמני עיבוד עם RTF מוצגים לפרויקט")


def main() -> int:
    if not (VIDEO.exists() and FIXTURE.exists()):
        print("אין וידאו בדיקה – דילוג")
        return 0
    PATHS.ensure()
    with TestClient(app) as c:
        run_case(c, windowed=False)
        run_case(c, windowed=True)
    ok = sum(results)
    print(f"\n{ok}/{len(results)} בדיקות שימוש חוזר בניתוח עברו")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
