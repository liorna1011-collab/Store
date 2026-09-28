"""
החלטה על חומר נלווה (B-roll): מתי הוא באמת עוזר, ומתי הדובר עדיף.

השאלה המרכזית כאן אינה „איזו תמונה מתאימה למשפט" אלא **האם בכלל
צריך תמונה**. תמונה לכל משפט הופכת סרטון אישי לסרט תדמית, ומרחיקה
את הצופה מהדובר בדיוק ברגעים שבהם הוא הכי חשוב.

לכן כל משפט מקבל הכרעה מפורשת — `broll` או `talking_head` — עם
הנימוק. שני אותות מכריעים:

  מוחשיות   האם אפשר לצלם את מה שנאמר. „נסעתי לבד בלילה בכביש ריק"
            הוא תיאור חזותי; „אני חושב שזה רעיון טוב" אינו. משפט
            מופשט מקבל תמונה גנרית שלא מוסיפה כלום.
  תפקיד     בשיא רגשי, בפאנץ' ובקריאה לפעולה — הפנים של הדובר הן
            התוכן. חיתוך מהן לתמונה מוריד את עוצמת הרגע. הגבלה זו
            מגיעה מ-`pacing_engine` ונאכפת כאן שוב.

העדפת מקורות: חומר שכבר קיים (הועלה או נוצר קודם) עדיף על יצירה
חדשה — הוא זול יותר, עקבי יותר ויזואלית, ולא מוסיף זמן המתנה.

המנוע מחזיר החלטות בלבד. הוא אינו יוצר תמונות ואינו משבץ אותן.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from . import lang as _lang
from .semantics import (
    SemanticAnalysis, Sentence, _hits, _norm, _pack_hits, _phrase_pattern,
)

log = logging.getLogger("polixor.broll")

# --------------------------------------------------------------------------
# לקסיקונים
# --------------------------------------------------------------------------
# הלקסיקונים נמצאים בחבילות השפה; השמות הישנים נשארים לתאימות לאחור
_HE, _EN = _lang.HEBREW, _lang.ENGLISH
PLACE_HE, PLACE_EN = list(_HE.places), list(_EN.places)
OBJECT_HE, OBJECT_EN = list(_HE.objects), list(_EN.objects)
MOTION_HE, MOTION_EN = list(_HE.motions), list(_EN.motions)
NATURE_HE, NATURE_EN = list(_HE.nature), list(_EN.nature)
# ניסוחים שמסמנים דעה או הפשטה — שם תמונה לא מוסיפה מידע
ABSTRACT_HE, ABSTRACT_EN = list(_HE.abstract), list(_EN.abstract)
# דיבור על הסרטון עצמו — שם הדובר תמיד עדיף
META_HE, META_EN = list(_HE.meta), list(_EN.meta)

# תפקידים שבהם הדובר הוא התוכן — תמיד.
# פתיח וקריאה לפעולה הם פנייה ישירה לצופה; חיתוך מהפנים שם שובר
# את הקשר, ואין תמונה ששווה את זה.
FACE_ALWAYS = frozenset({"hook", "cta"})

# תפקידים שבהם הפנים מנצחות **אלא אם** המשפט מצייר סצנה מוחשית.
# שיא רגשי שמתאר משהו שאפשר לראות — „נסעתי לבד בלילה בכביש ריק" —
# הוא בדיוק המקום שבו חומר נלווה משרת את הרגש במקום להחליף אותו.
# כשהרגש נישא בעיקר בהבעה ובקול, התמונה רק מסיטה את המבט.
FACE_UNLESS_VISUAL = frozenset({"emotional_peak", "payoff"})

FACE_ROLES = FACE_ALWAYS | FACE_UNLESS_VISUAL

# רף גבוה: רק תיאור חזותי חזק גובר על הפנים
HIGH_CONCRETE = 0.70

# --------------------------------------------------------------------------
CONCRETE_ENOUGH = 0.42      # מתחת לזה — תמונה תהיה גנרית
MIN_SENTENCE_SECONDS = 1.6  # משפט קצר מזה לא מספיק לחומר נלווה
MIN_GAP_SECONDS = 6.0       # מרווח מינימלי בין הכנסות
DEFAULT_DURATION = 3.0


@dataclass
class MediaAsset:
    """נכס קיים שאפשר להשתמש בו במקום לייצר חדש."""

    id: str
    description: str = ""
    kind: str = "image"        # image | video
    tags: list[str] = field(default_factory=list)
    is_ai: bool = False

    def haystack(self) -> str:
        return _norm(" ".join([self.description] + list(self.tags)))


@dataclass
class BrollDecision:
    start: float
    end: float
    text: str
    role: str
    verdict: str               # broll | talking_head
    reason: str
    confidence: float
    concreteness: float
    category: str = ""
    source: str = "none"       # existing | generate | none
    asset_id: str = ""
    query: str = ""
    prompt: str = ""
    duration: float = DEFAULT_DURATION

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 3), "end": round(self.end, 3),
            "text": self.text, "role": self.role, "verdict": self.verdict,
            "reason": self.reason, "confidence": round(self.confidence, 3),
            "concreteness": round(self.concreteness, 3),
            "category": self.category, "source": self.source,
            "asset_id": self.asset_id, "query": self.query,
            "prompt": self.prompt, "duration": round(self.duration, 2),
        }


@dataclass
class BrollPlan:
    decisions: list[BrollDecision] = field(default_factory=list)
    budget: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def inserts(self) -> list[BrollDecision]:
        return [d for d in self.decisions if d.verdict == "broll"]

    @property
    def talking_head(self) -> list[BrollDecision]:
        return [d for d in self.decisions if d.verdict == "talking_head"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget": self.budget,
            "inserts": len(self.inserts),
            "decisions": [d.to_dict() for d in self.decisions],
            "notes": self.notes,
        }


# --------------------------------------------------------------------------
# מוחשיות
# --------------------------------------------------------------------------
_NUMBER_UNIT = re.compile(
    r"\d+\s*(ק\"מ|קמ|מטר|שנים|שנה|חודש|ימים|שעות|דקות|km|miles|years?|"
    r"months?|days?|hours?)")


def concreteness(text: str, language: Optional[str] = None) -> tuple[float, str]:
    """
    עד כמה אפשר לצלם את מה שנאמר, ובאיזו קטגוריה.

    מחזיר ציון 0..1 ואת הקטגוריה החזקה ביותר. הציון נבנה מהצטברות
    של סימנים חזותיים, ומנוכה בניסוחים מופשטים ובדיבור על הסרטון
    עצמו — שם תמונה תמיד תהיה גנרית.
    """
    low = _norm(text)
    if not low:
        return 0.0, ""

    packs = _lang.packs_for(language)
    groups = {
        "place": _pack_hits(text, "places", packs),
        "object": _pack_hits(text, "objects", packs),
        "action": _pack_hits(text, "motions", packs),
        "nature": _pack_hits(text, "nature", packs),
    }
    total = sum(groups.values())
    best = max(groups, key=lambda k: groups[k]) if total else ""

    score = min(1.0, 0.34 * total)
    if _NUMBER_UNIT.search(low):
        score += 0.12                       # „נסעתי 300 ק\"מ" — ניתן לצילום
    abstract = _pack_hits(text, "abstract", packs)
    meta = _pack_hits(text, "meta", packs)
    score -= 0.28 * abstract
    score -= 0.5 * meta

    score = max(0.0, min(1.0, score))
    if meta:
        return score, "meta"
    if not total and abstract:
        return score, "abstract"
    return score, best


# --------------------------------------------------------------------------
# התאמה לחומר קיים
# --------------------------------------------------------------------------
def match_existing(text: str, assets: Sequence[MediaAsset]
                   ) -> tuple[Optional[MediaAsset], float]:
    """
    מוצא נכס קיים שמתאים למשפט.

    ההתאמה היא על מילות תוכן בגבולות מילה, ולא על תת-מחרוזות:
    „לבד" בתוך „לבדוק" הוא בדיוק סוג ההתאמה שמייצר תמונה שגויה.
    """
    if not assets:
        return None, 0.0
    words = [w for w in _content_words(text) if len(w) > 2]
    if not words:
        return None, 0.0

    forms = [_forms(w) for w in words]
    best, best_score = None, 0.0
    for asset in assets:
        hay = asset.haystack()
        if not hay:
            continue
        hit = sum(1 for variants in forms
                  if any(_phrase_pattern(v).search(hay) for v in variants))
        if not hit:
            continue
        score = hit / len(words)
        if score > best_score:
            best, best_score = asset, score
    return (best, best_score) if best_score >= 0.34 else (None, best_score)


def _forms(word: str) -> list[str]:
    """המילה עצמה, ובנוסף הצורה בלי תחילית דבוקה כשהיא נשארת מילה."""
    out = [word]
    for pack in _lang.packs_for(None):
        stem = pack.strip_prefix(word) if pack.prefixes else None
        if stem:
            out.append(stem)
            break
    return out


# מילים שאינן תוכן, מכל החבילות
_STOP = set().union(*(p.stop_words for p in _lang.packs_for(None)))


def _content_words(text: str) -> list[str]:
    toks = re.findall(r"[\w֐-׿']+", text or "")
    return [t for t in toks if t.lower() not in _STOP and len(t) > 1]


# --------------------------------------------------------------------------
# התכנון
# --------------------------------------------------------------------------
def plan_broll(sem: SemanticAnalysis, pacing=None, *,
               assets: Optional[Sequence[MediaAsset]] = None,
               budget: Optional[int] = None,
               aspect: str = "9:16",
               style: str = "",
               language: Optional[str] = None) -> BrollPlan:
    """
    מכריע לכל משפט: חומר נלווה או הדובר.

    `budget` — כמה הכנסות מותרות. כשלא נמסר, נלקח מתכנית הקצב,
    שמחשבת אותו לפי אורך הסרטון ולפי הסגנון. אין תקציב → אין
    הכנסות, וזו החלטה לגיטימית.
    """
    plan = BrollPlan()
    language = language or getattr(sem, "language", None) or None
    if not sem.sentences:
        plan.notes.append("אין תמלול — אי אפשר להחליט על חומר נלווה.")
        return plan

    if budget is None:
        budget = int(getattr(pacing, "total_broll_slots", 0) or 0)
    plan.budget = max(0, int(budget))

    assets = list(assets or [])
    candidates: list[tuple[float, BrollDecision]] = []

    for s in sem.sentences:
        decision = _judge(s, pacing, assets, aspect=aspect, style=style,
                          language=language)
        plan.decisions.append(decision)
        if decision.verdict == "broll":
            candidates.append((decision.concreteness, decision))

    if plan.budget <= 0:
        for _, d in candidates:
            d.verdict = "talking_head"
            d.source = "none"
            d.reason = ("הסגנון הזה לא מקצה חומר נלווה — הסרטון נשאר "
                        "על הדובר.")
        if candidates:
            plan.notes.append(
                f"{len(candidates)} משפטים היו מתאימים לחומר נלווה, אבל "
                "הסגנון הנוכחי אינו מקצה לו מקום.")
        return plan

    # בוחרים את המוחשיים ביותר, עם מרווח מינימלי ביניהם
    candidates.sort(key=lambda p: -p[0])
    chosen: list[BrollDecision] = []
    for _, d in candidates:
        if len(chosen) >= plan.budget:
            d.verdict = "talking_head"
            d.source = "none"
            d.reason += " · לא נבחר: נגמרה מכסת החומר הנלווה של הסגנון"
            continue
        if any(abs(d.start - c.start) < MIN_GAP_SECONDS for c in chosen):
            d.verdict = "talking_head"
            d.source = "none"
            d.reason += (f" · לא נבחר: קרוב מדי להכנסה אחרת "
                         f"(מרווח מינימלי {MIN_GAP_SECONDS:.0f} שנ')")
            continue
        chosen.append(d)

    reused = sum(1 for d in chosen if d.source == "existing")
    if reused:
        plan.notes.append(
            f"{reused} מתוך {len(chosen)} ההכנסות משתמשות בחומר שכבר "
            "קיים — זול יותר, מהיר יותר ועקבי יותר ויזואלית.")
    if len(candidates) > len(chosen):
        plan.notes.append(
            f"נבחרו {len(chosen)} הכנסות מתוך {len(candidates)} מועמדות. "
            "חומר נלווה על כל משפט מרחיק את הצופה מהדובר.")
    return plan


def _judge(s: Sentence, pacing, assets: Sequence[MediaAsset], *,
           aspect: str, style: str, language: Optional[str] = None) -> BrollDecision:
    """ההכרעה למשפט אחד, עם הנימוק."""
    score, category = concreteness(s.text, language)
    d = BrollDecision(
        start=s.start, end=s.end, text=s.text, role=s.role,
        verdict="talking_head", reason="", confidence=0.5,
        concreteness=score, category=category,
        duration=min(DEFAULT_DURATION, max(1.5, s.end - s.start)))

    # 1. פנייה ישירה לצופה — הפנים תמיד מנצחות
    if s.role in FACE_ALWAYS:
        d.reason = (f"{_ROLE_HE.get(s.role, s.role)}: פנייה ישירה לצופה. "
                    "חיתוך מהפנים כאן שובר את הקשר.")
        d.confidence = 0.88
        return d

    # 2. דיבור על הסרטון עצמו — סימן ספציפי יותר מהתפקיד, ולכן
    #    נבדק לפניו כדי שההסבר יהיה המדויק ביותר
    if category == "meta":
        d.reason = ("המשפט מדבר על הסרטון עצמו ולא על משהו שאפשר "
                    "לצלם — תמונה כאן תהיה קישוט.")
        d.confidence = 0.8
        return d

    # 3. רגע רגשי: הפנים מנצחות אלא אם המשפט מצייר סצנה מוחשית
    if s.role in FACE_UNLESS_VISUAL and score < HIGH_CONCRETE:
        d.reason = (f"{_ROLE_HE.get(s.role, s.role)}: הרגש נישא בהבעה "
                    "ובקול, ואין כאן תיאור חזותי חזק מספיק שיצדיק "
                    "חיתוך מהדובר.")
        d.confidence = 0.82
        return d

    # 4. הקטע חסום בתכנית הקצב — אלא אם התיאור חזותי במיוחד
    section = pacing.at(s.start) if pacing is not None else None
    if section is not None and not section.allow_broll \
            and score < HIGH_CONCRETE:
        d.reason = f"תכנית הקצב אינה מאפשרת חומר נלווה כאן: {section.reason}"
        d.confidence = 0.75
        return d

    # 5. משפט קצר מדי
    if (s.end - s.start) < MIN_SENTENCE_SECONDS:
        d.reason = (f"המשפט קצר מדי ({s.end - s.start:.1f} שנ') — הכנסה "
                    "כאן תיראה כמו הבהוב.")
        d.confidence = 0.7
        return d

    # 6. מוחשיות
    if score < CONCRETE_ENOUGH:
        d.reason = (
            "אין כאן תיאור שאפשר לצלם — "
            + ("ניסוח של דעה או רעיון. " if category == "abstract" else "")
            + "תמונה תהיה גנרית ולא תוסיף מידע.")
        d.confidence = 0.65
        return d

    # --- כן לחומר נלווה ---
    d.verdict = "broll"
    d.confidence = min(0.9, 0.45 + score * 0.5)
    serves_emotion = s.role in FACE_UNLESS_VISUAL
    asset, match = match_existing(s.text, assets)
    if asset is not None:
        d.source = "existing"
        d.asset_id = asset.id
        d.reason = (f"{_CATEGORY_HE.get(category, 'תיאור חזותי')} במשפט, "
                    f"ויש כבר חומר מתאים ({match * 100:.0f}% התאמה) — "
                    "עדיף על יצירה חדשה.")
        if serves_emotion:
            d.reason += (f" {_ROLE_HE.get(s.role, s.role)} עם תיאור חזותי "
                         "חזק — החומר משרת את הרגש ולא מחליף אותו.")
    else:
        d.source = "generate"
        d.query = " ".join(_content_words(s.text)[:6])
        d.prompt = build_prompt(s.text, category, aspect=aspect, style=style)
        d.reason = (f"{_CATEGORY_HE.get(category, 'תיאור חזותי')} במשפט — "
                    "חומר נלווה כאן מראה את מה שנאמר במקום להסביר אותו.")
        if serves_emotion:
            d.reason += (f" {_ROLE_HE.get(s.role, s.role)} עם תיאור חזותי "
                         "חזק — החומר משרת את הרגש ולא מחליף אותו.")
    return d


_ROLE_HE = {
    "hook": "פתיח", "emotional_peak": "שיא רגשי", "payoff": "פאנץ'",
    "cta": "קריאה לפעולה",
}
_CATEGORY_HE = {
    "place": "תיאור מקום", "object": "אובייקט מוחשי",
    "action": "פעולה פיזית", "nature": "תיאור סביבה",
}


# --------------------------------------------------------------------------
# פרומפט
# --------------------------------------------------------------------------
_SCENE = {
    "place": "a wide establishing shot of the place being described",
    "object": "a close-up of the object on a simple surface",
    "action": "the action being described, captured mid-motion",
    "nature": "the natural scene and weather being described",
}
_LOOK = {
    "place": "cinematic establishing shot, natural light",
    "object": "close-up product shot, shallow depth of field",
    "action": "dynamic cinematic shot, subtle motion blur",
    "nature": "atmospheric cinematic photography, natural light",
}


def build_prompt(text: str, category: str, *, aspect: str = "9:16",
                 style: str = "") -> str:
    """
    בונה פרומפט באנגלית מתוך משפט בעברית.

    המשתמש לא אמור לכתוב פרומפט מקצועי. הניסוח כאן מתאר **סוג
    צילום** ולא מתרגם את המשפט מילה במילה — תרגום מילולי של דיבור
    חופשי מפיק תמונות מוזרות.

    אין כאן שמות של אנשים אמיתיים, מותגים או יצירות מוגנות: המנוע
    מתאר סצנה גנרית שמתאימה למה שנאמר.
    """
    scene = _SCENE.get(category, "a simple symbolic scene")
    look = _LOOK.get(category, "clean cinematic photography")
    orientation = ("vertical composition" if aspect == "9:16"
                   else "horizontal composition")
    parts = [scene, look, orientation, "photorealistic", "no text",
             "no watermark", "no recognisable faces"]
    if style:
        parts.insert(2, style)
    return ", ".join(parts)
