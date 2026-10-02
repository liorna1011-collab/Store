"""
הוו העריכתי: הטקסט הקצר שמופיע על המסך בתחילת הקליפ (נפרד מהכתוביות).

שני מקורות למועמדים:
  * מתוך הקליפ עצמו: השאלה האמיתית, העמדה החדה, הוויכוח, ההשוואה או
    השורה התחתונה – מקוצרים לביטוי של 3–9 מילים (בלי פתיח ריק, בלי זנב,
    בלי פנייה „אחי"). כל מילה נאמרה בקליפ.
  * מודל שפה (אם הוגדר): ניסוח עריכתי חופשי יותר – אבל רק עם ציטוט מדויק
    מהקליפ כראיה, ורק כשכל מילת תוכן בו נאמרה בקליפ (או היא מילת מסגור
    עריכתית כללית כמו „למה", „הדעה", „הטעות"). מספרים ושמות – רק אם נאמרו.

נפסלים תמיד: קליקבייט גנרי („לא תאמינו מה קרה", „אתם חייבים לראות את
זה") – אלא אם המילים האלה נאמרו בקליפ עצמו; הוו בלי תוכן ספציפי; פתיחה
בכינוי בלי שברור על מי מדובר; יותר משתי שורות.

הדירוג: סוג הסיבה לצפות (ויכוח, שאלה, עמדה…), אורך, ספציפיות, ביטחון
הזיהוי של המילים, והאם הוא מגלה את הסוף מראש. הכותרת לפוסט היא המועמד
הטוב ביותר בצורת טענה (לא שאלה), כשיש כזה.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Sequence

from .. import lang as _lang
from .score import Scored
from .story import semantic_hooks
from .titles import clean_line
from .units import Unit

log = logging.getLogger("polixor.clip_intel.editorial")

MIN_WORDS, MAX_WORDS = 3, 9
MAX_CHARS = 52               # שתי שורות קצרות לכל היותר
MAX_LLM_WORDS = 11
MIN_HOOK_SCORE = 0.40

CATEGORY_WEIGHTS = {"conflict": 0.42, "meaningful_question": 0.40, "opinion": 0.36,
                    "comparison": 0.30, "verdict": 0.28, "claim": 0.28, "story": 0.20,
                    "emotion": 0.14, "hook_words": 0.12}

_PREFIXES = ("וכש", "לכש", "שה", "וה", "וש", "וב", "ול", "ומ", "כש", "ו", "ה", "ש", "ב", "ל", "מ", "כ")
_FINALS = str.maketrans({"ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ"})
_PUNCT = re.compile(r"[^\w\s֐-׿']", re.UNICODE)
_NIQQUD = re.compile(r"[֑-ׇ]")
_PRONOUN_START = ("הוא", "היא", "הם", "הן", "זה", "זאת", "זו", "אותו", "אותה", "לו", "לה", "he", "she",
                  "they", "it", "this", "that")
_VOCATIVE = re.compile(r"(^|[\s,])(אחי|אחשלי|גבר|bro|dude)(?=[\s,?.!…]|$)[,]?", re.IGNORECASE)


@dataclass
class HookCandidate:
    text: str
    source: str                       # question | opinion | conflict | verdict | … | llm
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    evidence: str = ""                # הטקסט מהקליפ שהוו נשען עליו
    unit: int = -1
    question: bool = False
    rejected: str = ""                # סיבת פסילה (ריק = תקין)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items()}


# --------------------------------------------------------------------------
# נרמול ועיגון
# --------------------------------------------------------------------------
def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    t = _NIQQUD.sub("", "".join(ch for ch in t if not unicodedata.combining(ch)))
    t = _PUNCT.sub(" ", t.lower()).translate(_FINALS)
    return " ".join(t.split())


def _stem(tok: str) -> str:
    t = tok
    for p in _PREFIXES:
        if t.startswith(p) and len(t) - len(p) >= 3:
            t = t[len(p):]
            break
    return t


def _tokens(text: str) -> list[str]:
    return [t for t in _norm(text).split() if t]


def _grounded(tok: str, clip_stems: set[str]) -> bool:
    st = _stem(tok)
    if st in clip_stems or tok in clip_stems:
        return True
    if len(st) < 3:
        return False
    return any(len(c) >= 3 and difflib.SequenceMatcher(None, st, c).ratio() >= 0.8 for c in clip_stems)


def _framing_words(packs: Sequence[Any]) -> set[str]:
    out: set[str] = set()
    for p in packs:
        out |= {_norm(w) for w in getattr(p, "stop_words", frozenset())}
        out |= {_norm(w) for w in getattr(p, "editorial_framing", ())}
    return out


def _empty_words(language: Optional[str]) -> set[str]:
    """מילים שלא נותנות תוכן לוו: קריאות, פתיחי סטרים, מילוי („רגע", „תקשיבו", „יאללה")."""
    out: set[str] = set()
    for p in _lang.packs_for(language):
        for f in ("hook", "throat_clearing", "cheers", "filler_phrases", "trailing_tags",
                  "reaction_tokens"):
            for ph in getattr(p, f, ()) or ():
                n = _norm(ph)
                if n and " " not in n:
                    out.add(n)
        out |= {_norm(w) for w in getattr(p, "filler_tokens", frozenset())}
    return out


def faithfulness(text: str, clip_text: str, language: Optional[str]) -> tuple[bool, str]:
    """
    האם הוו נאמן לקליפ: כל מילת תוכן נאמרה בו (בכל הטיה/תחילית), למעט מילת
    מסגור עריכתית כללית אחת לכל היותר מחוץ לרשימה; מספרים ושמות באנגלית –
    רק אם נאמרו.
    """
    packs = _lang.packs_for(language)
    clip_tokens = _tokens(clip_text)
    stems = {_stem(t) for t in clip_tokens} | set(clip_tokens)
    framing = _framing_words(packs)
    content = [t for t in _tokens(text) if t not in framing and _stem(t) not in framing]
    if not content:
        return False, "no_content"
    loose = [t for t in content if not _grounded(t, stems)]
    for t in loose:
        if re.search(r"\d", t) or re.search(r"[a-z]", t):
            return False, "ungrounded_name_or_number"
    grounded = len(content) - len(loose)
    if grounded == 0:
        return False, "nothing_from_clip"
    if len(loose) > 1:
        return False, "ungrounded_words"
    return True, ""


def clickbait(text: str, clip_text: str, language: Optional[str]) -> bool:
    """קליקבייט גנרי – אלא אם הניסוח הזה נאמר בקליפ עצמו."""
    low, clip = _norm(text), _norm(clip_text)
    for p in _lang.packs_for(language):
        for ph in getattr(p, "clickbait", ()):
            n = _norm(ph)
            if n and n in low and n not in clip:
                return True
    return False


# --------------------------------------------------------------------------
# מועמדים מהקליפ
# --------------------------------------------------------------------------
def _drop_vocatives(text: str) -> str:
    t = _VOCATIVE.sub(" ", text)
    t = re.sub(r"\s+([,?.!…])", r"\1", t)
    t = re.sub(r",\s*([,?.!…])", r"\1", t)
    return re.sub(r"\s+", " ", t).strip(" ,")


def _clauses(text: str) -> list[str]:
    parts = re.split(r"(?<=[,.;:!?…])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _has_marker(text: str, packs: Sequence[Any]) -> bool:
    for p in packs:
        for f in ("stance_markers", "conflict_markers", "comparison_markers", "verdict_markers",
                  "curiosity", "claim"):
            if p.count(text, getattr(p, f, ()) or ()):
                return True
    return False


def _core_phrases(u: Unit, language: Optional[str]) -> list[str]:
    """ביטויי ליבה מהמשפט: המשפט כולו (אם קצר), והפסוקית עם הסימן החזק."""
    packs = _lang.packs_for(language)
    out = []
    whole = clean_line(_drop_vocatives(u.text), language, shorten=False)
    out.append(whole)
    clauses = _clauses(_drop_vocatives(u.text))
    for i, c in enumerate(clauses):
        if not _has_marker(c, packs) and "?" not in c:
            continue
        # פסוקית קצרה מדי מצטרפת לבאה אחריה („הבעיה, …")
        cand = c
        if len(cand.split()) < MIN_WORDS and i + 1 < len(clauses):
            cand = f"{c} {clauses[i + 1]}"
        out.append(clean_line(cand, language, shorten=False))
    seen, uniq = set(), []
    for t in out:
        t = t.strip(" ,;:–—")
        if t and t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def _category(u: Unit) -> str:
    cats = semantic_hooks(u)
    if u.has("verdict_markers"):
        cats.append("verdict")
    best = max(cats, key=lambda c: CATEGORY_WEIGHTS.get(c, 0.0), default="")
    return best


def deterministic(sc: Scored, units: Sequence[Unit], language: Optional[str]) -> list[HookCandidate]:
    p = sc.proposal
    out: list[HookCandidate] = []
    for k in range(p.hook_idx, p.end_idx + 1):
        u = units[k]
        if u.private or u.garbled:
            continue
        cat = _category(u)
        if not cat:
            continue
        for text in _core_phrases(u, language):
            out.append(HookCandidate(text=text, source=cat, evidence=u.text, unit=k,
                                     question=text.endswith("?")))
    return out


# --------------------------------------------------------------------------
# מודל שפה (אופציונלי)
# --------------------------------------------------------------------------
SYSTEM = (
    "You are a senior Hebrew social-video editor. Write the short on-screen hook text for the start "
    "of a Reels/TikTok/Shorts clip, and a short post title. Rules: natural spoken Hebrew (or the "
    "clip's language), 3-9 words, specific to THIS clip, truthful, curiosity without fake clickbait. "
    "Never use generic lines like 'you won't believe this', 'you must see this', 'crazy moment', "
    "'what do you think?'. You may paraphrase, but every fact, name and number must come from the "
    "clip, and each item must cite an EXACT quote from the clip as evidence. Answer ONLY with JSON: "
    "{\"hooks\": [{\"text\": \"...\", \"evidence\": \"...\"}], "
    "\"titles\": [{\"text\": \"...\", \"evidence\": \"...\"}]} with up to 4 of each."
)


def llm_candidates(sc: Scored, units: Sequence[Unit], settings: Any,
                   language: Optional[str]) -> tuple[list[HookCandidate], list[HookCandidate]]:
    from .. import llm

    if settings is None or not llm.is_llm_enabled(settings) \
            or not getattr(settings, "editorial_hook_llm", True):
        return [], []
    p = sc.proposal
    clip = [units[k].text for k in range(p.hook_idx, p.end_idx + 1)]
    try:
        raw = llm.call_model(SYSTEM, json.dumps({"language": language, "clip": clip},
                                                ensure_ascii=False), settings)
        data = llm._parse_json(raw)
    except Exception as exc:                              # noqa: BLE001
        log.info("editorial hook model unavailable: %s", exc)
        return [], []
    if not isinstance(data, dict):
        return [], []

    def items(key: str) -> list[HookCandidate]:
        out = []
        for it in (data.get(key) or [])[:6]:
            if not isinstance(it, dict):
                continue
            text = re.sub(r"\s+", " ", str(it.get("text") or "")).strip().strip('"“”')
            ev = str(it.get("evidence") or "")[:300]
            if text:
                out.append(HookCandidate(text=text, source="llm", evidence=ev,
                                         question=text.endswith("?")))
        return out
    return items("hooks"), items("titles")


# --------------------------------------------------------------------------
# בדיקה ודירוג
# --------------------------------------------------------------------------
def check(c: HookCandidate, clip_text: str, language: Optional[str],
          low_conf: float = 0.0) -> HookCandidate:
    words = c.text.split()
    limit = MAX_LLM_WORDS if c.source == "llm" else MAX_WORDS
    if len(words) < MIN_WORDS:
        c.rejected = "too_short"
    elif len(words) > limit or len(c.text) > MAX_CHARS:
        c.rejected = "too_long"
    elif clickbait(c.text, clip_text, language):
        c.rejected = "generic_clickbait"
    elif c.source == "llm" and (not c.evidence or _norm(c.evidence) not in _norm(clip_text)
                                or len(_norm(c.evidence).split()) < 2):
        c.rejected = "unsupported_evidence"
    else:
        ok, why = faithfulness(c.text, clip_text, language)
        if not ok:
            c.rejected = why
    if c.rejected:
        return c
    v = CATEGORY_WEIGHTS.get(c.source, 0.30)
    reasons = [c.source]
    n = len(words)
    if 4 <= n <= 8:
        v += 0.15
    elif n in (3, 9):
        v += 0.05
    if c.question:
        v += 0.06
        reasons.append("question")
    if words[0].strip(",.?!").lower() in _PRONOUN_START:
        v -= 0.2
        reasons.append("unclear_reference")
    if c.source == "verdict":
        v -= 0.05                                  # מגלה את הסוף מראש
        reasons.append("reveals_ending")
    if re.search(r"\d|[A-Za-z]", c.text):
        v += 0.04
        reasons.append("specific")
    # ספציפיות: כמה מילות תוכן (לא מילות קישור/מסגור) יש בו
    framing = _framing_words(_lang.packs_for(language)) | _empty_words(language)
    content = list(dict.fromkeys(t for t in _tokens(c.text) if t not in framing and _stem(t) not in framing))
    if len(content) <= 1:
        v -= 0.12
        reasons.append("generic")
    else:
        v += min(0.15, 0.04 * len(content))
    if c.source == "llm":
        v += 0.08
    v -= 0.3 * low_conf
    c.score = round(v, 3)
    c.reasons = reasons
    return c


def build(sc: Scored, units: Sequence[Unit], language: Optional[str],
          settings: Any = None) -> dict[str, Any]:
    """הוו העריכתי והכותרת לקליפ, עם כל המועמדים (גם הנפסלים, לשקיפות)."""
    p = sc.proposal
    clip_text = " ".join(units[k].text for k in range(p.hook_idx, p.end_idx + 1))
    det = deterministic(sc, units, language)
    hooks_llm, titles_llm = llm_candidates(sc, units, settings, language)
    checked: list[HookCandidate] = []
    for c in det + hooks_llm:
        lc = units[c.unit].low_conf if c.unit >= 0 else 0.0
        checked.append(check(c, clip_text, language, lc))
    titles_checked = [check(c, clip_text, language) for c in titles_llm]
    ok = sorted((c for c in checked if not c.rejected), key=lambda c: -c.score)
    seen: set[str] = set()
    uniq = []
    for c in ok:
        key = _norm(c.text)
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    # עדיף בלי וו מאשר וו חלש: מתחת לסף לא מציירים (והשער האחרון מעיר על כך)
    hook = uniq[0] if uniq and uniq[0].score >= MIN_HOOK_SCORE else None
    statements = sorted([c for c in uniq + [t for t in titles_checked if not t.rejected]
                         if not c.question], key=lambda c: -c.score)
    title = next((c for c in statements if hook is None or _norm(c.text) != _norm(hook.text)), None)
    title = title or hook
    return {
        "hook": hook.text if hook else "",
        "hook_source": hook.source if hook else "",
        "title": title.text if title else "",
        "candidates": [c.to_dict() for c in uniq[:8]],
        "rejected": [c.to_dict() for c in checked + titles_checked if c.rejected][:12],
    }
