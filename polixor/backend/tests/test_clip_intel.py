"""
בדיקות למנוע בחירת הקליפים (services/clip_intel): Hook → Context → Payoff.

כל בדיקה בונה תמלול אמיתי (משפטים עם תזמון ברמת מילה) וציר זמן עם
ערוץ קולי (שיאי תגובה במקומות ידועים), ומריצה את המנוע המלא.

הרצה:  python3 tests/test_clip_intel.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxintel_"))

import numpy as np                                                   # noqa: E402

from polixor import i18n                                             # noqa: E402
from polixor.config import AppSettings                               # noqa: E402
from polixor.services import clip_intel, lang                        # noqa: E402
from polixor.services.clip_intel.units import LocalNorm, build_units  # noqa: E402
from polixor.services.scoring import Timeline, _fuse                 # noqa: E402
from polixor.services.transcribe import Segment, TranscriptResult, Word  # noqa: E402

HOP = 0.1


# --------------------------------------------------------------------------
# בניית קלט
# --------------------------------------------------------------------------
def transcript(lines: list[tuple[float, float, str]], language: str = "he",
               prob: float = 0.92) -> TranscriptResult:
    segs = []
    for start, end, text in lines:
        toks = text.split()
        step = (end - start) / max(1, len(toks))
        words = [Word(start=round(start + i * step, 3), end=round(start + (i + 1) * step - 0.02, 3),
                      text=t, probability=prob) for i, t in enumerate(toks)]
        segs.append(Segment(start=start, end=end, text=text, words=words, language=language))
    dur = (lines[-1][1] + 5.0) if lines else 10.0
    return TranscriptResult(segments=segs, language=language, duration=dur, provider="test")


def timeline(duration: float, spikes: list[tuple[float, float, float]] = (),
             base: float = 0.12, seed: int = 1) -> Timeline:
    """ערוץ קולי: רעש רקע נמוך + שיאים [(מ, עד, עוצמה)]."""
    n = int(np.ceil(duration / HOP))
    rng = np.random.default_rng(seed)
    vocal = np.clip(base + rng.normal(0, 0.02, n), 0, 1).astype(np.float32)
    for a, b, v in spikes:
        vocal[int(a / HOP): int(b / HOP)] = v
    tl = Timeline(hop=HOP, duration=duration, times=(np.arange(n) * HOP).astype(np.float32))
    tl.vocal = vocal
    z = np.zeros(n, dtype=np.float32)
    tl.speech, tl.visual, tl.pause, tl.chat = z.copy(), z.copy(), z.copy(), z.copy()
    tl.speech_mask = np.zeros(n, dtype=bool)
    tl.score = _fuse(tl, AppSettings())
    return tl


def settings(**kw) -> AppSettings:
    base = AppSettings().to_dict()
    base.update({"short_min_seconds": 10, "short_max_seconds": 45})
    base.update(kw)
    return AppSettings.from_dict(base)


def run(lines, spikes=(), *, language="he", limit=5, **kw):
    tr = transcript(lines, language)
    tl = timeline(tr.duration, spikes)
    with i18n.use_lang("en"):
        return clip_intel.select_short_clips(tl, tr, settings=settings(**kw),
                                             language=language, limit=limit), tr


# סיפור עברי מלא: פתיחה → הקשר → פאנץ' → תגובה (צחוק אחרי 38.5)
STORY = [
    (10.0, 14.0, "טוב אז אני סתם מסדר פה את ההגדרות"),
    (14.2, 17.5, "והמיקרופון קצת נמוך אז אני מעלה אותו"),
    (19.5, 23.5, "תקשיבו, אני חייב לספר לכם מה קרה לי אתמול בערב"),
    (24.0, 28.5, "הלכתי לסופר לקנות חלב ולחם כמו כל יום רגיל"),
    (29.0, 33.5, "ובקופה הקופאית מסתכלת עליי ושואלת אם אני הסטרימר"),
    (34.0, 38.5, "ובסוף התברר שהיא צופה בכל שידור שלי כבר שנתיים!"),
    (38.7, 40.0, "חחחח אין מצב"),
    (43.5, 47.5, "טוב נחזור למשחק ונראה מה קורה בשלב הבא"),
    (48.0, 52.0, "אני בונה פה את הבסיס ומעלה את הרמה"),
]
STORY_SPIKES = [(38.5, 40.5, 0.95)]


# --------------------------------------------------------------------------
# בדיקות
# --------------------------------------------------------------------------
def test_finds_the_story_with_hook_context_payoff():
    res, tr = run(STORY, STORY_SPIKES)
    assert res is not None and len(res.selected) == 1, [(c.start, c.end) for c in res.selected]
    c = res.selected[0]
    rec = res.review["selected"][0]
    assert rec["hook"]["text"].startswith("תקשיבו"), rec["hook"]["text"]
    assert "התברר" in rec["payoff"]["text"]
    assert "חחחח" in rec["payoff"]["tail"]                    # התגובה נכללת
    assert 19.2 <= c.start <= 19.5 and c.end >= 40.0, (c.start, c.end)
    assert c.end < 43.5                                        # לא נכנס למשפט הבא
    keys = {r["key"] for r in rec["payoff"]["reasons"]}
    assert "why.payoff_marker" in keys and "why.reaction_after" in keys
    assert c.quality["engine"] == "clip_intel" and c.quality["final"] == c.score


def test_boring_video_returns_no_clips():
    """איכות לפני כמות: סרטון בלי אף סיפור מחזיר אפס, לא 5 קליפים גרועים."""
    lines = [(i * 5.0, i * 5.0 + 4.2, t) for i, t in enumerate([
        "אני מעלה פה את הרמה של הדמות", "עכשיו אני הולך לחנות לקנות ציוד",
        "יש פה כמה פריטים שאפשר לשפר", "אני בודק את המפה בצד ימין",
        "נמשיך לאסוף משאבים בינתיים", "הדמות צריכה עוד קצת ניסיון",
        "אני שם את זה בתיק ומתקדם", "עוד כמה שלבים ואז נראה",
    ] * 3)]
    res, _ = run(lines, [])
    assert res is not None
    assert res.selected == [], [(c.start, c.end, c.score) for c in res.selected]
    assert any("No moment passed the quality bar" in n for n in res.notes), res.notes


def test_limit_is_a_cap_not_a_target():
    texts = [
        ("תקשיבו, אני חייב לספר לכם מה קרה לי אתמול בסופר",
         "עמדתי בתור עם עגלה מלאה בירקות ופירות", "הקופאית הסתכלה עליי ושאלה על הערוץ",
         "ובסוף התברר שהיא צופה בכל שידור שלי!"),
        ("אתם לא תאמינו מה קרה במשחק של שבת בערב", "השוער שלנו נפצע והכניסו את המאמן",
         "הוא בן שישים ולא שיחק עשרים שנה", "ובסוף התברר שהוא עצר פנדל בדקה האחרונה!"),
        ("פעם אחת נסעתי לאילת עם החברים מהצבא", "הרכב התקלקל באמצע המדבר בצהריים",
         "חיכינו שלוש שעות לגרר בחום של ארבעים", "ובסוף התברר שפשוט נגמר לנו הדלק!"),
    ]
    stories, spikes = [], []
    for k, t in enumerate(texts):
        o = k * 100.0
        stories += [(o + 19.5, o + 23.5, t[0]), (o + 24.0, o + 28.5, t[1]),
                    (o + 29.0, o + 33.5, t[2]), (o + 34.0, o + 38.5, t[3]),
                    (o + 38.7, o + 40.0, "חחחח אין מצב")]
        spikes.append((o + 38.5, o + 40.5, 0.95))
    res, _ = run(stories, spikes, limit=2)
    assert len(res.selected) == 2
    over = [r for r in res.review["near_misses"] if r["rejection"]["key"] == "reject.over_limit"]
    assert len(over) == 1


def test_never_reorders_or_fabricates():
    """הקליפ הוא טווח רציף; הוו הוא המשפט הראשון בטווח; שום משפט לא זז."""
    res, tr = run(STORY, STORY_SPIKES)
    c = res.selected[0]
    rec = res.review["selected"][0]
    inside = [s for s in tr.segments if s.end > c.start and s.start < c.end]
    assert inside[0].text == rec["hook"]["text"], (inside[0].text, rec["hook"]["text"])
    assert rec["hook"]["start"] <= rec["payoff"]["start"]
    # התחלה בתחילת משפט: לא בתוך המשפט הקודם ולא אחרי המילה הראשונה
    prev = [s for s in tr.segments if s.end <= inside[0].start][-1]
    assert prev.end <= c.start <= inside[0].words[0].start


def test_continuous_burst_is_not_cut():
    lines = [
        (40.0, 45.0, "תקשיבו, אני רוצה לספר לכם משהו שלא סיפרתי לאף אחד"),
        (45.5, 48.5, "לפני שנה חשבתי לעזוב את הסטרימינג לגמרי"),
        (49.0, 49.6, "וואו!"), (49.66, 50.8, "בדיוק עכשיו?"),
        (50.86, 51.5, "ניצחתי!"), (51.56, 53.4, "עשיתי את זה!"),
        (54.0, 58.0, "טוב בואו נמשיך בשידור ונראה מה עוד יש"),
    ]
    res, _ = run(lines, [(49.0, 53.5, 0.95)])
    assert res.selected, res.review["near_misses"]
    c = res.selected[0]
    assert c.end >= 53.4 and c.end < 54.0, c.end


def test_mid_thought_and_backref_openings_are_penalised():
    lines = [
        (10.0, 14.0, "אבל אז הוא אמר לי משהו שלא האמנתי ששמעתי"),
        (14.5, 19.0, "כמו שאמרתי קודם זה היה ממש בקניון הגדול"),
        (19.5, 24.0, "ובסוף התברר שהוא בכלל אח של השכן שלי!"),
        (24.2, 25.5, "חחחח אין מצב"),
    ]
    res, _ = run(lines, [(24.0, 26.0, 0.95)], short_min_seconds=8)
    records = res.review["selected"] + res.review["near_misses"]
    assert records
    # כל נקודת התחלה אפשרית כאן בעייתית, והרשומה אומרת למה
    for rec in records:
        pens = {p["key"] for p in rec["penalties"]}
        assert pens & {"starts_mid_thought", "needs_earlier_context", "no_setup"}, pens
    for rec in records:
        probs = {p["key"] for p in rec["hook"]["problems"]}
        assert probs & {"problem.starts_mid_thought", "problem.needs_earlier_context",
                        "problem.starts_with_conclusion"}, probs
    # ופתיחה ב„אבל…" / „כמו שאמרתי…" מסומנת ישירות
    from polixor.services.clip_intel.story import opening_quality
    units = build_units(transcript(lines), timeline(30.0), "he")
    assert "starts_mid_thought" in opening_quality(units[0], None)[2]
    assert "needs_earlier_context" in opening_quality(units[1], units[0])[2]


def test_same_joke_twice_is_a_duplicate():
    joke = [("תקשיבו, אני חייב לספר לכם את הבדיחה על הפינגווין במקרר"),
            ("הפינגווין פותח את המקרר ורואה שם דוב קוטב שמסתכל עליו"),
            ("ובסוף התברר שהדוב בכלל חיכה למשלוח של גלידה מהפינגווין!")]
    lines = [(10.0, 14.0, joke[0]), (14.5, 19.0, joke[1]), (19.5, 24.0, joke[2]),
             (24.2, 25.5, "חחחח אין מצב"),
             (30.0, 34.0, "אני ממשיך לשחק פה בשקט ואוסף עוד משאבים"),
             (610.0, 614.0, joke[0]), (614.5, 619.0, joke[1]), (619.5, 624.0, joke[2]),
             (624.2, 625.5, "חחחח אין מצב")]
    res, _ = run(lines, [(24.0, 26.0, 0.95), (624.0, 626.0, 0.9)], short_min_seconds=8)
    assert len(res.selected) == 1, [(c.start, c.end) for c in res.selected]
    dups = res.review["duplicates"]
    assert len(dups) == 1 and dups[0]["rejection"]["key"] == "reject.same_content"


def test_off_topic_is_rejected():
    lines = [
        (10.0, 14.0, "תקשיבו, יש לנו חסות מטורפת היום בשידור"),
        (14.5, 19.0, "קוד הנחה בלינק בתיאור לכל מי שרוצה"),
        (19.5, 23.0, "וואו! אין מצב! קוד קופון של חמישים אחוז!"),
        (23.2, 24.5, "חחחח"),
    ]
    res, _ = run(lines, [(23.0, 25.0, 0.95)], short_min_seconds=8)
    assert res.selected == []


def test_quality_bar_is_absolute_and_configurable():
    res_hi, _ = run(STORY, STORY_SPIKES, clip_min_quality=0.9)
    res_lo, _ = run(STORY, STORY_SPIKES, clip_min_quality=0.5)
    assert len(res_lo.selected) == 1 and len(res_hi.selected) == 0
    assert res_hi.review["threshold"] == 0.9
    near = res_hi.review["near_misses"]
    assert near and near[0]["rejection"]["key"] == "reject.below_quality_bar"


def test_review_record_is_complete():
    res, _ = run(STORY, STORY_SPIKES)
    rec = res.review["selected"][0]
    for key in ("proposed_by", "hook", "context", "payoff", "components", "penalties",
                "final_score", "boundaries", "overlapping_alternatives"):
        assert key in rec, key
    assert rec["boundaries"]["start_reason"]["text"] and rec["boundaries"]["end_reason"]["text"]
    assert rec["context"]["sentences"] >= 1 and rec["context"]["seconds"] > 0
    comps = {c["key"] for c in rec["components"]}
    assert comps == {"hook", "payoff", "completeness", "arc", "density", "signal"}
    st = res.review["stats"]
    assert st["selected"] == 1 and st["stories"] >= 1 and st["sentences"] == len(STORY)


def test_insight_payoff_and_natural_pauses():
    """
    תוכן מלמד/סיפורי: הפאנץ' הוא תובנה („למדתי את השיעור"), לא צחוק.
    הפסקות טבעיות של ~1.4 ש׳ בין משפטים אינן "אוויר מת".
    """
    from polixor.services.clip_intel import analyze_stories

    lines = ["שלוש טעויות שעשיתי בשנה הראשונה", "הראשונה הייתה שלא ביקשתי עזרה",
             "וזה עלה לי בחצי שנה של עבודה", "זה היה הרגע הכי קשה שעברתי",
             "אבל ממנו למדתי את השיעור הגדול"]
    L = [(10.0 + i * 4.0, 10.0 + i * 4.0 + 2.6, t) for i, t in enumerate(lines)]
    tr = transcript(L)
    an = analyze_stories(timeline(tr.duration), tr, settings=settings(short_min_seconds=15),
                         language="he")
    best = max(an.stories, key=lambda s: s.final)
    assert an.units[best.proposal.payoff_idx].text.startswith("אבל ממנו למדתי")
    assert "payoff_marker" in best.payoff_reasons
    assert "dead_air" not in best.penalties, best.penalties
    # שקט אמיתי (5 ש׳) בין המשפטים כן נקנס
    L2 = [(10.0 + i * 7.6, 10.0 + i * 7.6 + 2.6, t) for i, t in enumerate(lines)]
    tr2 = transcript(L2)
    an2 = analyze_stories(timeline(tr2.duration), tr2,
                          settings=settings(short_min_seconds=15, short_max_seconds=60),
                          language="he")
    best2 = max(an2.stories, key=lambda s: s.final)
    assert "dead_air" in best2.penalties


def test_hebrew_explanations():
    tr = transcript(STORY)
    tl = timeline(tr.duration, STORY_SPIKES)
    with i18n.use_lang("he"):
        res = clip_intel.select_short_clips(tl, tr, settings=settings(), language="he", limit=5)
    assert "וו:" in res.selected[0].reason and "פאנץ'" in res.selected[0].reason


def test_english_story():
    lines = [
        (5.0, 9.0, "okay let me fix the settings real quick"),
        (12.0, 16.0, "let me tell you what happened to me yesterday"),
        (16.5, 21.0, "i went to the store to buy milk like every day"),
        (21.5, 26.0, "and the cashier looks at me and asks if i stream"),
        (26.5, 31.0, "turns out she watched every single stream for two years!"),
        (31.2, 32.5, "hahaha no way"),
        (36.0, 40.0, "okay back to the game let's see the next level"),
    ]
    res, _ = run(lines, [(31.0, 33.0, 0.95)], language="en")
    assert len(res.selected) == 1
    rec = res.review["selected"][0]
    assert rec["hook"]["text"].startswith("let me tell you")
    assert "turns out" in rec["payoff"]["text"]


def test_no_transcript_returns_none():
    tl = timeline(60.0, [])
    empty = TranscriptResult(segments=[], language="he", duration=60.0, provider="none")
    assert clip_intel.select_short_clips(tl, empty, settings=settings(), language="he",
                                         limit=5) is None


def test_local_normalisation_finds_reactions_in_a_quiet_hour():
    """שעה שקטה ואחריה שעה רועשת: תגובה בשעה השקטה עדיין מזוהה."""
    dur = 7200.0
    n = int(dur / HOP)
    vocal = np.full(n, 0.08, dtype=np.float32)
    vocal[int(3600 / HOP):] = 0.7                       # שעה רועשת
    vocal[int(1000 / HOP): int(1002 / HOP)] = 0.4       # תגובה בשעה השקטה
    norm = LocalNorm(vocal, HOP)
    assert norm.rel(0.4, 1001.0) > 0.9
    assert norm.rel(0.7, 5000.0) < 0.2                  # רעש קבוע אינו תגובה


def test_prefix_does_not_create_false_reactions():
    he = lang.HEBREW
    assert he.count("טוב, בואו נירגע רגע ונמשיך", he.reaction_tokens) == 0
    assert he.count("חחחח אין מצב", he.reaction_tokens) >= 2


def test_candidate_records_survive_storage():
    from polixor.services import analysis_store

    res, _ = run(STORY, STORY_SPIKES)
    d = Path(tempfile.mkdtemp())
    path = analysis_store.save_candidates(d, {"short": res.selected})
    back = analysis_store.load_candidates(path)
    assert back["short"][0].quality["review_id"] == res.selected[0].quality["review_id"]
    rp = analysis_store.save_clip_review(d, res.review)
    assert analysis_store.load_clip_review(rp)["stats"]["selected"] == 1
    # רשומה ישנה בלי השדה quality עדיין נטענת
    import json
    data = json.loads(path.read_text("utf-8"))
    data["short"][0].pop("quality")
    path.write_text(json.dumps(data), "utf-8")
    assert analysis_store.load_candidates(path)["short"][0].quality == {}


def test_units_carry_asr_confidence():
    tr = transcript(STORY, prob=0.3)
    tl = timeline(tr.duration, STORY_SPIKES)
    units = build_units(tr, tl, "he")
    assert all(u.low_conf == 1.0 for u in units)


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
    print(f"\n{passed}/{len(fns)} בדיקות מנוע בחירת קליפים עברו")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
