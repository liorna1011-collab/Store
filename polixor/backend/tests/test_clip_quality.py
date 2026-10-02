"""
בדיקות איכות לבחירת קליפים: וו אמיתי, סיפור שלם, משמעת בחירה ו-recall.

הקלטים כאן *סינתטיים*: הם משחזרים את דפוסי הכשל שנמצאו בבדיקה על שידור
אמיתי (שאלה שגרתית כ"וו", צעקה כ"פאנץ'", פנייה פרטית, פתיחה משובשת, קפיצה
בין נושאים, ויכוח שקט שלא הוצע בכלל) בלי לשמור טקסט מהשידור עצמו.

הרצה:  python3 tests/test_clip_quality.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxquality_"))

from polixor import i18n                                             # noqa: E402
from polixor.services import clip_intel                              # noqa: E402
from polixor.services.clip_intel.score import MIN_HOOK, score_proposal  # noqa: E402
from polixor.services.clip_intel.story import (Proposal, has_semantic_payoff,  # noqa: E402
                                               opening_quality, payoff_potential, semantic_hooks)
from polixor.services.clip_intel.topics import detect_topics, strong_bounds  # noqa: E402
from polixor.services.clip_intel.units import build_units            # noqa: E402
from polixor.services.transcribe import Segment, TranscriptResult, Word  # noqa: E402
from test_clip_intel import settings, timeline                       # noqa: E402


def transcript(lines, language: str = "he") -> TranscriptResult:
    """שורות (התחלה, סוף, טקסט[, הסתברות זיהוי])."""
    segs = []
    for line in lines:
        start, end, text = line[:3]
        prob = line[3] if len(line) > 3 else 0.92
        toks = text.split()
        step = (end - start) / max(1, len(toks))
        words = [Word(start=round(start + i * step, 3), end=round(start + (i + 1) * step - 0.02, 3),
                      text=t, probability=prob) for i, t in enumerate(toks)]
        segs.append(Segment(start=start, end=end, text=text, words=words, language=language))
    return TranscriptResult(segments=segs, language=language, duration=lines[-1][1] + 5.0,
                            provider="test")


def units_of(lines, spikes=(), language="he"):
    tr = transcript(lines, language)
    tl = timeline(tr.duration, spikes)
    return build_units(tr, tl, language), tl


def run(lines, spikes=(), *, language="he", limit=5, **kw):
    tr = transcript(lines, language)
    tl = timeline(tr.duration, spikes)
    with i18n.use_lang("en"):
        return clip_intel.select_short_clips(tl, tr, settings=settings(**kw),
                                             language=language, limit=limit), tr


def one(text: str, language: str = "he"):
    us, _ = units_of([(10.0, 13.0, text)], language=language)
    return us[0]


def all_records(res) -> list[dict]:
    return res.review["selected"] + res.review["near_misses"] + res.review["duplicates"]


# --------------------------------------------------------------------------
# וו
# --------------------------------------------------------------------------
def test_trivial_questions_are_not_strong_hooks():
    for text in ("איזה יום היום?", "אתה כבר בבית?", "אתה מבין?", "מה השעה עכשיו?"):
        u = one(text)
        q, good, bad = opening_quality(u, None)
        assert u.question_kind in ("trivial", "tag"), (text, u.question_kind)
        assert "meaningful_question" not in semantic_hooks(u), text
        assert q < MIN_HOOK, (text, q, good)
        assert "trivial_question" in bad or "private_talk" in bad, (text, bad)
    for text in ("what day is it today?", "you know?"):
        u = one(text, "en")
        assert opening_quality(u, None)[0] < MIN_HOOK, text


def test_opinion_and_conflict_can_be_strong_hooks():
    cases = {
        "לדעתי הוא השחקן הכי גרוע בכל הליגה הזאת": "opinion",
        "מה פתאום, אני לא מסכים איתך בכלל בעניין הזה": "conflict",
        "למה דווקא אותו הכניסו להרכב ולא את הקשר הצעיר?": "meaningful_question",
    }
    for text, cat in cases.items():
        u = one(text)
        q, good, _ = opening_quality(u, None)
        assert cat in semantic_hooks(u), (text, semantic_hooks(u))
        assert q >= MIN_HOOK, (text, q, good)
    assert "opinion" in semantic_hooks(one("in my opinion he is the worst player in the league", "en"))


def test_acoustic_reaction_without_content_is_not_a_payoff():
    lines = [(10.0, 11.0, "יאללה!"), (14.0, 17.0, "טוב אני ממשיך לשחק פה רגע")]
    us, _ = units_of(lines, [(11.0, 13.5, 0.98)])
    v, why = payoff_potential(us[0], us[1])
    assert "acoustic_only" in why and not has_semantic_payoff(why), why
    # שידור שבו יש רק צעקות ושיחה שגרתית – אין קליפ
    lines = [
        (10.0, 13.0, "טוב אני מסדר פה את המצלמה רגע"),
        (14.0, 15.0, "וואי!"),
        (18.0, 21.0, "אוקיי ממשיכים עם המשחק עכשיו"),
        (22.0, 23.0, "יאללה!"),
        (26.0, 30.0, "רגע אני שותה משהו וחוזר אליכם"),
        (31.0, 32.0, "אוי!"),
        (35.0, 39.0, "טוב בואו נראה מה יש פה בתפריט"),
    ]
    res, _ = run(lines, [(15.0, 17.5, 0.97), (23.0, 25.5, 0.97), (32.0, 34.5, 0.97)])
    assert not res.selected, [(c.start, c.end, c.reason) for c in res.selected]


def test_setup_without_answer_is_extended_to_the_answer():
    lines = [
        (10.0, 15.0, "תקשיבו, יש לי דעה על המאמן החדש שאף אחד לא אומר"),
        (15.5, 19.0, "תסביר להם למה אתה חושב שהוא לא מתאים"),
        (19.6, 24.0, "כי לדעתי הוא פוחד לשנות את ההרכב, זה הכי גרוע"),
        (24.5, 28.0, "בסופו של דבר בלי אומץ אין תארים, אין מה לדבר"),
        (34.0, 38.0, "טוב בואו נמשיך לשאלה הבאה בצ'אט"),
    ]
    res, _ = run(lines, [(28.0, 30.0, 0.9)])
    assert res.selected, [r["rejection"] for r in res.review["near_misses"]]
    c = res.selected[0]
    assert c.end >= 28.0, (c.start, c.end)


def test_setup_without_answer_is_rejected():
    lines = [
        (10.0, 14.0, "רגע רגע, יש פה משהו מעניין שקרה במשחק"),
        (14.5, 18.0, "לדעתי זה היה המהלך הכי מטורף בעונה"),
        (18.5, 21.5, "תסביר להם למה זה היה ככה"),
        (40.0, 44.0, "טוב בואו נעבור למשהו אחר לגמרי"),
    ]
    res, _ = run(lines, [(21.5, 23.5, 0.97)])
    for c in res.selected:
        assert c.end > 22.0 or c.end < 18.6, (c.start, c.end)
    # ההצעה שנגמרת ב"תסביר להם" אינה עוברת
    endings = [r for r in all_records(res) if abs(r["end"] - 21.5) < 1.5]
    assert all(r["status"] != "selected" for r in endings), endings


# --------------------------------------------------------------------------
# משמעת בחירה
# --------------------------------------------------------------------------
def test_private_name_calling_is_penalised():
    lines = [
        (10.0, 10.8, "דני!"), (11.2, 12.0, "דני!"),
        (12.5, 14.0, "מה דני עכשיו?"),
        (14.5, 17.5, "תסביר להם למה אתה עושה את זה"),
        (17.8, 18.8, "וואו!"),
        (30.0, 34.0, "טוב חזרתי, איפה היינו בשידור"),
    ]
    us, _ = units_of(lines)
    assert us[0].private and us[1].private, [(u.text, u.private) for u in us]
    res, _ = run(lines, [(18.8, 21.0, 0.98)])
    assert not res.selected, [(c.start, c.end, c.reason) for c in res.selected]


def test_off_mic_question_is_private():
    u = one("אתה כבר בבית? תפתח מיק")
    assert u.private


def test_cheers_are_not_name_calls():
    for text in ("גול! גול!", "יאללה יאללה!", "יש! יש!"):
        assert not one(text).private, text
    assert not one("come on come on!", "en").private
    assert one("דני! דני!").private and one("John! John!", "en").private


def test_topic_jump_is_rejected():
    lines = [
        (10.0, 14.0, "לדעתי הקבוצה הזאת הכי גרועה בליגה השנה"),
        (14.5, 19.0, "אין מצב שהם עולים לגמר עם ההגנה הזאת"),
        (19.5, 24.0, "בסופו של דבר הם ירדו ליגה, אין מה לדבר"),
    ]
    us, tl = units_of(lines)
    p = Proposal(hook_idx=0, payoff_idx=2, end_idx=2, start=9.9, end=24.4, sources=["payoff_signal"],
                 start_reason="sentence_start", end_reason="sentence_end")
    ok = score_proposal(p, us, tl, min_d=10, threshold=0.4)
    jumped = score_proposal(p, us, tl, min_d=10, threshold=0.4, topic_bounds=[14.3])
    assert ok.gates["one_topic"] and not jumped.gates["one_topic"]
    assert jumped.rejection == "topic_jump" and not jumped.passed, jumped.rejection


def test_topic_detection_finds_strong_boundaries():
    a = ["הכדורגל", "ההרכב", "השוער", "הבלם", "המאמן", "הגול"]
    b = ["הבישול", "העוף", "התנור", "הרוטב", "הבצל", "השום"]
    lines, t = [], 0.0
    for k in range(14):
        lines.append((t, t + 6.0, f"דיברנו על {a[k % 6]} ועל {a[(k + 2) % 6]} ועל {a[(k + 4) % 6]} היום"))
        t += 6.5
    t += 5.0
    lines.append((t, t + 4.0, "בנושא אחר לגמרי, בואו נדבר על אוכל"))
    t += 4.5
    for k in range(14):
        lines.append((t, t + 6.0, f"שמנו את {b[k % 6]} עם {b[(k + 2) % 6]} ועם {b[(k + 4) % 6]} בסיר"))
        t += 6.5
    us, _ = units_of(lines)
    topics = detect_topics(us)
    assert len(topics) == 2, [(x.start, x.end) for x in topics]
    bounds = strong_bounds(topics)
    assert bounds and 90.0 < bounds[0] < 100.0, bounds


def test_short_talk_has_no_false_strong_topic_boundary():
    """שיחה קצרה בלי מעבר נושא מפורש: אין גבול "חזק" שיפסול סיפור באמצעו."""
    lines = [
        (1.0, 5.2, "שלום לכולם וברוכים הבאים לשידור של היום"),
        (5.6, 10.4, "היום אנחנו הולכים לנסות משהו שאף פעם לא עשיתי"),
        (11.0, 16.0, "קודם בואו נסתכל על מה שקרה אתמול במשחק"),
        (16.4, 19.8, "זה היה די רגיל בהתחלה, שום דבר מיוחד"),
        (22.2, 26.8, "אין מצב! ראיתם את זה? זה מטורף לגמרי!"),
        (27.2, 31.0, "אני נשבע לכם שאף פעם לא ראיתי דבר כזה"),
        (31.4, 36.5, "טוב, בואו נירגע רגע ונמשיך הלאה בשקט"),
        (43.2, 48.6, "תקשיבו, אני רוצה לספר לכם משהו שלא סיפרתי לאף אחד"),
        (49.0, 52.0, "לפני שנה חשבתי לעזוב את הסטרימינג לגמרי"),
        (52.4, 57.0, "וואו! בדיוק עכשיו? ניצחתי! עשיתי את זה!"),
        (62.4, 67.5, "בואו נדבר רגע על למה זה עבד ומה הייתה הטעות שלי"),
        (70.2, 74.8, "חחחח אני מת מצחוק, תראו את הפרצוף שלו"),
        (80.4, 86.0, "תודה שהייתם איתי היום, נתראה בשידור הבא"),
    ]
    us, _ = units_of(lines)
    assert strong_bounds(detect_topics(us)) == []
    res, _ = run(lines, [(52.4, 57.2, 0.95)], short_min_seconds=12, short_max_seconds=30)
    assert all(r["rejection"] is None or r["rejection"]["key"] != "reject.topic_jump"
               for r in all_records(res)), [r["rejection"] for r in all_records(res)]


def test_boundary_moves_to_the_real_hook():
    """פתיחה חלשה (שאלה שגרתית, הקראה משובשת) → הקליפ מתחיל בוו האמיתי."""
    lines = [
        (10.0, 12.0, "איזה יום היום? רביעי נכון?"),
        (12.3, 16.0, "כן כן קראתי את זה בצ'אט אוקיי סבבה", 0.30),
        (16.4, 20.0, "האם שווה בכלל לשדר את זה, אחי?"),
        (20.5, 25.0, "לדעתי אין מצב, זה הכי גרוע שיש לשדר משהו כזה"),
        (25.5, 29.0, "בסופו של דבר אנשים באים לראות משחק, לא שטויות"),
        (35.0, 39.0, "טוב נמשיך עם הסיבוב הבא עכשיו"),
    ]
    res, _ = run(lines, [(29.0, 31.0, 0.9)])
    assert res.selected, [r["rejection"] for r in res.review["near_misses"]]
    c = res.selected[0]
    assert 16.0 <= c.start <= 16.4, c.start
    assert c.end >= 29.0, c.end


def test_garbled_opening_is_not_a_hook():
    lines = [(10.0, 14.0, "ומה שהוא כתב שם בצ'אט זה ככה וככה", 0.25)]
    us, _ = units_of(lines)
    assert us[0].garbled and not semantic_hooks(us[0])
    assert "unclear_opening" in opening_quality(us[0], None)[2]


def test_quiet_football_debate_is_discovered():
    """ויכוח ענייני בלי צחוק ובלי צעקות – מוצע (מקור "ויכוח") ונבחר."""
    lines = [
        (100.0, 103.0, "טוב אז מה עם הנבחרת בסוף"),
        (104.0, 108.0, "למה דווקא את החלוץ הזה הכניסו להרכב?"),
        (108.5, 113.0, "לדעתי הוא לא מעניין, הייתי מחליף אותו בקשר הצעיר"),
        (113.5, 118.0, "מה פתאום, אני לא מסכים, הוא יותר טוב מכל הקשרים"),
        (118.5, 123.0, "אין מצב, הקשר הצעיר עדיף עליו בכל פרמטר"),
        (123.5, 128.0, "בסופו של דבר המאמן היה צריך להחליף אותו בדקה השישים"),
        (135.0, 139.0, "טוב נמשיך לשחק את המשחק שלנו עכשיו"),
    ]
    res, _ = run(lines)
    srcs = {s["key"].rsplit(".", 1)[-1] for r in all_records(res) for s in r["proposed_by"]}
    assert "argument" in srcs, srcs
    assert res.selected, [r["rejection"] for r in res.review["near_misses"]]
    c = res.selected[0]
    assert c.start <= 104.0 and c.end >= 127.5, (c.start, c.end)
    rec = res.review["selected"][0]
    assert {"opinion", "conflict", "meaningful_question"} & set(rec["hook"]["categories"]), rec["hook"]


def test_review_lists_topics_and_hook_categories():
    lines = [
        (10.0, 14.0, "לדעתי הקבוצה הזאת הכי גרועה בליגה השנה"),
        (14.5, 19.0, "מה פתאום, אני לא מסכים, יש להם את ההגנה הכי טובה"),
        (19.5, 24.0, "בסופו של דבר הם ירדו ליגה, אין מה לדבר"),
        (30.0, 34.0, "טוב נמשיך לשחק את המשחק שלנו"),
    ]
    res, _ = run(lines)
    assert "topics" in res.review and res.review["topics"], res.review.keys()
    for r in res.review["selected"]:
        assert "categories" in r["hook"] and "delay" in r["hook"]


PODCAST = [
    (100.0, 104.5, "החברים שלי כל הזמן שואלים אותי מה זה הפודקאסט הזה?"),
    (105.0, 107.5, "ואם כבר, באיזו פלטפורמה?"),
    (108.0, 111.5, "יוטיוב, ספוטיפיי, וקליפים בטיקטוק."),
    (112.2, 114.6, "האם בכלל שווה לפתוח פודקאסט היום, אחי?"),
    (115.0, 118.6, "זו תשובה של כן ולא. אם יש לך משהו להגיד, אז כן."),
    (119.0, 122.8, "הבעיה שרוב הפודקאסטים משעממים וחסרי אופי."),
    (123.0, 126.0, "בסופו של דבר זה עניין של מודעות עצמית."),
    (126.3, 131.0, "אם אתה מספיק טוב תצליח, ואם לא, אתה תבזבז את הזמן שלך."),
    (133.0, 133.8, "אתה מבין?"),
    (134.6, 136.0, "כאילו בסוף תחשוב"),
    (145.0, 149.0, "טוב, בואו נחזור למשחק עכשיו"),
]


def test_unrelated_preroll_trimmed_and_trailing_filler_dropped():
    """הקדמה (מה החברים שואלים, איזו פלטפורמה) נחתכת עד השאלה; „אתה מבין? כאילו…" לא בסוף."""
    res, _ = run(PODCAST, [(131.0, 133.0, 0.9)], short_min_seconds=15, short_max_seconds=45)
    assert res.selected, [r["rejection"] for r in res.review["near_misses"]]
    c = res.selected[0]
    assert 111.5 <= c.start <= 112.2, c.start
    assert 131.0 <= c.end < 133.0, c.end
    rec = res.review["selected"][0]
    assert "כאילו" not in rec["payoff"]["text"] and "verdict" not in rec["payoff"]["text"]


def test_payoff_is_never_a_trailing_tag():
    from polixor.services.clip_intel.story import weak_tail
    us, _ = units_of(PODCAST)
    by = {u.text: u for u in us}
    assert weak_tail(by["אתה מבין?"]) and weak_tail(by["כאילו בסוף תחשוב"])
    assert not weak_tail(by["אם אתה מספיק טוב תצליח, ואם לא, אתה תבזבז את הזמן שלך."])
    assert not weak_tail(by["בסופו של דבר זה עניין של מודעות עצמית."])


def test_rhetorical_question_after_opinion_is_not_an_open_setup():
    from polixor.services.clip_intel.story import unanswered
    lines = [(10.0, 14.0, "לדעתי רוב התוכניות האלה פשוט משעממות"),
             (14.5, 17.0, "אז בשביל מה לראות אותן בכלל?"),
             (20.0, 23.0, "תסביר להם למה אתה עדיין צופה")]
    us, _ = units_of(lines)
    assert not unanswered(us, 1) and unanswered(us, 2)
    lone, _ = units_of([(10.0, 13.0, "למה הם בחרו דווקא את המאמן הזה?")])
    assert unanswered(lone, 0)


def test_strong_short_story_is_recovered_without_lowering_the_bar():
    lines = [
        (50.0, 53.5, "אתה לא חושב שאתה מגזים עם הדרמה הזאת?"),
        (54.0, 58.0, "לדעתי אתה מטורף לגמרי, אין מצב שזה נכון"),
        (58.5, 63.5, "בסופו של דבר אתה תתחרט על זה, אין מה לדבר"),
        (75.0, 79.0, "טוב נמשיך לשחק עכשיו בשקט"),
    ]
    res, _ = run(lines, [(63.5, 65.5, 0.9)], short_min_seconds=20, short_max_seconds=60)
    assert res.selected, [r["rejection"] for r in res.review["near_misses"]]
    assert res.selected[0].end - res.selected[0].start < 17.0
    # אותו אורך בלי תוכן חזק – עדיין נפסל על אורך/איכות
    weak = [(50.0, 53.5, "טוב אז מה אני עושה עכשיו פה"), (54.0, 58.0, "רגע אני מסדר את המצלמה"),
            (58.5, 63.5, "אוקיי בסדר ממשיכים"), (75.0, 79.0, "טוב נמשיך לשחק עכשיו")]
    res2, _ = run(weak, [(63.5, 65.5, 0.9)], short_min_seconds=20, short_max_seconds=60)
    assert not res2.selected


# --------------------------------------------------------------------------
# עורך AI: שכבה משנית, מעוגנת בתמלול
# --------------------------------------------------------------------------
DEBATE = [
    (100.0, 103.0, "טוב אז מה עם הנבחרת"),
    (104.0, 108.0, "למה דווקא את החלוץ הזה הכניסו להרכב?"),
    (108.5, 113.0, "לדעתי הוא לא מעניין, הייתי מחליף אותו בקשר הצעיר"),
    (113.5, 118.0, "מה פתאום, אני לא מסכים, הוא יותר טוב מכל הקשרים"),
    (118.5, 123.0, "אין מצב, הקשר הצעיר עדיף עליו בכל פרמטר"),
    (123.5, 128.0, "בסופו של דבר המאמן היה צריך להחליף אותו בדקה השישים"),
    (135.0, 139.0, "טוב נמשיך לשחק את המשחק שלנו עכשיו"),
]


def _with_model(reply, fn):
    from polixor.services import llm
    from polixor.services.clip_intel import judge

    calls: list[str] = []
    orig = llm.is_llm_enabled, llm.call_model

    def call(system, user, s):
        calls.append(user)
        if isinstance(reply, Exception):
            raise reply
        return reply(user) if callable(reply) else reply

    llm.is_llm_enabled = lambda s: True
    llm.call_model = call
    judge._CACHE.clear()
    try:
        return fn(), calls
    finally:
        llm.is_llm_enabled, llm.call_model = orig
        judge._CACHE.clear()


def test_judge_hallucinated_quote_is_discarded():
    import json as _json
    fake = _json.dumps({"hook": 1, "clarity": 1, "interest": 1, "payoff": 1, "complete": 1,
                        "upload_worthy": False, "standalone": False, "ordinary": True,
                        "hook_quote": "הוא אמר שהוא עוזב את הקבוצה מחר",      # לא נאמר
                        "payoff_quote": "בסופו של דבר המאמן היה צריך",
                        "start": None, "end": None, "reason": "boring"}, ensure_ascii=False)
    (res, _), calls = _with_model(fake, lambda: run(DEBATE))
    assert calls, "the judge was not called"
    assert res.selected, "an ungrounded verdict must not reject the clip"
    rec = res.review["selected"][0]
    assert rec["judge"]["discarded"] == "unsupported_hook_quote", rec["judge"]
    assert res.review["stats"]["judged"] == 0


def test_judge_grounded_rejection_is_applied():
    import json as _json
    fake = _json.dumps({"hook": 3, "clarity": 4, "interest": 3, "payoff": 3, "complete": 5,
                        "upload_worthy": False, "standalone": True, "ordinary": False,
                        "hook_quote": "למה דווקא את החלוץ הזה",
                        "payoff_quote": "המאמן היה צריך להחליף אותו",
                        "start": None, "end": None, "reason": "not worth posting"}, ensure_ascii=False)
    (res, _), _ = _with_model(fake, lambda: run(DEBATE))
    assert not res.selected
    assert any(r["rejection"]["key"] == "reject.judge" for r in res.review["near_misses"])


def test_judge_boundary_suggestion_is_rechecked():
    """הצעת גבולות מאומצת רק אחרי חישוב מחדש; הצעה מחוץ לחלון נמחקת."""
    import json as _json

    def reply(user):
        data = _json.loads(user)
        nums = [x["n"] for x in data["sentences"]]
        return _json.dumps({"hook": 8, "clarity": 8, "interest": 9, "payoff": 8, "complete": 8,
                            "upload_worthy": True, "standalone": True, "ordinary": False,
                            "hook_quote": "למה דווקא את החלוץ", "payoff_quote": "המאמן היה צריך",
                            "start": max(nums) + 5, "end": max(nums) + 9,
                            "reason": "good debate"}, ensure_ascii=False)
    (res, tr), _ = _with_model(reply, lambda: run(DEBATE))
    assert res.selected
    rec = res.review["selected"][0]
    assert rec["judge"]["start"] is None and rec["judge"]["end"] is None, rec["judge"]
    c = res.selected[0]
    inside = [s for s in tr.segments if s.end > c.start and s.start < c.end]
    assert [s.text for s in inside] == [s.text for s in tr.segments
                                        if s.start >= inside[0].start and s.end <= inside[-1].end]


def test_selection_works_without_ai_provider():
    from polixor.services import llm
    plain, _ = run(DEBATE)
    assert plain.selected and plain.review["stats"]["judged"] == 0
    (failing, _), calls = _with_model(llm.AiProviderError(message_key="processing.llm.no_model_mode"),
                                      lambda: run(DEBATE))
    assert calls and failing.selected
    assert [(c.start, c.end) for c in failing.selected] == [(c.start, c.end) for c in plain.selected]


def test_titles_use_the_creators_own_strongest_line():
    from polixor.services.clip_intel.titles import clean_line
    res, tr = run(DEBATE)
    assert res.selected
    title = res.selected[0].title
    spoken = " ".join(s.text for s in tr.segments)
    assert title and title.rstrip("…") in spoken, title          # מילה במילה, באותו סדר
    assert len(title) <= 53 and res.selected[0].title_source == "transcript"
    assert clean_line("טוב אז לדעתי הוא השחקן הכי גרוע בליגה, אתה מבין?", "he") == \
        "לדעתי הוא השחקן הכי גרוע בליגה"
    assert clean_line("תקשיבו, אין מצב שהם עולים לגמר", "he") == "אין מצב שהם עולים לגמר"
    long = clean_line("למה דווקא את החלוץ הזה הכניסו להרכב במשחק הכי חשוב של העונה כולה?", "he")
    assert len(long) <= 53 and long.startswith("למה דווקא"), long


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
    print(f"\n{passed}/{len(fns)} בדיקות איכות בחירה עברו")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
