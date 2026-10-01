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
        f = out / f"clip{i} #1 100% רגע.mp4"          # שמות קליפים נגזרים מהכותרת
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
    from urllib.parse import unquote
    for c in data["cards"]:                                             # מקודד: # ו-% לא שוברים את הקישור
        assert "#" not in c["file"] and " " not in c["file"] and "%23" in c["file"]
        assert (out / unquote(c["file"])).resolve().exists(), c["file"]
    assert set(data["versions"].values()) == {"before", "after"}
    assert data["samples"] and all(set(s["order"]) == {"before", "after"} for s in data["samples"])
    summary = json.loads((out / "compare.json").read_text("utf-8"))["summary"]
    assert summary["after"]["reexport_seconds"] == 2.5 and summary["before"]["total_rtf"] == round(15 / 27, 3)


def _wav(path: Path, seconds: float = 2.0) -> Path:
    import wave

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x01\x00" * int(16000 * seconds))
    return path


def test_wav_info_detects_unfinished_extraction():
    p = _wav(Path(DATA) / "a.wav")
    info = ac.wav_info(p)
    assert info["valid"] and info["seconds"] == 2.0 and info["rate"] == 16000
    with open(p, "r+b") as f:                       # ffmpeg killed: the size field is still a placeholder
        f.seek(40)
        f.write(b"\xff\xff\xff\xff")
    assert not ac.wav_info(p)["valid"]
    p = _wav(Path(DATA) / "b.wav")
    os.truncate(p, 30000)                            # cut short
    assert "did not finish" in ac.wav_info(p)["problem"]
    assert not ac.wav_info(Path(DATA) / "missing.wav")["valid"]


def test_transcript_info_rejects_fallbacks_and_cut_files():
    good = Path(DATA) / "t_good.json"
    good.write_text(json.dumps({"provider": "faster-whisper", "model": "small", "language": "he", "note": "",
                                "duration": 30.0, "segments": [{"start": 0.5, "end": 29.0, "text": "שלום",
                                                                "words": [{"start": 0.5, "end": 1, "text": "שלום"}]}]}),
                    "utf-8")
    assert ac.transcript_info(good)["valid"]
    fb = Path(DATA) / "t_fb.json"
    fb.write_text(json.dumps({"provider": "none", "note": "fallback", "segments": []}), "utf-8")
    t = ac.transcript_info(fb)
    assert not t["valid"] and any("fallback" in x or "faster-whisper" in x for x in t["problems"])
    cut = Path(DATA) / "t_cut.json"
    cut.write_text(good.read_text("utf-8")[:40], "utf-8")
    assert "cut off" in ac.transcript_info(cut)["problem"]


OLD_SCHEMA = """
CREATE TABLE jobs (id TEXT PRIMARY KEY, title TEXT, status TEXT, stage TEXT, stage_progress REAL,
  overall_progress REAL, message TEXT, phase TEXT, run_scope TEXT, completed_stages TEXT, artifacts TEXT,
  settings_snapshot TEXT);
CREATE TABLE stage_timings (id INTEGER PRIMARY KEY, job_id TEXT, stage TEXT, seconds REAL, media_seconds REAL);
CREATE TABLE transcript_segments (id INTEGER PRIMARY KEY, job_id TEXT, text TEXT);
"""


def _checkpoint(name: str, *, completed=("probe", "audio", "transcribe"), stage="ANALYZE",
                stage_progress=0.44, overall=0.81, seg_rows=1) -> tuple[Path, Path]:
    import sqlite3

    out = Path(DATA) / name
    data = out / "before_data"
    work = data / "work" / "job1"
    work.mkdir(parents=True)
    media = Path(DATA) / f"{name}.mp4"
    media.write_bytes(b"\x00" * 2048)
    (data / "sources").mkdir()
    (data / "sources" / "stream.mp4").symlink_to(media)
    _wav(work / "audio16k.wav", 30.0)
    (work / "transcript.json").write_text(json.dumps({
        "provider": "faster-whisper", "model": "small", "language": "he", "note": "", "duration": 30.0,
        "segments": [{"start": 0.5, "end": 29.0, "text": "שלום", "words": []}]}), "utf-8")
    con = sqlite3.connect(str(data / "polixor.db"))
    con.executescript(OLD_SCHEMA)
    arts = {"source_path": str(data / "sources" / "stream.mp4"), "audio_path": str(work / "audio16k.wav"),
            "transcript_path": str(work / "transcript.json"), "source_info": {"duration": 30.0, "size": 2048}}
    con.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("job1", "before: stream.mp4", "RUNNING", stage, stage_progress, overall, "", "analyzing",
                 "analyze", json.dumps(list(completed)), json.dumps(arts),
                 json.dumps({"transcript_provider": "faster-whisper"})))
    for st, sec in (("probe", 0.4), ("audio", 52.0), ("transcribe", 3100.0)):
        if st in completed:
            con.execute("INSERT INTO stage_timings (job_id, stage, seconds, media_seconds) VALUES (?,?,?,?)",
                        ("job1", st, sec, 30.0))
    for _ in range(seg_rows):
        con.execute("INSERT INTO transcript_segments (job_id, text) VALUES ('job1', 'x')")
    con.commit()
    con.close()
    return out, media


def test_inspect_before_finds_the_furthest_valid_checkpoint():
    out, media = _checkpoint("ck_ok")
    before = {p: p.stat().st_mtime_ns for p in (out / "before_data").rglob("*") if p.is_file()}
    rep = ac.inspect_before(out, media, None)
    assert rep["resumable"], rep["problems"]
    assert rep["job"]["completed_stages"] == ["probe", "audio", "transcribe"]
    assert "video frames" in rep["active_at_stop"]
    assert rep["reuse"] == ["probe", "audio", "transcribe"]
    assert any(r.startswith("analysis") for r in rep["rerun"]) and not any("transcription" == r for r in rep["rerun"])
    assert rep["transcript"]["valid"] and rep["audio"]["valid"]
    after = {p: p.stat().st_mtime_ns for p in (out / "before_data").rglob("*") if p.is_file()}
    assert before == after                             # read-only: nothing touched


def test_inspect_before_refuses_unprovable_checkpoints():
    out, media = _checkpoint("ck_rows", seg_rows=3)        # transcript ≠ database
    assert not ac.inspect_before(out, media, None)["resumable"]
    out, media = _checkpoint("ck_gen", completed=("probe", "audio", "transcribe", "analyze"), stage="RENDER_SHORT")
    rep = ac.inspect_before(out, media, None)
    assert not rep["resumable"] and any("partial outputs" in p for p in rep["problems"])
    out, media = _checkpoint("ck_wrong")
    other = Path(DATA) / "other.mp4"
    other.write_bytes(b"\x00" * 2048)
    assert not ac.inspect_before(out, other, None)["resumable"]
    out, media = _checkpoint("ck_partial_audio", completed=("probe",), stage="AUDIO")
    rep = ac.inspect_before(out, media, None)
    assert not rep["resumable"] and any("never completed" in p for p in rep["problems"])
    out, media = _checkpoint("ck_done")
    (out / "before.json").write_text("{}", "utf-8")
    assert not ac.inspect_before(out, media, None)["resumable"]


def test_stage_max_ignores_skipped_stage_records():
    rows = [{"stage": "audio", "seconds": 52.0}, {"stage": "probe", "seconds": 0.4},
            {"stage": "probe", "seconds": 0.3}, {"stage": "audio", "seconds": 0.01}, {"stage": "analyze", "seconds": 900}]
    assert ac._stage_max(rows) == {"audio": 52.0, "probe": 0.4, "analyze": 900}


def test_cli_rejects_missing_video():
    try:
        ac.main(["--media", "/nope/missing.mp4"])
        raise AssertionError("expected exit")
    except SystemExit as exc:
        assert "not found" in str(exc)


def test_diagnostics_capture_what_actually_ran():
    """Model, strong-model use, proofreading, near misses and render stats – from the run's own records."""
    import sqlite3

    d = Path(tempfile.mkdtemp(prefix="pxdiag_"))
    tr = d / "transcript.json"
    tr.write_text(json.dumps({"provider": "faster-whisper", "model": "small", "language": "he"}), "utf-8")
    corr = d / "corr.json"
    corr.write_text(json.dumps({"strong_model": "ivrit-ai/whisper-large-v3-turbo-ct2",
                                "retranscribed_seconds": 40.0, "strong_wall_seconds": 31.5,
                                "priority_checked": 6, "stats": {"corrected": 2}}), "utf-8")
    rev = d / "review.json"
    rev.write_text(json.dumps({"stats": {"selected": 1}, "topics": [{"start": 0, "end": 60}],
                               "selected": [{"start": 1, "end": 30, "final_score": 0.7,
                                             "hook": {"text": "h", "categories": ["opinion"]},
                                             "payoff": {"text": "p"}, "proposed_by": [{"key": "source.argument"}]}],
                               "near_misses": [{"start": 40, "end": 70, "final_score": 0.4,
                                                "rejection": {"key": "reject.weak_hook"},
                                                "hook": {"text": "x"}, "payoff": {"text": "y"}}]}), "utf-8")
    con = sqlite3.connect(str(d / "polixor.db"))
    con.execute("CREATE TABLE jobs (id TEXT, settings_snapshot TEXT)")
    con.execute("CREATE TABLE clips (id TEXT, job_id TEXT, render_params TEXT)")
    con.execute("INSERT INTO jobs VALUES ('j', ?)", (json.dumps({"whisper_model": "small",
                                                              "performance_profile": "fast"}),))
    con.execute("INSERT INTO clips VALUES ('c', 'j', ?)", (json.dumps({"render_stats": {
        "encode_seconds": 60.1, "encoder": "libx264", "preset": "medium", "size": "1080x1920"}}),))
    con.commit()
    con.close()
    arts = {"transcript_path": str(tr), "corrections_path": str(corr), "clip_review_path": str(rev),
            "notes": ["n1"], "substage_timings": [{"items": []}]}
    rx = {"seconds": 70.8, "clip_seconds": 20.5, "render_stats": {"encode_seconds": 66.0, "encoder": "libx264"}}
    diag = ac.collect_diagnostics(d, "j", arts, {}, rx)
    assert diag["asr"]["model"] == "small" and diag["asr"]["profile"] == "fast"
    assert diag["proofread"]["priority_checked"] == 6 and diag["proofread"]["strong_wall_seconds"] == 31.5
    assert diag["selection"]["near_misses"][0]["rejection"] == "reject.weak_hook"
    assert diag["selection"]["selected"][0]["proposed_by"] == ["source.argument"]
    assert diag["render_stats"][0]["encode_seconds"] == 60.1 and diag["notes"] == ["n1"]
    md = "\n".join(ac.diagnostics_lines({"after": {"diagnostics": diag}}))
    assert "faster-whisper / small" in md and "ivrit-ai" in md and "encode 66.0 s" in md, md
    # a version that records nothing still works
    assert ac.collect_diagnostics(d, "missing", {}, {}, None)["proofread"]["strong_model"] is None


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
