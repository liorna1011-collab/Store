"""
Suggest Visuals – מציאת נקודות בתמלול שבהן תמונה תחזק את הסרטון.

שני מנועים:
  * היוריסטי – זמין תמיד, ללא מודל שפה ובלי עלות. מחפש משפטים
    שמתארים מקום, זמן, זיכרון או תפנית רגשית – בדיוק המקומות שבהם
    עורך היה שם ויזואל.
  * מודל שפה – כשהוגדר, לתיאורים מדויקים יותר. נופל חזרה להיוריסטי
    אם המודל לא זמין או החזיר תשובה פסולה.

המנוע **מציע בלבד**. שום תמונה אינה נוצרת או משובצת בלי פעולה מפורשת
של המשתמש; הפונקציות כאן אינן כותבות דבר למסד הנתונים.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from .. import i18n
from ..config import AppSettings
from ..models import Clip, SubtitleCue
from . import llm as llm_svc

log = logging.getLogger("polixor.visual_suggest")

# מרווח מינימלי בין שתי הצעות, כדי שלא נציע רצף תמונות צפוף מדי
MIN_GAP_SECONDS = 6.0
DEFAULT_DURATION = 3.0

# --------------------------------------------------------------------------
# מילות מפתח: עברית ואנגלית.
# לכל קבוצה משקל ותבנית פרומפט שמתארת סוג ויזואל מתאים.
# --------------------------------------------------------------------------
CUES: list[dict[str, Any]] = [
    {
        "key": "place",
        "weight": 1.0,
        "words": ["הגעתי ל", "הייתי ב", "נסעתי ל", "בדרך ל", "במקום",
                  "בבית", "בחוץ", "ביער", "בים", "בהר", "בעיר", "במדבר",
                  "בכביש", "בחדר", "במשרד", "בחנות",
                  "i went to", "i was at", "we drove", "on the way",
                  "in the forest", "at the beach", "in the city"],
        "scene": "a wide establishing shot of the place being described",
        "style": "cinematic establishing shot",
    },
    {
        "key": "low",
        "weight": 1.35,
        "words": ["הכי נמוך", "נשברתי", "בכיתי", "לבד", "אבוד", "חושך",
                  "קשה לי", "ייאוש", "פחדתי", "כאב",
                  "rock bottom", "i broke", "alone", "lost", "afraid",
                  "the darkest"],
        "scene": "a lonely empty road at night, a single figure far away",
        "style": "dark moody cinematic, lonely, muted colors",
    },
    {
        "key": "high",
        "weight": 1.2,
        "words": ["ניצחתי", "הצלחתי", "עשינו את זה", "אלוף", "שיא",
                  "בום", "מטורף", "לא יאומן",
                  "we won", "i made it", "insane", "unbelievable",
                  "record", "clutch"],
        "scene": "a triumphant moment, arms raised, confetti and stage light",
        "style": "dramatic high energy cinematic, bold lighting",
    },
    {
        "key": "time",
        "weight": 0.9,
        "words": ["לפני שנה", "כשהייתי ילד", "פעם", "בעבר", "אחרי ש",
                  "יום אחד", "בהתחלה", "בסוף",
                  "a year ago", "when i was", "back then", "one day"],
        "scene": "a nostalgic memory scene, warm light through a window",
        "style": "nostalgic cinematic, soft light, film grain",
    },
    {
        "key": "object",
        "weight": 0.8,
        "words": ["המחשב", "המכונית", "הטלפון", "הכסף", "הדלת", "המפתח",
                  "הספר", "המכתב", "השלט",
                  "the car", "the phone", "the money", "the door",
                  "the letter", "the key"],
        "scene": "a close-up of a single meaningful object on a desk",
        "style": "close-up product shot, shallow depth of field",
    },
    {
        "key": "concept",
        "weight": 0.75,
        "words": ["החלום", "התוכנית", "העתיד", "המטרה", "הסיכוי",
                  "האמת", "הסוד",
                  "the dream", "the plan", "the future", "the goal",
                  "the truth", "the secret"],
        "scene": "a symbolic conceptual image of an idea taking shape",
        "style": "conceptual cinematic illustration, symbolic",
    },
]

# מילים שאינן תורמות לתיאור ויזואלי – מכל חבילות השפה
from . import lang as _lang  # noqa: E402

STOPWORDS = set().union(*(p.stop_words for p in _lang.packs_for(None)))


def _clean_words(text: str) -> list[str]:
    tokens = re.findall(r"[\w֐-׿']+", text or "")
    return [t for t in tokens if t.lower() not in STOPWORDS and len(t) > 1]


def _match_cues(text: str) -> tuple[float, Optional[dict[str, Any]]]:
    """
    מוצא את הרמז החזק ביותר בטקסט, **בגבולות מילה**.

    התאמת תת-מחרוזת שברה את הלקסיקון בעברית: „לבד" נמצא בתוך
    „לבדוק", ולכן המשפט „אני רוצה לבדוק את זה" סווג כרגע רגשי
    נמוך וקיבל הצעה לתמונה חשוכה ובודדה. הביטוי „הכי" בתוך
    „הכי נמוך" הפיק את אותה תקלה במקום אחר.
    """
    from .semantics import _phrase_pattern

    low = (text or "").lower()
    best: Optional[dict[str, Any]] = None
    score = 0.0
    for cue in CUES:
        for word in cue["words"]:
            if _phrase_pattern(word).search(low):
                if cue["weight"] > score:
                    score, best = cue["weight"], cue
                break
    return score, best


HEBREW_RE = re.compile(r"[֐-׿]")


def _is_hebrew(text: str) -> bool:
    letters = re.findall(r"[^\W\d_]", text or "", re.UNICODE)
    if not letters:
        return False
    hebrew = sum(1 for c in letters if HEBREW_RE.match(c))
    return hebrew > len(letters) * 0.3


def _prompt_for(text: str, cue: Optional[dict[str, Any]], aspect: str) -> str:
    """
    בונה פרומפט ליצירת תמונה.

    מודלי תמונה מבינים אנגלית הרבה יותר טוב מעברית, ולכן על משפט
    עברי לא מעבירים את הטקסט כמו שהוא: משתמשים בתיאור הסצנה
    האנגלי של הקטגוריה שזוהתה, ומוסיפים רק מילים לטיניות שנאמרו
    בפועל (שמות, מותגים, מונחים). המשתמש תמיד יכול לערוך את
    הפרומפט לפני היצירה.
    """
    style = (cue or {}).get("style", "cinematic, realistic")
    orient = {"9:16": "vertical 9:16 composition",
              "16:9": "wide 16:9 composition",
              "1:1": "square composition"}.get(aspect, "")

    if _is_hebrew(text):
        subject = (cue or {}).get("scene", "a cinematic atmospheric scene")
        latin = [w for w in _clean_words(text)
                 if not HEBREW_RE.search(w) and len(w) > 2][:4]
        if latin:
            subject += ", featuring " + " ".join(latin)
    else:
        words = _clean_words(text)[:10]
        subject = " ".join(words) if words else "abstract cinematic scene"

    parts = [subject, style, orient, "no text, no words, no logos"]
    return ", ".join(p for p in parts if p)


def _aspect_for_clip(clip: Clip) -> str:
    return "9:16" if (clip.aspect or "") == "9:16" else "16:9"


# --------------------------------------------------------------------------
# מנוע היוריסטי
# --------------------------------------------------------------------------
def heuristic_suggestions(cues: list[SubtitleCue], *, aspect: str,
                          clip_duration: float,
                          limit: int = 5) -> list[dict[str, Any]]:
    scored: list[tuple[float, dict[str, Any]]] = []
    for cue in cues:
        text = (cue.text or "").strip()
        if len(text) < 8:
            continue
        weight, matched = _match_cues(text)
        if weight <= 0:
            continue
        # משפט ארוך יותר נושא יותר תוכן ויזואלי
        length_bonus = min(0.25, len(text) / 400.0)
        score = weight + length_bonus
        start = max(0.0, float(cue.start))
        # התמונה נכנסת בסוף המשפט – אחרי שהצופה שמע מה מתואר
        at = min(max(0.0, clip_duration - 0.5), float(cue.end))
        scored.append((score, {
            "start": round(start, 2),
            "end": round(float(cue.end), 2),
            "text": text,
            "prompt": _prompt_for(text, matched, aspect),
            "aspect": aspect,
            "reason": i18n.tr(f"suggest.cue.{matched['key']}" if matched
                              else "suggest.cue.default"),
            "role": "insert",
            "duration": DEFAULT_DURATION,
            "score": round(min(1.0, score / 1.6), 3),
            "source": "heuristic",
            "_at": at,
        }))

    scored.sort(key=lambda p: -p[0])
    picked: list[dict[str, Any]] = []
    for _, item in scored:
        if any(abs(item["_at"] - p["_at"]) < MIN_GAP_SECONDS for p in picked):
            continue
        picked.append(item)
        if len(picked) >= limit:
            break

    picked.sort(key=lambda p: p["_at"])
    for p in picked:
        p.pop("_at", None)
    return picked


# --------------------------------------------------------------------------
# מנוע מודל שפה
# --------------------------------------------------------------------------
LLM_SYSTEM = """אתה עורך וידאו שמחליט היכן תמונה תחזק סרטון.
אתה מקבל שורות תמלול עם זמנים, ומחזיר רק מקומות שבהם ויזואל באמת עוזר.
אל תציע תמונה לכל משפט. עדיף שלוש הצעות טובות מעשר בינוניות."""

LLM_INSTRUCTIONS = """החזר JSON יחיד בלבד, בצורה:
{"suggestions": [{"index": <מספר השורה>, "prompt": "<תיאור באנגלית ליצירת תמונה>", "reason": "<למה כאן, %s>", "duration": <שניות 2-5>}]}

כללים:
- prompt באנגלית, תיאורי וקונקרטי, בלי טקסט או לוגו בתמונה.
- הפרומפט חייב לתאר את מה שנאמר בשורה, לא משהו שלא נאמר.
- עד %d הצעות.
- אם אף שורה אינה מתאימה, החזר רשימה ריקה."""


def llm_suggestions(cues: list[SubtitleCue], *, aspect: str,
                    clip_duration: float, settings: AppSettings,
                    limit: int = 5) -> list[dict[str, Any]]:
    lines = []
    usable = [c for c in cues if len((c.text or "").strip()) >= 8][:120]
    for i, cue in enumerate(usable):
        lines.append(f"[{i}] {cue.start:.1f}-{cue.end:.1f}: {cue.text.strip()}")
    if not lines:
        return []

    user = ("שורות התמלול של הקליפ:\n" + "\n".join(lines) + "\n\n"
            + (LLM_INSTRUCTIONS % (i18n.tr("suggest.llm_reason_language"), limit)))
    raw = llm_svc.call_model(LLM_SYSTEM, user, settings)
    data = llm_svc._parse_json(raw)
    items = data.get("suggestions") or []
    if not isinstance(items, list):
        return []

    out: list[dict[str, Any]] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index", -1))
        except (TypeError, ValueError):
            continue
        if not 0 <= idx < len(usable):
            continue
        prompt = str(item.get("prompt") or "").strip()
        if len(prompt) < 4:
            continue
        cue = usable[idx]
        try:
            dur = float(item.get("duration") or DEFAULT_DURATION)
        except (TypeError, ValueError):
            dur = DEFAULT_DURATION
        out.append({
            "start": round(max(0.0, float(cue.start)), 2),
            "end": round(float(cue.end), 2),
            "text": (cue.text or "").strip(),
            "prompt": prompt,
            "aspect": aspect,
            "reason": str(item.get("reason") or "").strip() or i18n.tr("suggest.llm_reason"),
            "role": "insert",
            "duration": min(5.0, max(2.0, dur)),
            "score": 0.0,
            "source": "llm",
        })
    out.sort(key=lambda p: p["start"])
    return out


# --------------------------------------------------------------------------
# נקודת כניסה
# --------------------------------------------------------------------------
def suggest_for_clip(session, clip: Clip, *, limit: int = 5,
                     settings: AppSettings) -> dict[str, Any]:
    """
    מחזיר הצעות לקליפ. אינו יוצר תמונות ואינו כותב ל-DB.

    זמני ההצעות הם על ציר הפלט הערוך, כי הכתוביות כבר מופו אליו –
    כך שההצעה מצביעה על אותה נקודה שהמשתמש רואה בנגן.
    """
    cues = (session.query(SubtitleCue)
            .filter(SubtitleCue.clip_id == clip.id)
            .order_by(SubtitleCue.start).all())
    aspect = _aspect_for_clip(clip)
    duration = float(clip.duration or 0.0)

    if not cues:
        return {"clip_id": clip.id, "job_id": clip.job_id, "suggestions": [],
                "source": "none",
                "note": i18n.tr("suggest.note.no_transcript")}

    note = ""
    source = "heuristic"
    items: list[dict[str, Any]] = []

    if llm_svc.is_llm_enabled(settings):
        try:
            items = llm_suggestions(cues, aspect=aspect, clip_duration=duration,
                                    settings=settings, limit=limit)
            source = "llm"
        except Exception as exc:
            log.warning("LLM suggest failed, falling back: %s", exc)
            note = i18n.tr("suggest.note.llm_unavailable")
            items = []

    if not items:
        items = heuristic_suggestions(cues, aspect=aspect,
                                      clip_duration=duration, limit=limit)
        source = "heuristic" if source != "llm" or not note else source
        if source == "llm" and not note:
            note = i18n.tr("suggest.note.llm_empty")
            source = "heuristic"

    if not items and not note:
        note = i18n.tr("suggest.note.none")

    return {"clip_id": clip.id, "job_id": clip.job_id,
            "suggestions": items, "source": source, "note": note}
