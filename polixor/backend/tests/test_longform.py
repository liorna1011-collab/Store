"""
בדיקות למצב Long-Form (services/longform.py).

התמלולים סינתטיים ובנויים כך שכל סוג תוכן ידוע מראש: בלוקים של הנושא
הראשי, פטפוט off-topic (חסות, תקלה טכנית), AFK של דקות, בלוק שחוזר
כמעט מילה במילה, קטעים עמוסי מילוי ושיא. כך אפשר לבדוק שכל אחד מהם
מטופל נכון, ולא רק ש„יצא משהו".

הרצה:  python3 tests/test_longform.py
"""

from __future__ import annotations

import os
import random
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxlong_"))

from polixor.services import longform as lf                        # noqa: E402
from polixor.services.transcribe import Segment, TranscriptResult, Word  # noqa: E402

WPS = 2.6        # קצב דיבור: מילים בשנייה

EN = {
    "topics": [
        ["graphics card", "gpu", "vram", "ray tracing", "frame rate", "drivers", "benchmark"],
        ["water cooling", "radiator", "pump", "fans", "temperatures", "thermal paste", "airflow"],
        ["mechanical keyboard", "switches", "keycaps", "stabilizers", "lubing", "typing", "layout"],
        ["monitor", "refresh rate", "panel", "response time", "hdr", "resolution", "calibration"],
    ],
    "verbs": ["tested", "compared", "measured", "installed", "tuned", "checked", "replaced",
              "configured", "upgraded", "explained"],
    "glue": ["because", "which means", "and then", "so basically", "after that", "while"],
    "tails": ["under load", "for an hour", "at stock settings", "with the new build",
              "on the bench", "in this chassis", "for the second time", "with our settings"],
    "offtopic": [
        "this stream is sponsored by our friends and you can use the promo code in the description",
        "can you hear me now the stream crashed and the audio is broken sorry guys",
        "thanks for the follow and thanks for the donation welcome everyone who just joined",
        "hold on I am getting water give me a sec my mic keeps cutting out",
    ],
    "afk": "brb guys be right back",
    "filler": "um uh like you know um uh basically like um",
    "peak": "no way this is insane I cannot believe the benchmark just broke the world record",
}
HE = {
    "topics": [
        ["כרטיס מסך", "מעבד גרפי", "זיכרון וידאו", "מעקב קרניים", "קצב פריימים", "דרייברים", "בנצ'מרק"],
        ["קירור מים", "רדיאטור", "משאבה", "מאווררים", "טמפרטורות", "משחה תרמית", "זרימת אוויר"],
        ["מקלדת מכנית", "מתגים", "מקשים", "מייצבים", "שימון", "הקלדה", "פריסה"],
        ["מסך", "קצב רענון", "פאנל", "זמן תגובה", "טווח דינמי", "רזולוציה", "כיול"],
    ],
    "verbs": ["בדקנו", "השווינו", "מדדנו", "התקנו", "כיוונו", "החלפנו", "הגדרנו", "שדרגנו",
              "הסברנו", "ניסינו"],
    "glue": ["כי", "ולכן", "ואחר כך", "ובעצם", "ואז", "בזמן ש"],
    "tails": ["תחת עומס", "במשך שעה", "בהגדרות היצרן", "במחשב החדש", "על השולחן",
              "במארז הזה", "בפעם השנייה", "עם ההגדרות שלנו"],
    "offtopic": [
        "הסטרים הזה בחסות החברים שלנו ויש קוד הנחה בלינק בתיאור",
        "שומעים אותי עכשיו הסטרים נפל והסאונד נפל סליחה חברים",
        "תודה על העוקבים ותודה על הדונייט ברוכים הבאים לכל מי שהצטרף",
        "רגע אני מביא מים המיקרופון שלי ממשיך לקפוץ",
    ],
    "afk": "אני כבר חוזר תנו לי דקה",
    "filler": "אה אממ כאילו יעני אה אממ בעצם כאילו אה",
    "peak": "אין מצב זה מטורף לא האמנתי שהבנצ'מרק שבר את שיא העולם",
}


class Builder:
    def __init__(self, lang: str, seed: int = 7) -> None:
        self.lang, self.t, self.segs = lang, 0.0, []
        self.rng = random.Random(seed)
        self.marks: dict[str, list[tuple[float, float]]] = {}

    def say(self, text: str, tag: str = "") -> None:
        toks = text.split()
        dur = len(toks) / WPS
        words, t = [], self.t
        for tok in toks:
            words.append(Word(start=round(t, 3), end=round(t + 0.9 / WPS, 3), text=tok))
            t += 1 / WPS
        self.segs.append(Segment(start=round(self.t, 3), end=round(self.t + dur, 3),
                                 text=text, words=words, language=self.lang))
        if tag:
            self.marks.setdefault(tag, []).append((self.t, self.t + dur))
        self.t += dur + self.rng.uniform(0.25, 0.9)

    def silence(self, seconds: float, tag: str = "") -> None:
        if tag:
            self.marks.setdefault(tag, []).append((self.t, self.t + seconds))
        self.t += seconds

    def topic_sentence(self, bank: dict, topic: int) -> str:
        r = self.rng
        a, b = r.sample(bank["topics"][topic], 2)
        if self.lang == "he":
            return (f"{r.choice(bank['verbs'])} את {a} {r.choice(bank['tails'])} "
                    f"{r.choice(bank['glue'])} {b} {r.choice(bank['tails'])}.")
        return (f"we {r.choice(bank['verbs'])} the {a} {r.choice(bank['tails'])} "
                f"{r.choice(bank['glue'])} the {b} {r.choice(bank['tails'])}.")

    def block(self, bank: dict, topic: int, seconds: float, tag: str = "") -> list[str]:
        said, end = [], self.t + seconds
        while self.t < end:
            s = self.topic_sentence(bank, topic)
            said.append(s)
            self.say(s, tag or f"topic{topic}")
        return said

    def result(self) -> TranscriptResult:
        return TranscriptResult(segments=self.segs, language=self.lang, duration=self.t)


def stream(lang: str, *, scale: float = 1.0) -> Builder:
    """כ-80 דקות של „סטרים" עם כל סוגי התוכן."""
    bank = HE if lang == "he" else EN
    b = Builder(lang)
    b.block(bank, 0, 600 * scale)
    for s in bank["offtopic"][:2]:
        b.say(s, "offtopic")
        b.say(s.split(" ", 3)[-1], "offtopic")
    repeated = b.block(bank, 1, 420 * scale)
    b.say(bank["afk"], "afk")
    b.silence(300 * scale, "afk")                 # 5 דקות AFK
    for _ in range(int(20 * scale) or 1):
        b.say(bank["filler"], "filler")
    b.block(bank, 2, 540 * scale)
    b.say(bank["peak"], "peak")
    b.say(bank["peak"].replace("world record", "record"), "peak")
    for s in bank["offtopic"][2:]:
        b.say(s, "offtopic")
        b.say(s.split(" ", 2)[-1], "offtopic")
    # הבלוק מהנושא השני חוזר כמעט מילה במילה
    for s in repeated[: max(4, len(repeated) // 2)]:
        b.say(s.rstrip(".") + " again.", "repeat")
    b.block(bank, 3, 600 * scale)
    return b


def overlap(spans, marks) -> float:
    tot = 0.0
    for a, b in spans:
        for c, d in marks:
            tot += max(0.0, min(b, d) - max(a, c))
    return tot


def plan_for(lang: str, target: float = 900, scale: float = 1.0):
    b = stream(lang, scale=scale)
    tr = b.result()
    plan = lf.plan_longform(tr, duration=tr.duration,
                            settings=lf.LongformSettings(target_seconds=target), language=lang)
    return b, plan


# ==========================================================================
def test_hits_target_within_tolerance_he_en():
    for lang in ("he", "en"):
        _b, plan = plan_for(lang, target=900)
        lo, hi = 900 * 0.88, 900 * 1.12
        assert lo <= plan.output_seconds <= hi, (lang, plan.output_seconds, plan.stats)
        assert plan.stats["within_tolerance"] is True


def test_dead_air_and_afk_never_kept():
    for lang in ("he", "en"):
        b, plan = plan_for(lang)
        assert overlap(plan.segments, b.marks["afk"]) < 1.0, lang
        # אין שתיקה ארוכה בתוך חלק שנשמר
        units = {(round(s.start, 2)) for s in b.segs}
        assert units
        for a, e in plan.segments:
            inside = sorted((s for s in b.segs if s.start >= a - 0.5 and s.end <= e + 0.5),
                            key=lambda s: s.start)
            for x, y in zip(inside, inside[1:]):
                assert y.start - x.end < 2.0 + 1e-6, (lang, x.end, y.start)
        assert plan.removed_seconds()["dead_air"] >= 290, plan.removed_seconds()


def test_off_topic_chatter_removed():
    for lang in ("he", "en"):
        b, plan = plan_for(lang)
        kept = overlap(plan.segments, b.marks["offtopic"])
        total = sum(e - a for a, e in b.marks["offtopic"])
        assert kept <= 0.1 * total, (lang, kept, total)


def test_repetition_removed_first_occurrence_kept_eligible():
    for lang in ("he", "en"):
        b, plan = plan_for(lang)
        kept = overlap(plan.segments, b.marks["repeat"])
        assert kept < 1.0, (lang, kept)
        assert plan.removed_seconds()["repetition"] > 0, plan.removed_seconds()


def test_filler_removed():
    for lang in ("he", "en"):
        b, plan = plan_for(lang)
        assert overlap(plan.segments, b.marks["filler"]) < 1.0, lang


def test_every_main_topic_is_covered_in_order():
    for lang in ("he", "en"):
        b, plan = plan_for(lang)
        for t in range(4):
            got = overlap(plan.segments, b.marks[f"topic{t}"])
            assert got >= 60, (lang, t, got)
        starts = [a for a, _ in plan.segments]
        assert starts == sorted(starts)
        assert all(e - a >= 1.0 for a, e in plan.segments)


def test_chapters_titles_from_speech():
    for lang in ("he", "en"):
        b, plan = plan_for(lang)
        assert len(plan.chapters) >= 3, (lang, [c.title for c in plan.chapters])
        assert plan.chapters[0].start == 0.0
        spoken = " ".join(s.text for s in b.segs)
        for c in plan.chapters:
            words = c.title.rstrip("…").split()
            assert words and all(w.strip(",.") in spoken for w in words), (lang, c.title)
        text = plan.youtube_chapters_text()
        assert text.splitlines()[0].startswith("0:00 "), text


def test_short_source_is_explained_not_padded():
    b, plan = plan_for("en", target=1800, scale=0.4)
    assert plan.output_seconds < 1800 * 0.88
    assert plan.stats["short_source"] is True
    assert overlap(plan.segments, b.marks["afk"]) < 1.0
    lines = plan.explain("en")
    assert any("shorter than the target" in ln for ln in lines), lines
    assert any("היעד" in ln for ln in plan.explain("he"))


def test_roundtrip_dict():
    _b, plan = plan_for("he")
    again = lf.LongformPlan.from_dict(plan.to_dict())
    assert again.to_dict() == plan.to_dict()


def test_four_hour_transcript_is_fast():
    b = Builder("en", seed=3)
    for rep in range(4):                           # 4 × ~60 דקות
        for t in range(4):
            b.block(EN, t, 840)
        b.say(EN["offtopic"][rep % 4], "offtopic")
        b.silence(120, "afk")
    tr = b.result()
    words = sum(len(s.words) for s in tr.segments)
    assert tr.duration > 3.5 * 3600 and words > 30000, (tr.duration, words)
    t0 = time.perf_counter()
    plan = lf.plan_longform(tr, duration=tr.duration,
                            settings=lf.LongformSettings(target_seconds=1200), language="en")
    elapsed = time.perf_counter() - t0
    assert elapsed < 10.0, f"{elapsed:.1f}s"
    assert 1200 * 0.88 <= plan.output_seconds <= 1200 * 1.12, plan.output_seconds


def test_no_transcript_falls_back_to_timeline_honestly():
    import numpy as np

    from polixor.services.scoring import Timeline

    n = 36000
    tl = Timeline(hop=0.1, duration=3600.0, times=np.arange(n, dtype=np.float32) * 0.1,
                  score=np.random.default_rng(1).random(n).astype(np.float32))
    plan = lf.plan_longform(None, duration=3600.0, timeline=tl,
                            settings=lf.LongformSettings(target_seconds=600))
    assert plan.stats.get("no_transcript") is True
    assert 0 < plan.output_seconds <= 600 * 1.12
    assert any("transcript" in ln for ln in plan.explain("en"))


# ==========================================================================
def _run_all() -> int:
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    passed, failed = 0, []
    for name, fn in fns:
        t0 = time.perf_counter()
        try:
            fn()
            print(f"  \033[92m✓\033[0m {name} ({time.perf_counter() - t0:.1f}s)")
            passed += 1
        except AssertionError as exc:
            print(f"  \033[91m✗\033[0m {name}: {exc}")
            failed.append(name)
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"  \033[91m✗\033[0m {name}: {type(exc).__name__}: {exc}")
            failed.append(name)
    print(f"\n{passed}/{len(fns)} בדיקות Long-Form עברו")
    if failed:
        print("נכשלו: " + ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
