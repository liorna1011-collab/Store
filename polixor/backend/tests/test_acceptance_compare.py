"""
בדיקות לכלי ההשוואה BEFORE/AFTER (שלב 12, scripts/acceptance_compare.py) –
בלי להריץ את הצינור: נתוני ריצה מזויפים ותמלול קטן.

  * דיוק כתוביות: WER/CER עם נרמול עברית (ניקוד, גרש), קריאת SRT/VTT.
  * אמת-מידה אחת: קליפ שמתחיל באמצע משפט / נגמר באמצע משפט מזוהה; קליפ
    שלם מקבל ציון גבוה יותר מקליפ חתוך.
  * כפילויות: חפיפה בזמן או אותו סיפור.
  * דוח: compare.md עם שתי העמודות; compare.html עיוור (הגרסה לא מופיעה
    בכרטיס), עם קבצי הווידאו בנתיב יחסי, ודגימות כתוביות בסדר אקראי.

הרצה:  python3 tests/test_acceptance_compare.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))
DATA = tempfile.mkdtemp(prefix="pxcmp_")
os.environ["POLIXOR_DATA_DIR"] = DATA

import acceptance_compare as ac                                      # noqa: E402

SEGS = [
    (0.0, 3.0, "שלום לכולם, ברוכים הבאים לשידור."),
    (3.5, 7.0, "אתם לא תאמינו מה קרה לי אתמול בערב."),
    (7.2, 11.0, "הלכתי לקנות פיצה ופתאום המוכר זיהה אותי מהסטרים."),
    (11.2, 15.0, "הוא אמר לי שהוא צופה בכל שידור כבר שלוש שנים."),
    (15.2, 19.0, "ובסוף הוא נתן לי את הפיצה בחינם, זה היה מטורף!"),
    (19.5, 23.0, "טוב, בואו נחזור למשחק."),
    (23.5, 27.0, "עכשיו אני צריך למצוא את המפתח בחדר הזה."),
]


def _transcript(segs=SEGS, path: Path | None = None) -> Path:
    items = []
    for a, b, t in segs:
        words = t.split()
        step = (b - a) / len(words)
        items.append({"start": a, "end": b, "text": t, "words": [
            {"start": round(a + i * step, 3), "end": round(a + (i + 1) * step - 0.02, 3), "text": w, "p": 0.9}
            for i, w in enumerate(words)]})
    path = path or Path(DATA) / "transcript.json"
    path.write_text(json.dumps({"language": "he", "segments": items}, ensure_ascii=False), "utf-8")
    return path


def test_wer_cer_and_hebrew_normalisation():
    assert ac.wer("שָׁלוֹם לכולם", "שלום לכולם") == (0, 2)            # ניקוד לא נחשב טעות
    assert ac.wer("הלכתי לקנות פיצה", "הלכתי לקנות פיצה היום") == (1, 3)
    assert ac.wer("a b c", "a x c") == (1, 3)
    e, n = ac.cer("פיצה", "פיזה")
    assert (e, n) == (1, 4)
    assert ac.normalize_tokens("צ׳יפס, ה\"שידור\"!") == ["צ'יפס", "ה", "שידור"]


def test_parse_reference_srt_and_vtt():
    srt = Path(DATA) / "ref.srt"
    srt.write_text("1\n00:00:03,500 --> 00:00:07,000\nאתם לא תאמינו\nמה קרה לי אתמול בערב.\n\n"
                   "2\n00:00:07,200 --> 00:00:11,000\n<i>הלכתי לקנות פיצה</i>\n", "utf-8")
    ref = ac.parse_reference(srt)
    assert ref[0] == (3.5, 7.0, "אתם לא תאמינו מה קרה לי אתמול בערב.") and ref[1][2] == "הלכתי לקנות פיצה"
    vtt = Path(DATA) / "ref.vtt"
    vtt.write_text("WEBVTT\n\n00:01:02.250 --> 00:01:04.000\nhello there\n", "utf-8")
    assert ac.parse_reference(vtt) == [(62.25, 64.0, "hello there")]


def test_text_accuracy_against_reference():
    tr = ac.load_transcript({"transcript_path": str(_transcript())}, effective=False)
    ref = [(7.2, 11.0, "הלכתי לקנות פיצה ופתאום המוכר זיהה אותי מהסטרים."),
           (15.2, 19.0, "ובסוף הוא נתן לי את הפיצה בחינם")]
    acc = ac.text_accuracy(ref, tr)
    assert acc["segments"] == 2 and acc["wer"] < 0.4
    assert ac.text_accuracy([], tr) is None


def test_yardstick_flags_cut_clips_and_prefers_complete_story():
    tr = ac.load_transcript({"transcript_path": str(_transcript())}, effective=False)
    y = ac.Yardstick(tr, None, "he", min_d=5.0)
    full = y.span(3.4, 19.1)                 # וו → הקשר → פאנץ'
    cut = y.span(8.5, 13.0)                  # מתחיל ונגמר באמצע משפט
    assert not full["empty"] and not full["starts_mid_sentence"] and not full["ends_mid_sentence"]
    assert cut["starts_mid_sentence"] and cut["ends_mid_sentence"]
    assert full["final"] > cut["final"]
    assert "פיצה" in full["text"] and full["tokens"]
    assert y.span(40.0, 50.0)["empty"]


def test_duplicate_pairs():
    tr = ac.load_transcript({"transcript_path": str(_transcript())}, effective=False)
    y = ac.Yardstick(tr, None, "he", min_d=5.0)
    clips = [{"start": 3.4, "end": 19.1}, {"start": 7.0, "end": 19.1}, {"start": 19.4, "end": 27.1}]
    for c in clips:
        c["eval"] = y.span(c["start"], c["end"])
    pairs = ac.duplicate_pairs(clips)
    assert [(i, j) for i, j, _ in pairs] == [(0, 1)]


def _run(label: str, clips, tp: Path) -> dict:
    out = Path(DATA) / label
    out.mkdir(exist_ok=True)
    files = []
    for i, _ in enumerate(clips):
        f = out / f"clip{i}.mp4"
        f.write_bytes(b"\x00" * 64)
        files.append(str(f))
    return {"label": label, "media_seconds": 27.0, "analysis_seconds": 10.0, "generation_seconds": 5.0,
            "total_seconds": 15.0, "stages": [], "reexport": {"seconds": 2.5, "clip_seconds": 15.0},
            "artifacts": {"transcript_path": str(tp)},
            "clips": [{"id": f"{label}{i}", "title": f"t{i}", "start": a, "end": b, "file": files[i],
                       "cues": [{"start": 0, "end": 2, "text": "טקסט"}]} for i, (a, b) in enumerate(clips)]}


def test_report_is_blind_and_complete():
    tp_after = _transcript()
    before_segs = [(a, b, t.replace("פיצה", "פיזה")) for a, b, t in SEGS]
    tp_before = _transcript(before_segs, Path(DATA) / "before_transcript.json")
    runs = {"before": _run("before", [(8.5, 13.0), (9.0, 13.5), (19.4, 27.1)], tp_before),
            "after": _run("after", [(3.4, 19.1)], tp_after)}
    ev = ac.evaluate(runs, "he", [(7.2, 11.0, "הלכתי לקנות פיצה ופתאום המוכר זיהה אותי מהסטרים.")])
    assert ev["before"]["stats"]["clips"] == 3 and ev["after"]["stats"]["clips"] == 1
    assert ev["before"]["stats"]["duplicate_pairs"] == 1 and ev["after"]["stats"]["duplicate_pairs"] == 0
    assert ev["before"]["stats"]["starts_mid_sentence"] >= 1 and ev["after"]["stats"]["starts_mid_sentence"] == 0
    assert ev["after"]["text_accuracy"]["wer"] < ev["before"]["text_accuracy"]["wer"]
    assert ev["after"]["timing"]["words"] > 0
    out = Path(DATA) / "report"
    out.mkdir()
    ac.write_report(out, runs, ev)
    md = (out / "compare.md").read_text("utf-8")
    assert "| BEFORE | AFTER |" in md and "Duplicate pairs" in md and "Re-export" in md
    page = (out / "compare.html").read_text("utf-8")
    data = json.loads(page.split("const DATA = ", 1)[1].split(";\nconst KEY", 1)[0])
    assert len(data["cards"]) == 4
    assert all("version" not in c for c in data["cards"])                # כרטיס לא מגלה את הגרסה
    assert all(not c["file"].startswith("/") for c in data["cards"])     # נתיב יחסי – נפתח מהתיקייה
    assert set(data["versions"].values()) == {"before", "after"}
    assert data["samples"] and all(set(s["order"]) == {"before", "after"} for s in data["samples"])
    summary = json.loads((out / "compare.json").read_text("utf-8"))["summary"]
    assert summary["after"]["reexport_seconds"] == 2.5 and summary["before"]["total_rtf"] == round(15 / 27, 3)


def test_cli_rejects_missing_video():
    try:
        ac.main(["--media", "/nope/missing.mp4"])
        raise AssertionError("expected exit")
    except SystemExit as exc:
        assert "not found" in str(exc)


# --------------------------------------------------------------------------
def _run_all() -> int:
    fns = [(n, globals()[n]) for n in list(globals()) if n.startswith("test_")]
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
    print(f"\n{passed}/{len(fns)} בדיקות כלי ההשוואה עברו")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
