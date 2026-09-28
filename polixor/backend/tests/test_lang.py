"""
בדיקות לחבילות השפה (services/lang): התאמה בגבולות מילה, תחיליות
וסיומות בעברית, שלילה, זיהוי שפה, והוספת שפה חדשה בלי לגעת במנועים.

הרצה:  python3 tests/test_lang.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxlang_"))

from polixor.services import lang as L                              # noqa: E402
from polixor.services import scoring                                # noqa: E402
from polixor.services.transcribe import Segment, TranscriptResult   # noqa: E402

HE, EN = L.get_pack("he"), L.get_pack("en")


def test_word_boundaries():
    assert HE.count("זו האמת הפשוטה", ["מת"]) == 0          # „מת" לא בתוך „אמת"
    assert HE.count("הוא מת מצחוק", ["מת"]) == 1
    assert EN.count("I love lollipops", ["lol"]) == 0
    assert EN.count("lol that was good", ["lol"]) == 1
    assert HE.count("בחיים לא ראיתי", ["בחיי"]) == 0          # „בחיי" לא בתוך „בחיים"


def test_hebrew_prefixes_and_suffixes():
    assert HE.count("וניצחתי בסוף", ["ניצחתי"]) == 1
    assert HE.count("כשניצחתי הבנתי", ["ניצחתי"]) == 1
    assert HE.count("סיפורים מצחיקים", ["סיפור"]) == 1
    # מילה קצרה לא מקבלת תחילית: „הכי" אינו ה+„כי"
    assert HE.count("זה הכי טוב", ["כי"]) == 0
    assert HE.strip_prefix("בכביש") == "כביש"
    assert HE.strip_prefix("בית") is None                     # אחרי הסרה נשארות 2 אותיות


def test_negation_is_skipped_on_request():
    assert HE.count("לא אהבתי את זה", ["אהבתי"], skip_negated=True) == 0
    assert HE.count("אהבתי את זה", ["אהבתי"], skip_negated=True) == 1
    assert HE.count("לא אהבתי את זה", ["אהבתי"]) == 1


def test_hanging_words():
    assert HE.is_hanging("של") and HE.is_hanging("ושל") and not HE.is_hanging("שולחן")
    assert EN.is_hanging("the") and EN.is_hanging("The,") and not EN.is_hanging("theme")


def test_detect_language():
    assert L.detect_language("שלום לכולם, מה קורה?") == "he"
    assert L.detect_language("Hello everyone, what's up?") == "en"
    assert L.detect_language("מרحبا بكم في البث") == "ar"      # ערבית אינה עברית
    assert L.detect_language("123 !!!") is None
    tr = TranscriptResult(segments=[
        Segment(0, 5, "שלום לכולם היום נדבר על מחשבים", language="he"),
        Segment(5, 7, "OK", language="he"),
        Segment(7, 20, "ואחר כך נמשיך לדבר על מסכים ומקלדות", language="en")],
        language="en")
    # הכתב בפועל גובר על מה שהמודל הצהיר
    assert L.resolve_language(tr) == "he"
    assert L.segment_languages(tr)[2] == "he"


def test_unknown_language_uses_all_packs():
    assert len(L.packs_for(None)) >= 2
    assert len(L.packs_for("ja")) == len(L.packs_for(None))
    assert L.packs_for("he-IL") == [HE] and L.packs_for("iw") == [HE]


def test_scoring_respects_language():
    he = scoring.lexical_score("וואו אין מצב, זה מטורף!", "he")
    en = scoring.lexical_score("wow no way, that is insane!", "en")
    assert he > 0.3 and en > 0.3, (he, en)
    # טקסט בעברית לא מקבל ציון מחבילת האנגלית
    assert scoring.lexical_score("וואו אין מצב", "en") == 0.0


def test_new_language_plugs_in_without_engine_changes():
    es = L.LanguagePack(code="es", name="Español", direction="ltr",
                        script_ranges=((0x41, 0x5A), (0x61, 0x7A), (0xC0, 0xFF)),
                        stop_words=frozenset({"el", "la", "de"}),
                        lexicon={"increíble": 1.0, "no puede ser": 1.0},
                        hanging_words=frozenset({"de", "la", "el"}))
    L.register_pack(es)
    try:
        assert L.get_pack("es-MX") is es
        assert any(x["code"] == "es" for x in L.available_languages())
        assert scoring.lexical_score("¡Increíble, no puede ser!", "es") > 0.3
        assert es.is_hanging("de")
    finally:
        L._PACKS.pop("es", None)


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
    print(f"\n{passed}/{len(fns)} בדיקות שפה עברו")
    if failed:
        print("נכשלו: " + ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
