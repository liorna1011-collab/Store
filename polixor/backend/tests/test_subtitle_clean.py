"""
בדיקות לניקוי הכתוביות (services/subtitle_clean) ולשבירה לפי ביטויים.

  * „אה"/„אממ" יורדים; „אחי" ומילים עם אופי נשארים.
  * גמגום של מילת קישור („אני אני") יורד; חזרה מכוונת („לא לא לא") נשארת.
  * המילים שנשארות שומרות על הזמנים המדויקים (הדגשת המילה הפעילה).
  * כתובית שהתמלאה נחתכת בגבול הביטוי, בלי מילה תלויה בסוף.

הרצה:  python3 tests/test_subtitle_clean.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxsubclean_"))

from polixor.services import caption_engine as ce                    # noqa: E402
from polixor.services import subtitle_clean as sc                    # noqa: E402
from polixor.services.transcribe import Segment, TranscriptResult, Word  # noqa: E402


def words(text: str, t0: float = 0.0, step: float = 0.32) -> list[Word]:
    return [Word(round(t0 + i * step, 3), round(t0 + i * step + step - 0.04, 3), w, 0.9)
            for i, w in enumerate(text.split())]


def tr(text: str, language: str = "he") -> TranscriptResult:
    ws = words(text)
    return TranscriptResult(segments=[Segment(ws[0].start, ws[-1].end, text, words=ws, language=language)],
                            language=language, duration=ws[-1].end + 2, provider="test")


def test_hesitations_removed_personality_words_kept():
    out = sc.clean_words(words("אה אחי אממ אני אומר לך משהו"), "he")
    assert [w.text for w in out] == ["אחי", "אני", "אומר", "לך", "משהו"]
    out = sc.clean_words(words("um so uh basically yeah"), "en")
    assert [w.text for w in out] == ["so", "basically", "yeah"]


def test_stutter_removed_but_emphasis_kept():
    assert [w.text for w in sc.clean_words(words("אני אני חושב שזה טוב"), "he")] == \
        ["אני", "חושב", "שזה", "טוב"]
    assert [w.text for w in sc.clean_words(words("לא לא לא אין מצב"), "he")] == \
        ["לא", "לא", "לא", "אין", "מצב"]
    assert [w.text for w in sc.clean_words(words("יאללה יאללה בוא"), "he")] == ["יאללה", "יאללה", "בוא"]
    assert [w.text for w in sc.clean_words(words("ש- שלום לכולם"), "he")] == ["שלום", "לכולם"]
    # רווח ארוך בין המילים – לא גמגום
    ws = [Word(0.0, 0.3, "זה", 0.9), Word(1.5, 1.8, "זה", 0.9), Word(1.9, 2.2, "נגמר", 0.9)]
    assert len(sc.clean_words(ws, "he")) == 3


def test_kept_words_keep_exact_timing():
    src = words("אה אני אני חושב שזה טוב")
    out = sc.clean_words(src, "he")
    by = {(w.start, w.end, w.text) for w in src}
    assert all((w.start, w.end, w.text) in by for w in out)
    t = tr("אה אני אני חושב שזה טוב")
    c = sc.cleaned(t, 0.0, 10.0)
    assert c is not t and t.segments[0].text == "אה אני אני חושב שזה טוב", "original untouched"
    assert c.segments[0].text == "אני חושב שזה טוב"
    cues = ce.build_captions(c, clip_start=0.0, clip_end=10.0, preset="clean")
    first = cues[0].words[0]
    assert first["text"] == "אני" and abs(first["start"] - src[2].start) < 1e-6, first


def test_full_cue_breaks_at_phrase_boundary():
    preset = ce.CaptionPreset(**{**ce.get_preset("clean").__dict__, "max_words": 8,
                                 "max_chars": 200, "max_lines": 1, "max_cue_seconds": 9.0,
                                 "pause_break": 0.9})
    t = tr("אני אומר לך משהו, הוא הכי טוב בליגה וזהו זה")
    cues = ce.build_captions(t, clip_start=0.0, clip_end=10.0, preset=preset, frame_chars=200)
    assert cues[0].text.endswith("משהו,"), [c.text for c in cues]
    # אף כתובית (חוץ מהאחרונה בקליפ) לא נגמרת במילה תלויה, ושום מילה לא אבדה
    for c in cues[:-1]:
        assert not ce._is_hanging(c.text.split()[-1]), c.text
    assert " ".join(c.text for c in cues) == t.segments[0].text, [c.text for c in cues]


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
    print(f"\n{passed}/{len(fns)} בדיקות ניקוי כתוביות עברו")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
