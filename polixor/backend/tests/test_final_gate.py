"""
בדיקות לבדיקת הכתוביות האחרונה (subtitle_qa), לשער העורך לפני רינדור
(pipeline._final_gate) ולאוצר המילים של הפרויקט.

הרצה:  python3 tests/test_final_gate.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxgate_"))

from polixor.services import subtitle_qa as qa                       # noqa: E402
from polixor.services import transcript_correct as tc                # noqa: E402
from polixor.services.transcribe import Segment, TranscriptResult, Word  # noqa: E402


def words(text: str, p: float = 0.95, t0: float = 0.0, flag: str = "") -> list[Word]:
    return [Word(t0 + i * 0.3, t0 + i * 0.3 + 0.25, w, p, flag=flag) for i, w in enumerate(text.split())]


def test_clean_hebrew_with_english_and_slang_passes():
    r = qa.check(words("אחי הסטרים הזה ב Kick היה 100 אחוז מטורף"), "he")
    assert r["ok"] and not r["warnings"], r


def test_broken_transcription_is_severe():
    assert "foreign_script" in qa.check(words("סד 걱정 זה לא צומח"), "he")["severe"]
    assert "mixed_script_word" in qa.check(words("סימן בשביליםcular היום"), "he")["severe"]
    loop = qa.check(words(" ".join(["קבודלת לי,"] * 10)), "he")
    assert "hallucination_loop" in loop["severe"]
    low = qa.check(words("מפוסע ממני השבל שדהר דתך פלטפורמה", p=0.3), "he")
    assert "too_many_uncertain_words" in low["severe"]


def test_a_few_uncertain_words_warn_and_confirmed_words_do_not():
    ws = words("לדעתי הוא השחקן הכי טוב בליגה הזאת ובכלל בעולם כולו") + words("דומפריז", p=0.3, t0=5)
    r = qa.check(ws, "he")
    assert r["ok"] and "uncertain_words" in r["warnings"] and r["suspicious"][0]["text"] == "דומפריז"
    ok = qa.check(words("לדעתי הוא השחקן הכי טוב בליגה") + words("דומפריז", p=0.3, t0=5, flag="confirmed"), "he")
    assert ok["ok"] and not ok["warnings"], ok


def test_final_gate_drops_broken_clips_and_records_why():
    from polixor import pipeline
    from polixor.services import analysis_store
    from polixor.services.selection import Candidate

    good = Segment(0.0, 4.0, "לדעתי הוא השחקן הכי טוב בליגה", words=words("לדעתי הוא השחקן הכי טוב בליגה"))
    bad = Segment(20.0, 24.0, "סד 걱정 זה לא צומח", words=words("סד 걱정 זה לא צומח", t0=20.0))
    tr = TranscriptResult(segments=[good, bad], language="he", duration=30.0, provider="test")
    work = Path(tempfile.mkdtemp())
    rp = analysis_store.save_clip_review(work, {"selected": []})
    notes: list[str] = []
    ctx = NS(transcript=tr, language="he", artifacts={"clip_review_path": str(rp)}, work_dir=work,
             note=notes.append)
    c1 = Candidate(start=0.0, end=4.5, peak_time=2.0, score=0.7, title="a",
                   quality={"engine": "clip_intel", "editorial": {"hook": "הוא השחקן הכי טוב?"}})
    c2 = Candidate(start=19.5, end=24.5, peak_time=22.0, score=0.8, title="b",
                   quality={"engine": "clip_intel", "editorial": {}})
    kept = pipeline._final_gate(ctx, [c1, c2])
    assert kept == [c1], kept
    assert c1.quality["final_qa"]["passed"] and not c2.quality["final_qa"]["passed"]
    rev = analysis_store.load_clip_review(rp)
    assert [r["passed"] for r in rev["final_qa"]] == [True, False]
    assert notes and "b" in notes[0]


def test_proofreading_keeps_strong_marks_and_confirmations():
    seg = Segment(0.0, 3.0, "מבאפה עשה את זה", words=[Word(0.0, 0.5, "מבאפה", 0.4, asr="strong"),
                                                       Word(0.6, 1.0, "עשה", 0.9, asr="strong"),
                                                       Word(1.1, 1.5, "את", 0.9, asr="strong"),
                                                       Word(1.6, 2.0, "זה", 0.9, asr="strong")])
    tr = TranscriptResult(segments=[seg], language="he", duration=5.0)
    for status in ("flagged", "confirmed"):
        data = {"segments": [{"index": 0, "original": seg.text, "status": status,
                              "low_words": [{"i": 0}]}]}
        out = tc.apply(tr, data)
        assert all(w.asr == "strong" for w in out.segments[0].words), status
    out = tc.apply(tr, {"segments": [{"index": 0, "original": seg.text, "status": "confirmed"}]})
    assert qa.check(out.segments[0].words, "he")["ok"] and not qa.check(out.segments[0].words, "he")["warnings"]


def test_project_vocabulary_reaches_the_strong_model():
    from polixor.config import AppSettings

    d = Path(tempfile.mkdtemp())
    wav = d / "a.wav"
    wav.write_bytes(b"")
    st = AppSettings.from_dict({**AppSettings().to_dict(), "asr_vocabulary": ["Kick"]})
    r = tc.WhisperRetranscriber(wav, st, "he")
    r.add_vocabulary(["דומפריז", "Kick", "מבאפה"])
    hw = r.plan.hotwords or ""
    assert "דומפריז" in hw and "מבאפה" in hw and hw.count("Kick") == 1, hw


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
    print(f"\n{passed}/{len(fns)} בדיקות שער אחרון עברו")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
