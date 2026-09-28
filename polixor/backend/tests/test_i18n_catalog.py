"""
בדיקות שלמות לקטלוג ההודעות של השרת (polixor/locales).

1. לכל מפתח יש טקסט בעברית ובאנגלית.
2. כל מפתח קבוע שהקוד מבקש – ב-`i18n.tr("...")`, ב-`message_key=`
   או ב-`hint_key=` – קיים בקטלוג. מפתח שגוי לא מפיל בקשה, ולכן
   בלי הבדיקה הזו היה מופיע בממשק כמחרוזת גולמית בלי שאיש ישים לב.
3. פרמטרים: כל {שם} בתבנית העברית מופיע גם באנגלית, ולהפך.
4. תבניות דינמיות (f"...{x}") נבדקות מול רשימת הערכים הידועה שלהן.

הרצה:  python3 tests/test_i18n_catalog.py
"""

from __future__ import annotations

import ast
import os
import re
import string
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxi18n_"))

from polixor import i18n  # noqa: E402

SRC = ROOT / "polixor"
KEY_ARGS = ("message_key", "hint_key")


def _code_files() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "locales" not in p.parts]


def _is_tr_call(node: ast.Call) -> bool:
    f = node.func
    return (isinstance(f, ast.Attribute) and f.attr in ("tr", "has")
            and isinstance(f.value, ast.Name) and f.value.id == "i18n")


def _literal_keys() -> list[tuple[str, str]]:
    """(מפתח, מיקום) לכל מפתח קבוע שהקוד מבקש."""
    found: list[tuple[str, str]] = []
    for path in _code_files():
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            where = f"{path.relative_to(ROOT)}:{node.lineno}"
            if _is_tr_call(node) and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.append((arg.value, where))
                elif isinstance(arg, ast.IfExp):
                    for branch in (arg.body, arg.orelse):
                        if isinstance(branch, ast.Constant) and isinstance(branch.value, str):
                            found.append((branch.value, where))
            for kw in node.keywords:
                if kw.arg in KEY_ARGS and isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, str):
                    found.append((kw.value.value, where))
    return found


def _fields(template: str) -> set[str]:
    try:
        return {name for _, name, _, _ in string.Formatter().parse(template) if name}
    except ValueError:
        return set()


# --------------------------------------------------------------------------
def test_every_key_has_hebrew_and_english():
    missing = i18n.missing_translations(["he", "en"])
    assert not missing, f"{len(missing)} תרגומים חסרים, למשל: {missing[:8]}"


def test_literal_keys_exist():
    keys = _literal_keys()
    assert len(keys) > 300, f"נמצאו רק {len(keys)} מפתחות – הסריקה שבורה?"
    unknown = sorted({f"{k} ({w})" for k, w in keys if not i18n.has(k)})
    assert not unknown, "מפתחות שאינם בקטלוג:\n  " + "\n  ".join(unknown[:20])


def test_placeholders_match_between_languages():
    bad = []
    for key in i18n.catalog_keys():
        he = i18n.tr(key, "he")
        en = i18n.tr(key, "en")
        if _fields(he) != _fields(en):
            bad.append(f"{key}: he={sorted(_fields(he))} en={sorted(_fields(en))}")
    assert not bad, "פרמטרים שונים בין השפות:\n  " + "\n  ".join(bad[:20])


def test_dynamic_key_families_are_complete():
    """מפתחות שנבנים בזמן ריצה – לכל ערך אפשרי יש רשומה."""
    from polixor.models import ImageRole, JobStage, LiveState
    from polixor.services import audio_mastering, caption_engine, editing, music_engine
    from polixor.services import pacing_engine
    from polixor.services.video_director import Action

    families = {
        "system.stage.{}": [s.value for s in JobStage],
        "system.live_state.{}": [s.value for s in LiveState],
        "system.image_role.{}": [r.value for r in ImageRole],
        "director.action.{}": [a.value for a in Action],
        "editing.style.{}.label": list(editing.STYLES),
        "director.pacing.{}.label": list(pacing_engine.PROFILES),
        "captions.preset.{}.label": list(caption_engine.PRESETS),
        "music.profile.{}.label": list(music_engine.PROFILES),
        "mastering.target.{}": list(audio_mastering.TARGETS),
        "mastering.step.{}": ["highpass", "denoise", "gate", "level", "compress",
                              "normalize", "limit"],
        "director.hook.verdict.{}": ["strong", "ok", "weak", "missing"],
        "images.provider.{}": ["base", "openai", "placeholder"],
    }
    missing = [pattern.format(v) for pattern, values in families.items()
               for v in values if not i18n.has(pattern.format(v))]
    assert not missing, f"חסרים: {missing}"


def test_labels_follow_the_active_language():
    from polixor.services import editing, pacing_engine

    with i18n.use_lang("en"):
        assert editing.STYLES["dynamic"].label == "Dynamic"
        assert pacing_engine.PROFILES["viral_short"].label == "Viral short"
    with i18n.use_lang("he"):
        assert editing.STYLES["dynamic"].label == "דינמי"
        assert pacing_engine.PROFILES["viral_short"].label == "שורט ויראלי"


def test_localized_error_with_params():
    from polixor.services import images

    for lang, needle in (("en", "too long (5000 characters)"), ("he", "5000 תווים")):
        with i18n.use_lang(lang):
            try:
                images.validate_prompt("x" * 5000)
            except images.ImagePromptError as exc:
                body = exc.to_dict()
                assert needle in body["message"], body
                assert re.search(r"\d", body["hint"]), body
            else:
                raise AssertionError("validate_prompt accepted a 5000-character prompt")


def test_image_worker_keeps_request_language():
    """תהליכון היצירה לא יורש contextvars – השפה חייבת לעבור במפורש."""
    import threading

    from polixor.services.image_assets import ImageWorkers

    seen: dict[str, str] = {}
    done = threading.Event()

    def job(_image_id, _cancel):
        seen["lang"] = i18n.get_lang()
        done.set()

    workers = ImageWorkers(workers=1)
    with i18n.use_lang("en"):
        workers.submit("img-test", job)
    assert done.wait(5), "worker did not run"
    assert seen["lang"] == "en", seen


# --------------------------------------------------------------------------
def _run_all() -> int:
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
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
    print(f"\n{passed}/{len(fns)} בדיקות קטלוג תרגום עברו")
    if failed:
        print("נכשלו: " + ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
