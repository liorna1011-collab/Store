"""
בדיקות לתמלול החוזר בענן ולהכרעה לפי הסכמה (services/transcribe_cloud,
transcript_correct.decide עם `cloud`). אין כאן קריאות רשת – לקוח HTTP מדומה.

הרצה:  python3 tests/test_transcribe_cloud.py
"""

from __future__ import annotations

import logging
import math
import os
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxcloud_"))

import numpy as np                                                   # noqa: E402

from polixor.config import SECRETS, AppSettings                      # noqa: E402
from polixor.services import transcribe_cloud as tcl                 # noqa: E402
from polixor.services import transcript_correct as tc                # noqa: E402
from polixor.services.transcribe import Segment, TranscriptResult, Word  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="pxcloudwav_"))
FAKE_KEY = "sk-test-not-a-real-key-000"


def _w(s, e, t, p):
    return Word(start=s, end=e, text=t, probability=p)


def _seg(text, p=0.3, start=1.0, end=2.0):
    toks = text.split()
    step = (end - start) / len(toks)
    return Segment(start=start, end=end, text=text,
                   words=[_w(start + i * step, start + (i + 1) * step, t, p)
                          for i, t in enumerate(toks)])


def _cloud(text, p=0.9, start=1.0, end=2.0):
    return [_seg(text, p, start, end)]


# --------------------------------------------------------------------------
# ההכרעה
# --------------------------------------------------------------------------
def test_cloud_agrees_with_original_keeps_it_even_if_strong_model_changed_it():
    orig = _seg("הוא אמר שלום", 0.35)
    alt = [_seg("הוא אמר שלוש", 0.9)]
    rv = tc.decide(orig, 0, alt, cloud=_cloud("הוא אמר שלום"), cloud_model="m")
    assert rv.status == "confirmed" and rv.corrected is None, rv
    assert rv.source == "cloud"
    assert [e["kind"] for e in rv.evidence] == ["retranscription", "cloud"]


def test_cloud_agreeing_with_weak_alternative_accepts_it():
    orig = _seg("אני הולך לחנות עם אוהת", 0.3)
    # חלופה מקומית, ביטחון לא מספיק לבד (0.55 < MIN_ALT_CONF)
    alt = [_seg("אני הולך לחנות עם אוהד", 0.55)]
    local = tc.decide(orig, 0, alt)
    assert local.status == "flagged"
    rv = tc.decide(orig, 0, alt, cloud=_cloud("אני הולך לחנות עם אוהד"))
    assert rv.status == "corrected" and rv.source == "retranscription+cloud"
    assert rv.corrected == "אני הולך לחנות עם אוהד"
    # הזמנים מגיעים מהחלופה המקומית (שיש לה זמני מילים אמיתיים)
    assert rv.corrected_words[0]["start"] == alt[0].words[0].start


def test_cloud_alone_needs_clear_confidence_and_no_local_alternative():
    orig = _seg("משפט לא ברור בכלל", 0.3)
    strong = tc.decide(orig, 0, None, cloud=_cloud("משפט די ברור בכלל", 0.92))
    assert strong.status == "corrected" and strong.source == "cloud"
    weak = tc.decide(orig, 0, None, cloud=_cloud("משפט די ברור בכלל", 0.7))
    assert weak.status == "flagged"
    # טקסט אחר לגמרי – גם בביטחון גבוה לא מחליף
    far = tc.decide(orig, 0, None, cloud=_cloud("אתמול נסענו לים", 0.97))
    assert far.status == "flagged"
    # כשיש חלופה מקומית שהענן לא מסכים איתה – לא בוחרים אף אחד לבד
    alt = [_seg("משהו אחר לגמרי כאן", 0.5)]
    split = tc.decide(orig, 0, alt, cloud=_cloud("משפט די ברור בכלל", 0.95))
    assert split.status == "flagged"


def test_cloud_hallucination_or_empty_is_ignored():
    orig = _seg("משפט לא ברור", 0.3)
    for text in ("", "תודה שצפיתם"):
        rv = tc.decide(orig, 0, None, cloud=_cloud(text, 0.99) if text else [])
        assert rv.status == "flagged", (text, rv.status)


def test_cloud_length_mismatch_is_not_accepted():
    orig = _seg("שתי מילים", 0.2)
    rv = tc.decide(orig, 0, None, cloud=_cloud("הרבה מאוד מילים שלא נאמרו כאן בכלל", 0.95))
    assert rv.status == "flagged"


def test_cloud_does_not_undo_a_vocabulary_fix():
    orig = Segment(start=0, end=1, text="דיברתי עם אוהת",
                   words=[_w(0, .3, "דיברתי", .9), _w(.3, .5, "עם", .9), _w(.5, 1, "אוהת", .2)])
    rv = tc.decide(orig, 0, None, vocabulary=["אוהד"], cloud=_cloud("דיברתי עם אוהת", 0.6, 0, 1))
    assert rv.status == "corrected" and rv.source == "vocabulary"


def test_review_transcript_calls_cloud_per_sentence_and_records_model():
    segs = [_seg("שלום לכולם", 0.95, 0, 1), _seg("משפט לא ברור", 0.3, 1.2, 2.0),
            _seg("בכלל לא קשור לזה", 0.3, 5.0, 6.0)]
    tr = TranscriptResult(segments=segs, language="he", duration=8.0)
    asked = []

    def cloud(a, b):
        asked.append((round(a, 2), round(b, 2)))
        return _cloud("משפט לא ברור", 0.95, a, b)

    data = tc.review_transcript(tr, spans=None, retranscribe=None, cloud=cloud, cloud_model="gpt-x")
    assert asked == [(0.9, 2.3), (4.7, 6.3)], asked          # רק המשפטים החשודים, כל אחד לבד
    assert data["cloud_model"] == "gpt-x"
    by = {r["index"]: r for r in data["segments"]}
    assert by[1]["status"] == "confirmed" and by[2]["status"] == "flagged"


def test_llm_choice_can_use_the_cloud_alternative():
    from polixor.services import llm

    seg = _seg("משפט לא ברור", 0.3)
    tr = TranscriptResult(segments=[seg], language="he", duration=3.0)
    data = tc.review_transcript(tr, spans=None, retranscribe=None,
                                cloud=lambda a, b: _cloud("משפט די ברור", 0.7, a, b))
    assert data["segments"][0]["status"] == "flagged"
    real = (llm.is_llm_enabled, llm.call_model)
    try:
        llm.is_llm_enabled = lambda s: True
        llm.call_model = lambda system, user, s: '{"choice": "B"}'
        assert tc.llm_choose(data, tr, AppSettings()) == 1
    finally:
        llm.is_llm_enabled, llm.call_model = real
    assert data["segments"][0]["corrected"] == "משפט די ברור"


# --------------------------------------------------------------------------
# הלקוח
# --------------------------------------------------------------------------
def test_word_probabilities_from_token_logprobs():
    lp = [{"token": "של", "logprob": math.log(0.9)}, {"token": "ום", "logprob": math.log(0.4)},
          {"token": " עולם", "logprob": math.log(0.8)}]
    assert tcl.word_probabilities("שלום עולם", lp) == [0.4, 0.8]
    assert tcl.word_probabilities("שלום עולם", None) == [0.7, 0.7]


def test_estimated_times_cover_the_speech_in_order():
    t = tcl.estimate_word_times(["א", "בבבב", "גג"], 1.0, 3.0)
    assert t[0][0] == 1.0 and abs(t[-1][1] - 3.0) < 1e-6
    assert all(a < b for a, b in t) and all(t[i][1] <= t[i + 1][0] + 1e-6 for i in range(2))


class _Resp:
    def __init__(self, code, body):
        self.status_code, self._body = code, body

    def json(self):
        return self._body


class _Http:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def post(self, url, **kw):
        self.calls.append((url, kw))
        if isinstance(self.resp, Exception):
            raise self.resp
        return self.resp


def _wav() -> Path:
    x = np.zeros(16000 * 4, np.float32)
    t = np.arange(x.size) / 16000
    m = (t >= 1.5) & (t < 2.5)
    x[m] = 0.3 * np.sin(2 * np.pi * 220 * t[m])
    x += np.random.default_rng(0).normal(0, 0.002, x.size).astype(np.float32)
    p = TMP / "a.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    return p


def _with_key(fn):
    before = SECRETS.get("openai_api_key")
    SECRETS.set("openai_api_key", FAKE_KEY)
    try:
        return fn()
    finally:
        SECRETS.set("openai_api_key", before or "")


def test_client_sends_only_the_window_and_parses_logprobs():
    http = _Http(_Resp(200, {"text": "שלום עולם",
                             "logprobs": [{"token": "שלום", "logprob": -0.05},
                                          {"token": " עולם", "logprob": -0.1}]}))
    s = AppSettings(asr_vocabulary=["אוהד"])

    def run():
        r = tcl.CloudRetranscriber(_wav(), s, "he", http=http)
        return r, r(1.0, 3.0)
    r, segs = _with_key(run)
    url, kw = http.calls[0]
    assert url == tcl.ENDPOINT
    assert kw["data"]["model"] == "gpt-4o-transcribe" and kw["data"]["language"] == "he"
    assert kw["data"]["include[]"] == "logprobs" and "אוהד" in kw["data"]["prompt"]
    assert kw["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    name, body, mime = kw["files"]["file"]
    assert mime == "audio/wav" and 60000 < len(body) < 70000   # 2 שניות, לא כל הקובץ
    assert segs[0].text == "שלום עולם" and segs[0].words[0].probability > 0.9
    # הזמנים המשוערים נצמדים לדיבור בחלון (1.5-2.5)
    assert abs(segs[0].words[0].start - 1.5) <= 0.05 and abs(segs[0].words[-1].end - 2.5) <= 0.05
    assert r.calls == 1 and r.failures == 0


def test_client_failure_returns_none_without_leaking_key():
    records = []

    class H(logging.Handler):
        def emit(self, rec):
            records.append(rec.getMessage())
    h = H()
    logging.getLogger("polixor.transcribe_cloud").addHandler(h)
    try:
        for resp in (_Resp(401, {"error": {"message": f"bad key {FAKE_KEY}"}}),
                     RuntimeError(f"boom {FAKE_KEY}")):
            http = _Http(resp)
            out = _with_key(lambda: tcl.CloudRetranscriber(_wav(), AppSettings(), "he", http=http)(1, 3))
            assert out is None
    finally:
        logging.getLogger("polixor.transcribe_cloud").removeHandler(h)
    assert records and not any(FAKE_KEY in m for m in records), records


def test_client_without_key_does_nothing():
    http = _Http(_Resp(200, {"text": "x"}))
    before = SECRETS.get("openai_api_key")
    SECRETS.set("openai_api_key", "")
    try:
        assert tcl.CloudRetranscriber(_wav(), AppSettings(), "he", http=http)(1, 3) is None
    finally:
        SECRETS.set("openai_api_key", before or "")
    assert http.calls == []


def test_available_only_in_quality_mode_when_enabled_with_key():
    def check(**kw):
        return _with_key(lambda: tcl.available(AppSettings(**kw)))
    assert check(asr_cloud_fallback=True, performance_profile="quality")
    assert not check(asr_cloud_fallback=True, performance_profile="fast")
    assert not check(asr_cloud_fallback=False, performance_profile="quality")
    SECRETS.set("openai_api_key", "")
    assert not tcl.available(AppSettings(asr_cloud_fallback=True, performance_profile="quality"))


def test_unknown_cloud_model_falls_back_to_default():
    assert AppSettings(asr_cloud_model="evil-model").clamp().asr_cloud_model == "gpt-4o-transcribe"


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
    print(f"\n{passed}/{len(fns)} בדיקות תמלול בענן עברו")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
