"""
לוקליזציה בצד השרת: קטלוג הודעות אחד לכל השפות.

עקרונות:
  * מקור אחד לכל טקסט שמוצג למשתמש: `tr("namespace.key", lang, **params)`.
  * כל קובץ ב-`polixor/locales/` מגדיר `NAMESPACE` ו-`MESSAGES`, והוא
    מתגלה אוטומטית. הוספת namespace או שפה חדשה היא הוספת נתונים בלבד:
    אין צורך לגעת בקוד.
  * שרשרת נפילה: השפה המבוקשת ← אנגלית ← עברית ← המפתח עצמו. מפתח חסר
    לעולם אינו מפיל בקשה; הוא מופיע כמו שהוא, וכך קל לזהות אותו.
  * השפה הפעילה נשמרת ב-contextvar: ה-middleware קובע אותה לכל בקשה,
    והפייפליין קובע אותה לכל ריצה (`use_lang(job.ui_language)`).

זיהוי שפת הבקשה אינו משתמש בכתובת IP: רק `?lang=`, הכותרת
`X-Polixor-Lang` ו-`Accept-Language`. ברירת המחדל היא עברית.
"""

from __future__ import annotations

import contextvars
import importlib
import logging
import pkgutil
import string
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional

log = logging.getLogger("polixor.i18n")

DEFAULT_LANG = "he"
# שפות הנפילה, לפי הסדר, כשאין תרגום בשפה המבוקשת
FALLBACK_CHAIN: tuple[str, ...] = ("en", "he")

# מטא-דאטה לשפות שהממשק מכיר. שפה חדשה שמופיעה בקטלוג בלי רשומה
# כאן עדיין עובדת; היא פשוט מוצגת עם הקוד שלה ובכיוון LTR.
LANGUAGE_META: dict[str, dict[str, str]] = {
    "he": {"name": "עברית", "english_name": "Hebrew", "direction": "rtl"},
    "en": {"name": "English", "english_name": "English", "direction": "ltr"},
}

# קודים ישנים או חלופיים
_ALIASES: dict[str, str] = {"iw": "he", "heb": "he", "eng": "en"}

_catalog: dict[str, dict[str, str]] = {}      # "ns.key" -> {lang: text}
_languages: set[str] = set(LANGUAGE_META)
_lock = threading.RLock()
_loaded = False

_current: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "polixor_lang", default=None)


# --------------------------------------------------------------------------
# רישום קטלוגים
# --------------------------------------------------------------------------
def register(namespace: str, entries: dict[str, dict[str, str]]) -> None:
    """
    רושם הודעות תחת namespace. `entries` הוא {מפתח: {שפה: טקסט}}.

    רישום חוזר של אותו מפתח משלים או מחליף תרגומים, ולכן אפשר להוסיף
    שפה חדשה מקובץ נפרד בלי לגעת בקטלוג המקורי.
    """
    ns = namespace.strip(".")
    with _lock:
        for key, texts in (entries or {}).items():
            full = f"{ns}.{key}" if ns else key
            slot = _catalog.setdefault(full, {})
            for lang, text in (texts or {}).items():
                code = _canonical(lang)
                if not code or text is None:
                    continue
                slot[code] = str(text)
                _languages.add(code)


def _ensure_loaded() -> None:
    """טוען פעם אחת את כל קובצי `locales/*.py`."""
    global _loaded
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        from . import locales

        for mod in pkgutil.iter_modules(locales.__path__):
            if mod.name.startswith("_"):
                continue
            module = importlib.import_module(f"{locales.__name__}.{mod.name}")
            namespace = getattr(module, "NAMESPACE", mod.name)
            messages = getattr(module, "MESSAGES", None)
            if isinstance(messages, dict):
                register(namespace, messages)
        _loaded = True


# --------------------------------------------------------------------------
# קודי שפה
# --------------------------------------------------------------------------
def _canonical(value: Optional[str]) -> str:
    """מנרמל קוד שפה לצורה בסיסית (he-IL → he, iw → he) בלי לבדוק תמיכה."""
    if not value:
        return ""
    code = str(value).strip().lower().replace("_", "-")
    code = code.split(";", 1)[0].strip()
    base = code.split("-", 1)[0]
    return _ALIASES.get(base, base)


def normalize_lang(value: Optional[str]) -> Optional[str]:
    """
    ממיר קוד שפה לקוד נתמך, או None אם השפה אינה נתמכת.

    'he-IL' → 'he', 'iw' → 'he', 'en_US' → 'en', 'fr' → None (כל עוד אין
    קטלוג בצרפתית).
    """
    code = _canonical(value)
    if not code:
        return None
    _ensure_loaded()
    return code if code in _languages else None


def supported_languages() -> list[str]:
    _ensure_loaded()
    ordered = [c for c in LANGUAGE_META if c in _languages]
    return ordered + sorted(c for c in _languages if c not in LANGUAGE_META)


def available_languages() -> list[dict[str, str]]:
    """רשימת השפות לממשק: קוד, שם וכיוון."""
    out = []
    for code in supported_languages():
        meta = LANGUAGE_META.get(code, {})
        out.append({"code": code, "name": meta.get("name", code),
                    "english_name": meta.get("english_name", code),
                    "direction": meta.get("direction", "ltr")})
    return out


def direction(lang: Optional[str] = None) -> str:
    code = normalize_lang(lang) or get_lang()
    return LANGUAGE_META.get(code, {}).get("direction", "ltr")


def parse_accept_language(header: Optional[str]) -> Optional[str]:
    """
    בוחר את השפה הנתמכת המועדפת מכותרת Accept-Language.

    'en-US,en;q=0.9,he;q=0.8' → 'en'. מחזיר None כשאף שפה אינה נתמכת.
    """
    if not header:
        return None
    ranked: list[tuple[float, int, str]] = []
    for pos, part in enumerate(str(header).split(",")):
        piece = part.strip()
        if not piece:
            continue
        lang, _, params = piece.partition(";")
        q = 1.0
        for p in params.split(";"):
            p = p.strip()
            if p.startswith("q="):
                try:
                    q = float(p[2:])
                except ValueError:
                    q = 0.0
        if q <= 0:
            continue
        ranked.append((-q, pos, lang.strip()))
    for _q, _pos, lang in sorted(ranked):
        code = normalize_lang(lang)
        if code:
            return code
    return None


# --------------------------------------------------------------------------
# השפה הפעילה
# --------------------------------------------------------------------------
def get_lang() -> str:
    return _current.get() or DEFAULT_LANG


def set_lang(lang: Optional[str]) -> contextvars.Token:
    return _current.set(normalize_lang(lang) or DEFAULT_LANG)


def reset_lang(token: contextvars.Token) -> None:
    try:
        _current.reset(token)
    except ValueError:
        # הטוקן נוצר בהקשר אחר (למשל בתהליכון אחר); מחזירים לברירת מחדל
        _current.set(None)


@contextmanager
def use_lang(lang: Optional[str]) -> Iterator[str]:
    """מריץ בלוק קוד בשפה נתונה, ומחזיר את הקודמת בסופו."""
    token = set_lang(lang)
    try:
        yield get_lang()
    finally:
        reset_lang(token)


# --------------------------------------------------------------------------
# תרגום
# --------------------------------------------------------------------------
class _SafeParams(dict):
    """פרמטר חסר נשאר כ-{name} במקום להפיל את התרגום."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


_FORMATTER = string.Formatter()


def _format(template: str, params: dict[str, Any]) -> str:
    if not params or "{" not in template:
        return template
    try:
        return _FORMATTER.vformat(template, (), _SafeParams(params))
    except (ValueError, IndexError, KeyError, AttributeError) as exc:
        log.debug("i18n format failed for %r: %s", template, exc)
        return template


def has(key: str) -> bool:
    _ensure_loaded()
    return key in _catalog


def tr(key: str, lang: Optional[str] = None, *, default: Optional[str] = None,
       **params: Any) -> str:
    """
    מתרגם מפתח לשפה. `lang` ריק => השפה הפעילה בהקשר.

    `default` מוחזר כשהמפתח אינו קיים כלל (שימושי לערכים דינמיים).
    """
    _ensure_loaded()
    code = normalize_lang(lang) if lang else None
    code = code or get_lang()
    entry = _catalog.get(key)
    if not entry:
        text = default if default is not None else key
        return _format(text, params)
    text = entry.get(code)
    if text is None:
        for fb in FALLBACK_CHAIN:
            if fb in entry:
                text = entry[fb]
                break
        else:
            text = next(iter(entry.values()))
    return _format(text, params)


def tr_all(key: str, **params: Any) -> dict[str, str]:
    """אותו מפתח בכל השפות הנתמכות – למשל עבור תוויות presets."""
    return {code: tr(key, code, **params) for code in supported_languages()}


def missing_translations(langs: Optional[list[str]] = None) -> list[str]:
    """
    מפתחות שחסר להם תרגום באחת השפות. משמש לבדיקת שלמות.

    מוחזרים כ-"lang:ns.key", ממוינים.
    """
    _ensure_loaded()
    wanted = [c for c in (langs or list(LANGUAGE_META)) if c]
    out: list[str] = []
    with _lock:
        for key, texts in _catalog.items():
            for code in wanted:
                if not str(texts.get(code, "")).strip():
                    out.append(f"{code}:{key}")
    return sorted(out)


def catalog_keys(namespace: str = "") -> list[str]:
    _ensure_loaded()
    prefix = f"{namespace}." if namespace else ""
    return sorted(k for k in _catalog if k.startswith(prefix))
