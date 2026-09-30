"""
בדיקות לניתוח מקורות ארוכים: אזורים עם חפיפה, ניתוח חזותי בחלונות
המועמדים בלבד (מצב FAST), ושימוש חוזר בניתוח בין יצירות.

הרצה:  python3 tests/test_long_source.py
"""

from __future__ import annotations

import os
import random
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxlong_"))

import numpy as np                                                    # noqa: E402

from polixor import i18n                                              # noqa: E402
from polixor.services import clip_intel                               # noqa: E402
from polixor.services.clip_intel.regions import overlap_for, plan_regions  # noqa: E402
from polixor.services.clip_intel.score import Scored                  # noqa: E402
from polixor.services.clip_intel.story import Proposal                # noqa: E402
from polixor.services.clip_intel.visual_check import apply_visual     # noqa: E402
from polixor.services.visual import VisualFeatures, merge_windows, subtract_windows  # noqa: E402
import test_clip_intel as T                                           # noqa: E402

VIDEO = Path("/home/claude/testdata/polixor_test_stream.mp4")
FIXTURE = VIDEO.with_suffix(".transcript.json")


# --------------------------------------------------------------------------
def test_regions_cover_every_possible_clip():
    """כל חלון באורך עד הקליפ המרבי נמצא בשלמותו באזור אחד לפחות."""
    rng = random.Random(7)
    for duration, max_d in ((3600.0, 60.0), (8 * 3600.0, 90.0), (1500.0, 180.0)):
        regions = plan_regions(duration, max_d)
        assert regions[0].start == 0.0 and regions[-1].end == duration
        for a, b in zip(regions, regions[1:]):
            assert a.end - b.start >= overlap_for(max_d) - 1e-6, (a, b)
        for _ in range(3000):
            L = rng.uniform(5.0, max_d)
            s = rng.uniform(0.0, duration - L)
            assert any(r.contains(s, s + L) for r in regions), (s, L)


def test_short_source_is_one_region():
    assert len(plan_regions(600.0, 60.0)) == 1


def test_story_across_a_region_boundary_is_found():
    """סיפור שמתחיל בסוף אזור ונגמר בתחילת הבא לא הולך לאיבוד."""
    filler = ["אני אוסף פה משאבים לאט לאט", "הדמות מתקדמת בשלב הזה",
              "יש פה כמה פריטים בחנות", "נמשיך לשחק בינתיים בשקט"]
    lines = []
    t = 0.0
    while t < 1200.0:
        if 470.0 <= t < 520.0:
            t = 520.0
            continue
        lines.append((t, t + 4.2, filler[int(t / 5) % len(filler)]))
        t += 5.0
    story = [(470.0, 474.0, "תקשיבו, אני חייב לספר לכם מה קרה לי אתמול בערב"),
             (474.5, 479.0, "הלכתי לסופר לקנות חלב ולחם כמו כל יום רגיל"),
             (479.5, 484.0, "ובקופה הקופאית מסתכלת עליי ושואלת אם אני הסטרימר"),
             (484.5, 489.0, "ובסוף התברר שהיא צופה בכל שידור שלי כבר שנתיים!"),
             (489.2, 490.5, "חחחח אין מצב")]
    lines = sorted(lines + story)
    regions = plan_regions(lines[-1][1] + 5.0, 45.0)
    assert len(regions) >= 3
    # הסיפור חוצה את הגבול של האזור הראשון
    assert regions[0].end < 490.5 and regions[0].end > 470.0, regions[0]
    res, _ = T.run(lines, [(489.0, 491.0, 0.95)])
    assert len(res.selected) == 1, res.review["stats"]
    c = res.selected[0]
    assert 469.5 <= c.start <= 470.0 and c.end >= 490.5, (c.start, c.end)
    assert res.review["stats"]["regions"] == len(regions)
    assert sum(r["selected"] for r in res.review["regions"]) >= 1


def test_visual_check_penalises_black_video_and_is_idempotent():
    fps = 1.0
    n = 100
    vf = VisualFeatures(fps=fps, duration=100.0, times=np.arange(n, dtype=np.float32),
                        scene=np.full(n, 0.3, np.float32), motion=np.full(n, 0.3, np.float32),
                        brightness=np.full(n, 0.4, np.float32), flash=np.zeros(n, np.float32),
                        faces=[[] for _ in range(n)], coverage=[(0.0, 100.0)])
    vf.brightness[40:70] = 0.0                                 # מסך שחור
    good = Scored(proposal=Proposal(0, 1, 1, 5.0, 30.0), final=0.7, passed=True, threshold=0.5)
    black = Scored(proposal=Proposal(0, 1, 1, 40.0, 65.0), final=0.6, passed=True, threshold=0.5)
    for _ in range(2):                                        # פעמיים – אותה תוצאה
        assert apply_visual(good, vf, vf.coverage, 0.5)
        assert apply_visual(black, vf, vf.coverage, 0.5)
    assert "black_or_frozen_video" in black.penalties and black.final < 0.5 and not black.passed
    assert "black_or_frozen_video" not in good.penalties and good.final > 0.7 and good.passed
    # מחוץ לחלונות שנותחו – אין שינוי
    other = Scored(proposal=Proposal(0, 1, 1, 150.0, 170.0), final=0.6, passed=True)
    assert not apply_visual(other, vf, [(0.0, 100.0)], 0.5) and other.final == 0.6


def test_window_arithmetic():
    assert merge_windows([(10, 20), (19, 30), (50, 60)]) == [(10, 30), (50, 60)]
    assert subtract_windows([(0, 100)], [(10, 20), (50, 60)]) == [(0, 10), (20, 50), (60, 100)]
    assert subtract_windows([(10, 20)], [(0, 100)]) == []


def test_windowed_pipeline_analyses_only_candidate_windows():
    """
    ריצה אמיתית של הפייפליין במצב FAST עם סף "מקור ארוך" נמוך: הניתוח
    החזותי לא רץ על כל הסרטון, אלא רק על חלונות המועמדים, והקליפים
    נוצרים עם פנים ופריסה מהחלונות. יצירה חוזרת לא מנתחת שוב.
    """
    if not (VIDEO.exists() and FIXTURE.exists()):
        print("    (דילוג: אין וידאו בדיקה)")
        return
    from polixor.config import AppSettings, PATHS
    from polixor.db import init_db, session_scope
    from polixor.models import Clip, Job, JobStatus, RunScope, new_id
    from polixor.pipeline import run_job
    from polixor.services import analysis_store

    os.environ["POLIXOR_FIXTURE_TRANSCRIPT"] = str(FIXTURE)
    PATHS.ensure()
    init_db()
    base = AppSettings().to_dict()
    base.update({"transcript_provider": "fixture", "ai_mode": "heuristic",
                 "video_quality": "low", "performance_profile": "fast",
                 "long_source_minutes": 1, "short_min_seconds": 10, "short_max_seconds": 45,
                 "long_enabled": False, "short_count": 3, "short_layout": "auto"})
    jid = new_id()
    with session_scope() as s:
        s.add(Job(id=jid, title="long", input_url="", status=JobStatus.QUEUED,
                  settings_snapshot=AppSettings.from_dict(base).to_dict(),
                  artifacts={"source_path": str(VIDEO)}, completed_stages=[],
                  run_scope=RunScope.ALL.value))
    run_job(jid, threading.Event())
    with session_scope() as s:
        job = s.get(Job, jid)
        arts = dict(job.artifacts or {})
        clips = s.query(Clip).filter(Clip.job_id == jid).all()
        clip_ranges = [(c.source_start, c.source_end, c.status.value) for c in clips]
    assert arts.get("visual_mode") == "windows", arts.get("visual_mode")
    vf = analysis_store.load_visual(Path(arts["visual_path"]), Path(arts["faces_path"]))
    covered = sum(b - a for a, b in vf.coverage)
    assert vf.coverage and covered < 0.9 * 90.0, vf.coverage
    assert clip_ranges and all(st in ("ready", "needs_review") for _, _, st in clip_ranges), clip_ranges
    assert all(vf.covers(a, b) for a, b, _ in clip_ranges), (vf.coverage, clip_ranges)
    subs = [i["name"] for run in arts.get("substage_timings", []) for i in run["items"]]
    assert "select.visual_windows" in subs and "analyze.visual" not in subs, subs
    notes = " ".join(arts.get("notes") or [])
    assert "Long source" in notes or "מקור ארוך" in notes, notes

    # יצירה חוזרת מאותו ניתוח: אין ניתוח חזותי נוסף לאותם חלונות
    with session_scope() as s:
        job = s.get(Job, jid)
        job.run_scope = RunScope.GENERATE.value
        job.status = JobStatus.QUEUED
    run_job(jid, threading.Event())
    with session_scope() as s:
        arts2 = dict(s.get(Job, jid).artifacts or {})
    last = arts2["substage_timings"][-1]["items"]
    assert not any(i["name"] == "select.visual_windows" for i in last), last


# --------------------------------------------------------------------------
def _run_all() -> int:
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    passed, failed = 0, []
    with i18n.use_lang("en"):
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
    print(f"\n{passed}/{len(fns)} בדיקות מקור ארוך עברו")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
