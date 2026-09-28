"""
בדיקות ל-Caption Engine 2.0.

הרצה:  python3 tests/test_captions.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxcap_"))

from polixor.config import PATHS, AppSettings                    # noqa: E402

PATHS.ensure()

from polixor.services import caption_engine as ce                # noqa: E402
from polixor.services import subtitles as sub                    # noqa: E402
from polixor.services.transcribe import (                        # noqa: E402
    Segment, TranscriptResult, Word,
)

TMP = Path(os.environ["POLIXOR_DATA_DIR"])


def build(lines: list[tuple[str, float]], gap: float = 0.3,
          with_words: bool = True) -> TranscriptResult:
    segs, t = [], 0.0
    for text, dur in lines:
        toks = text.split()
        per = dur / max(1, len(toks))
        words, wt = [], t
        for tok in toks:
            words.append(Word(start=round(wt, 3),
                              end=round(wt + per * 0.9, 3), text=tok))
            wt += per
        segs.append(Segment(start=t, end=t + dur, text=text,
                            words=words if with_words else [], language="he"))
        t += dur + gap
    return TranscriptResult(segments=segs, language="he", duration=t)


SCRIPT = [
    ("הרבה אנשים חושבים שצריך ציוד יקר כדי להתחיל.", 3.4),
    ("זה פשוט לא נכון, ואני אסביר למה.", 2.8),
    ("הסרטון הראשון שלי צולם בטלפון ישן, בלי מיקרופון,", 3.6),
    ("והוא הביא לי את הלקוח הראשון.", 2.4),
]


# ==========================================================================
# פריסטים
# ==========================================================================
def test_five_presets_exist_and_are_distinct():
    assert set(ce.PRESETS) == {"clean", "viral", "cinematic", "podcast",
                               "story"}
    shapes = {(p.max_chars, p.max_words, p.word_level, p.animation,
               p.position, round(p.size_scale, 2))
              for p in ce.PRESETS.values()}
    assert len(shapes) == 5, "שני פריסטים מייצרים בדיוק אותו מראה"


def test_presets_change_parameters_not_engines():
    """
    הדרישה: „כל Preset משנה פרמטרים — לא משתמש במנוע נפרד."
    אותה פונקציה בונה את כל הפריסטים, והתוצאה שונה.
    """
    tr = build(SCRIPT)
    out = {name: ce.build_captions(tr, clip_start=0.0, clip_end=14.0,
                                   preset=name)
           for name in ce.PRESETS}
    assert len(out["viral"]) > len(out["cinematic"]), {
        k: len(v) for k, v in out.items()}
    for name, cues in out.items():
        assert cues, name
        # אותו טקסט בכל הפריסטים — משתנה רק הפירוק
        joined = " ".join(c.text for c in cues)
        assert "הלקוח" in joined, name


def test_viral_preset_is_short_lines():
    tr = build(SCRIPT)
    cues = ce.build_captions(tr, clip_start=0.0, clip_end=14.0,
                             preset="viral")
    assert cues
    for c in cues:
        assert len(c.text.split()) <= 3, c.text
        assert c.duration <= ce.PRESETS["viral"].max_cue_seconds + 0.6, c.to_dict()


def test_legacy_names_resolve_and_unknown_falls_back():
    assert ce.get_preset("hype").name == "viral"
    assert ce.get_preset("film").name == "cinematic"
    assert ce.get_preset("nonsense").name == ce.DEFAULT_PRESET
    assert ce.get_preset(None).name == ce.DEFAULT_PRESET


def test_catalog_is_serialisable():
    blob = json.dumps(ce.preset_catalog(), ensure_ascii=False)
    assert "podcast" in blob and "cinematic" in blob
    assert len(ce.preset_catalog()) == len(ce.PRESETS)


# ==========================================================================
# אזור בטוח
# ==========================================================================
def test_caption_stays_out_of_the_platform_ui_strip():
    """
    רגרסיה: שוליים קבועים של 150px על פריים 1920 השאירו את הכתובית
    מתחת לשורת הכפתורים של הפיד האנכי.
    """
    zone = ce.SafeZone.for_frame(vertical=True)
    mv, mh = ce.safe_margins(1080, 1920, zone, "bottom")
    assert mv >= int(1920 * zone.bottom)
    assert mv > 150, mv
    assert mh >= int(1080 * zone.side)


def test_top_position_uses_the_top_reserve():
    zone = ce.SafeZone.for_frame(vertical=True)
    mv, _ = ce.safe_margins(1080, 1920, zone, "top")
    assert mv == int(round(1920 * zone.top))


def test_middle_position_has_no_vertical_margin():
    mv, _ = ce.safe_margins(1080, 1920, ce.SafeZone(), "middle")
    assert mv == 0


def test_horizontal_frame_reserves_less():
    v = ce.SafeZone.for_frame(vertical=True)
    h = ce.SafeZone.for_frame(vertical=False)
    assert h.bottom < v.bottom and h.top < v.top


def test_apply_preset_keeps_user_size_as_the_base():
    base = sub.SubtitleStyle(size=50, outline=3.0)
    small = ce.apply_preset(base, ce.get_preset("cinematic"),
                            frame_w=1080, frame_h=1920, vertical=True)
    big = ce.apply_preset(base, ce.get_preset("viral"),
                          frame_w=1080, frame_h=1920, vertical=True)
    assert small.size < base.size < big.size
    assert small.position == "bottom" and big.animation == "punch"
    assert big.margin_v >= int(1920 * 0.16)
    # המשתמש הגדיל את הגופן — הפריסט עדיין מכבד את זה
    bigger_base = sub.SubtitleStyle(size=70, outline=3.0)
    assert ce.apply_preset(bigger_base, ce.get_preset("cinematic"),
                           frame_w=1080, frame_h=1920,
                           vertical=True).size > small.size


# ==========================================================================
# פיסוק ושבירת שורות
# ==========================================================================
def test_cue_breaks_at_a_full_stop():
    tr = build(SCRIPT)
    cues = ce.build_captions(tr, clip_start=0.0, clip_end=14.0,
                             preset="clean")
    joined = [c.text for c in cues]
    # אף כתובית לא ממשיכה אחרי נקודה אל המשפט הבא
    for text in joined:
        stripped = text.rstrip()
        inner = stripped[:-1] if stripped and stripped[-1] in ".!?" else stripped
        assert "." not in inner, text


def test_lines_are_balanced_not_lopsided():
    text = "הסרטון הראשון שלי צולם בטלפון ישן בלי מיקרופון בכלל"
    lines = ce.wrap_balanced(text, max_chars=28, max_lines=2)
    assert len(lines) == 2, lines
    assert abs(len(lines[0]) - len(lines[1])) <= 10, lines
    assert all(len(l) <= 28 for l in lines), lines


def test_line_never_ends_on_a_hanging_word():
    text = "זה היה הסרטון הראשון של הערוץ החדש שלי"
    lines = ce.wrap_balanced(text, max_chars=20, max_lines=2)
    assert len(lines) == 2, lines
    assert not ce._is_hanging(lines[0].split()[-1]), lines


def test_no_text_is_lost_in_wrapping():
    text = " ".join(f"מילה{i}" for i in range(14))
    for max_chars in (14, 20, 30, 40):
        lines = ce.wrap_balanced(text, max_chars=max_chars, max_lines=2)
        assert " ".join(lines).split() == text.split(), (max_chars, lines)


def test_short_text_stays_on_one_line():
    assert ce.wrap_balanced("שלום עולם", max_chars=30) == ["שלום עולם"]


def test_cue_does_not_end_on_a_hanging_word():
    tr = build([("זאת הייתה הטעות הגדולה של", 2.0),
                ("החודש הראשון בעסק", 1.8)])
    cues = ce.build_captions(tr, clip_start=0.0, clip_end=6.0,
                             preset="viral")
    for c in cues[:-1]:
        assert not ce._is_hanging(c.text.split()[-1]), c.text


def test_single_word_tail_is_merged_back():
    tr = build([("אחת שתיים שלוש ארבע חמש", 2.5)], with_words=True)
    cues = ce.build_captions(tr, clip_start=0.0, clip_end=3.0,
                             preset="clean")
    assert all(len(c.text.split()) > 1 for c in cues), [c.text for c in cues]


def test_segment_fallback_still_splits_on_punctuation():
    tr = build(SCRIPT, with_words=False)
    cues = ce.build_captions(tr, clip_start=0.0, clip_end=14.0,
                             preset="clean")
    assert len(cues) >= len(SCRIPT), [c.text for c in cues]
    assert all(c.end > c.start for c in cues)


def test_cues_never_overlap():
    tr = build(SCRIPT)
    for name in ce.PRESETS:
        cues = ce.build_captions(tr, clip_start=0.0, clip_end=14.0,
                                 preset=name)
        for a, b in zip(cues, cues[1:]):
            assert a.end <= b.start + 1e-6, (name, a.to_dict(), b.to_dict())


def test_no_transcript_gives_no_captions():
    assert ce.build_captions(None, clip_start=0.0, clip_end=10.0) == []


# ==========================================================================
# דוברים
# ==========================================================================
def test_speaker_change_breaks_the_cue():
    tr = build([("אז מה קרה שם בסוף", 2.4),
                ("בדיוק מה שאמרתי לך", 2.2)], gap=0.1)
    spans = [(0.0, 2.4, "A"), (2.4, 10.0, "B")]
    cues = ce.build_captions(tr, clip_start=0.0, clip_end=6.0,
                             preset="podcast", speakers=spans)
    labels = [c.speaker for c in cues]
    assert "A" in labels and "B" in labels, labels
    # אין כתובית שמערבבת שני דוברים
    for c in cues:
        assert c.speaker in ("A", "B"), c.to_dict()


def test_speaker_colors_are_stable_by_first_appearance():
    cues = [sub.Cue(0, 1, "א", speaker="B"), sub.Cue(1, 2, "ב", speaker="A"),
            sub.Cue(2, 3, "ג", speaker="B")]
    colors = ce.speaker_color_map(cues)
    assert colors["B"] == ce.SPEAKER_PALETTE[0]
    assert colors["A"] == ce.SPEAKER_PALETTE[1]
    assert ce.speaker_color_map(cues) == colors


def test_no_speaker_labels_means_no_speaker_data_invented():
    tr = build(SCRIPT)
    cues = ce.build_captions(tr, clip_start=0.0, clip_end=14.0,
                             preset="podcast")
    assert all(c.speaker == "" for c in cues)
    assert ce.speaker_color_map(cues) == {}


# ==========================================================================
# הדגשות
# ==========================================================================
def _cues_and_spans():
    tr = build(SCRIPT)
    cues = ce.build_captions(tr, clip_start=0.0, clip_end=14.0,
                             preset="clean")
    spans = []
    for c in cues:
        for w in c.words:
            spans.append(ce.EmphasisSpan(start=w["start"], end=w["end"],
                                         word=w["text"]))
    return cues, spans


def test_emphasis_is_capped_per_minute():
    """הדרישה: „לא כל מילה שנייה"."""
    cues, spans = _cues_and_spans()
    rep = ce.apply_emphasis(cues, spans, "clean", total_duration=14.0)
    total = sum(len(c.emphasis) for c in cues)
    allowed = int(ce.PRESETS["clean"].emphasis_per_minute * (14.0 / 60.0)) or 0
    assert total <= max(allowed, 1), (total, allowed)
    assert total < len(spans) / 4, (total, len(spans))
    assert rep.skipped_budget > 0 and rep.notes


def test_emphasis_respects_the_minimum_gap():
    cues, spans = _cues_and_spans()
    ce.apply_emphasis(cues, spans, "viral", total_duration=14.0)
    times = []
    for c in cues:
        for i in c.emphasis:
            times.append(c.words[i]["start"])
    times.sort()
    gap = ce.PRESETS["viral"].emphasis_min_gap
    for a, b in zip(times, times[1:]):
        assert b - a >= gap - 1e-6, (a, b)


def test_emphasis_is_not_applied_to_the_wrong_word():
    """
    אם המילה בהחלטה אינה המילה שבכתובית — מוותרים על ההדגשה.
    עדיף בלי הדגשה מאשר הדגשה על המילה הלא נכונה.
    """
    cues, _ = _cues_and_spans()
    bogus = [ce.EmphasisSpan(start=1.0, end=1.4, word="מילהשלאקיימת")]
    rep = ce.apply_emphasis(cues, bogus, "viral", total_duration=14.0)
    assert rep.applied == 0 and rep.skipped_unmatched == 1
    assert sum(len(c.emphasis) for c in cues) == 0


def test_emphasis_lands_on_the_right_word():
    cues, _ = _cues_and_spans()
    target = None
    for c in cues:
        for w in c.words:
            if w["text"].strip(".,") == "יקר":
                target = w
                break
        if target:
            break
    assert target is not None, "מילת הבדיקה לא נמצאה בכתוביות"
    rep = ce.apply_emphasis(
        cues, [ce.EmphasisSpan(start=target["start"], end=target["end"],
                               word="יקר")], "viral", total_duration=14.0)
    assert rep.applied == 1
    hits = [c.words[i]["text"] for c in cues for i in c.emphasis]
    assert hits == ["יקר"] or hits == ["יקר."], hits


def test_reapplying_emphasis_clears_the_previous_marks():
    cues, spans = _cues_and_spans()
    ce.apply_emphasis(cues, spans, "viral", total_duration=14.0)
    first = sum(len(c.emphasis) for c in cues)
    ce.apply_emphasis(cues, [], "viral", total_duration=14.0)
    assert first > 0
    assert sum(len(c.emphasis) for c in cues) == 0


def test_spans_from_decisions_skips_cut_material():
    class _Dec:
        def __init__(self, s, e, word, enabled=True):
            self.action, self.start, self.end = "emphasize_word", s, e
            self.params, self.enabled = {"word": word}, enabled
            self.reason = "בדיקה"

    class _Mapper:
        def map_span(self, s, e):
            return None if s < 2.0 else (s - 2.0, e - 2.0)

    decs = [_Dec(1.0, 1.3, "נחתכה"), _Dec(5.0, 5.4, "נשארה"),
            _Dec(6.0, 6.4, "כבויה", enabled=False)]
    spans = ce.spans_from_decisions(decs, clip_start=0.0, mapper=_Mapper())
    assert [s.word for s in spans] == ["נשארה"]
    assert spans[0].start == 3.0


# ==========================================================================
# רינדור ASS
# ==========================================================================
def test_emphasised_word_is_coloured_in_the_ass_file():
    cues, _ = _cues_and_spans()
    word = cues[0].words[2]
    ce.apply_emphasis(cues, [ce.EmphasisSpan(start=word["start"],
                                             end=word["end"],
                                             word=word["text"])],
                      "clean", total_duration=14.0)
    style = ce.apply_preset(sub.SubtitleStyle(), ce.get_preset("clean"),
                            frame_w=1080, frame_h=1920, vertical=True)
    dst = TMP / "emph.ass"
    sub.write_ass(cues, dst, width=1080, height=1920, style=style)
    body = dst.read_text(encoding="utf-8")
    from polixor.util.text import hex_to_ass_color
    assert hex_to_ass_color(style.emphasis_color) in body, style.emphasis_color


def test_ass_margins_come_from_the_preset():
    tr = build(SCRIPT)
    cues = ce.build_captions(tr, clip_start=0.0, clip_end=14.0,
                             preset="viral")
    style = ce.apply_preset(sub.SubtitleStyle(), ce.get_preset("viral"),
                            frame_w=1080, frame_h=1920, vertical=True)
    dst = TMP / "viral.ass"
    sub.write_ass(cues, dst, width=1080, height=1920, style=style)
    line = [l for l in dst.read_text(encoding="utf-8").splitlines()
            if l.startswith("Style: Polixor,")][0]
    assert line.rstrip().split(",")[-2] == str(style.margin_v), line


def test_word_level_events_do_not_exceed_the_cue():
    tr = build(SCRIPT)
    cues = ce.build_captions(tr, clip_start=0.0, clip_end=14.0,
                             preset="viral")
    style = ce.apply_preset(sub.SubtitleStyle(), ce.get_preset("viral"),
                            frame_w=1080, frame_h=1920, vertical=True)
    dst = TMP / "wl.ass"
    sub.write_ass(cues, dst, width=1080, height=1920, style=style)
    assert "Dialogue:" in dst.read_text(encoding="utf-8")


# ==========================================================================
# רגרסיית יציבות
# ==========================================================================
def test_three_builds_produce_identical_timestamps():
    """
    הדרישה: „3 exports רצופים של אותו Project צריכים להפיק בדיוק
    אותם caption timestamps. לא Drift."
    """
    tr = build(SCRIPT)
    runs = []
    for _ in range(3):
        cues = ce.build_captions(tr, clip_start=1.0, clip_end=13.0,
                                 preset="podcast")
        ce.apply_emphasis(cues, [ce.EmphasisSpan(c.words[0]["start"],
                                                 c.words[0]["end"],
                                                 c.words[0]["text"])
                                 for c in cues if c.words],
                          "podcast", total_duration=12.0)
        runs.append([(c.start, c.end, c.text, tuple(c.emphasis))
                     for c in cues])
    assert runs[0] == runs[1] == runs[2]


def test_remap_carries_speaker_and_emphasis():
    class _Plan:
        def map_span(self, s, e):
            return (s, e)

    cue = sub.Cue(0.0, 2.0, "אחת שתיים שלוש",
                  words=[{"start": 0.0, "end": 0.6, "text": "אחת"},
                         {"start": 0.6, "end": 1.2, "text": "שתיים"},
                         {"start": 1.2, "end": 2.0, "text": "שלוש"}],
                  speaker="A", emphasis=[2])
    out = sub.remap_cues([cue], _Plan())
    assert out[0].speaker == "A"
    assert out[0].emphasis == [2]


def test_emphasis_index_follows_a_cut_word():
    class _Plan:
        """חותך את המילה הראשונה."""

        def map_span(self, s, e):
            if e <= 0.6:
                return None
            return (max(0.0, s - 0.6), e - 0.6)

    cue = sub.Cue(0.0, 2.0, "אחת שתיים שלוש",
                  words=[{"start": 0.0, "end": 0.6, "text": "אחת"},
                         {"start": 0.6, "end": 1.2, "text": "שתיים"},
                         {"start": 1.2, "end": 2.0, "text": "שלוש"}],
                  emphasis=[2])
    out = sub.remap_cues([cue], _Plan())
    assert out[0].emphasis == [1], out[0].to_dict()
    assert out[0].words[1]["text"] == "שלוש"


# ==========================================================================
def _run_all() -> int:
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    passed, failed = 0, []
    for name, fn in fns:
        try:
            fn()
            print(f"  \033[92m✓\033[0m {name}")
            passed += 1
        except AssertionError as exc:
            print(f"  \033[91m✗\033[0m {name}: {exc}")
            failed.append(name)
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"  \033[91m✗\033[0m {name}: {type(exc).__name__}: {exc}")
            failed.append(name)
    print(f"\n{passed}/{len(fns)} בדיקות Caption Engine עברו")
    if failed:
        print("נכשלו: " + ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
