"""
ציון מוסבר להצעת קליפ.

רכיבים (0..1 כל אחד). הליבה היא וו + פאנץ'; שלמות, קשת וצפיפות הם מכפיל
על הליבה, כך שדיבור נקי בלי סיפור לא מקבל ציון גבוה:
  hook          כמה חזקה הפתיחה (משפט הפתיחה עצמו, לא משפט שהוזז לשם)
  payoff        כמה חזק הפאנץ'/הפתרון/התגובה, ומיקומו בסוף הקליפ
  completeness  האם הקליפ עומד בפני עצמו: מתחיל בתחילת רעיון, נגמר בסוף
  arc           האם העניין בונה אל הפאנץ' (ולא דועך אחרי שיא מוקדם)
  density       דיבור רציף ובקצב סביר, מעט מילות מילוי
  signal        הציון המשולב הקיים (עוצמה, חזותי, צ'אט)

קנסות: אוויר מת, מילות מילוי, התחלה באמצע מחשבה, תלות בהקשר קודם, סיום
לא פתור, תוכן לא רלוונטי (חסות/AFK/תקלות), הקשר ארוך מדי לתוכן, תמלול
לא בטוח, קליפ קצר מהמבוקש.

שערי חובה: בלי וו מינימלי או בלי פאנץ' מינימלי – אין קליפ, גם אם הציון
הכולל גבוה. מעבר לזה, סף איכות מוחלט (`clip_min_quality`) – לא יחסי
לשאר הסרטון, ולכן סרטון חלש מחזיר מעט קליפים או אף אחד.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np

from ..scoring import Timeline
from .story import Proposal, opening_quality, payoff_potential
from .units import Unit

CORE_WEIGHTS = {"hook": 0.45, "payoff": 0.55}
# הציון המשולב הקיים מנורמל יחסית לסרטון עצמו, ולכן משקלו קטן
SIGNAL_WEIGHT = 0.05
MIN_HOOK = 0.22
MIN_PAYOFF = 0.30
# רגע חזק שמתחיל בתוך השניות האלה מתחילת הקליפ נחשב גם הוא וו
EARLY_MOMENT_SECONDS = 5.0


@dataclass
class Scored:
    proposal: Proposal
    final: float = 0.0
    components: dict[str, float] = field(default_factory=dict)
    penalties: dict[str, float] = field(default_factory=dict)
    gates: dict[str, bool] = field(default_factory=dict)
    hook_reasons: list[str] = field(default_factory=list)
    hook_problems: list[str] = field(default_factory=list)
    payoff_reasons: list[str] = field(default_factory=list)
    context_seconds: float = 0.0
    low_confidence: float = 0.0
    passed: bool = False
    rejection: str = ""              # מפתח סיבת הדחייה
    threshold: float = 0.5
    visual: dict[str, float] = field(default_factory=dict)   # visual_check.measure
    base_final: Optional[float] = None       # הציון לפני הבדיקה החזותית
    base_passed: bool = False

    @property
    def start(self) -> float:
        return self.proposal.start

    @property
    def end(self) -> float:
        return self.proposal.end


def score_proposal(p: Proposal, units: Sequence[Unit], tl: Timeline, *,
                   min_d: float, threshold: float) -> Scored:
    sc = Scored(proposal=p, threshold=threshold)
    inside = list(units[p.hook_idx: p.end_idx + 1])
    hook_u, pay_u = units[p.hook_idx], units[p.payoff_idx]
    prev = units[p.hook_idx - 1] if p.hook_idx else None
    nxt = units[p.payoff_idx + 1] if p.payoff_idx + 1 < len(units) else None
    dur = max(0.1, p.duration)

    # ---- וו ----
    hook_q, sc.hook_reasons, sc.hook_problems = opening_quality(hook_u, prev)
    # משפט פתיחה קצרצר („רגע") – מצרפים את הבא אחריו לשניות הראשונות
    if hook_u.duration < 1.2 and p.hook_idx + 1 <= p.payoff_idx:
        nxt_q, g2, _ = opening_quality(units[p.hook_idx + 1], hook_u)
        if nxt_q > hook_q:
            hook_q = 0.5 * hook_q + 0.5 * nxt_q
            sc.hook_reasons += [g for g in g2 if g not in sc.hook_reasons]
    # רגע חזק כבר בשניות הראשונות מחזיק את הצופה גם בלי משפט פתיחה
    # „קלאסי" (הכנה קצרה ומיד תפנית). המשפט נשאר במקומו – זה רק מדידה.
    early = [(k, u) for k, u in enumerate(inside[1:], start=p.hook_idx + 1)
             if u.start - p.start <= EARLY_MOMENT_SECONDS]
    for k, u in early:
        pv, _ = payoff_potential(u, units[k + 1] if k + 1 < len(units) else None)
        if 0.75 * pv > hook_q:
            hook_q = 0.75 * pv
            if "early_moment" not in sc.hook_reasons:
                sc.hook_reasons.append("early_moment")
    sc.components["hook"] = round(hook_q, 3)

    # ---- פאנץ' ----
    pay_q, sc.payoff_reasons = payoff_potential(pay_u, nxt)
    pos = (pay_u.end - p.start) / dur
    if pos < 0.45:
        # הפאנץ' בהתחלה והקליפ ממשיך אחריו – לא זה המבנה
        pay_q *= 0.6
        sc.payoff_reasons.append("payoff_early")
    if p.end_reason in ("after_reaction", "closing_line"):
        pay_q = min(1.0, pay_q + 0.06)
    sc.components["payoff"] = round(pay_q, 3)

    # ---- שלמות ----
    comp = 1.0
    if hook_u.has("continuation"):
        comp -= 0.35
    if hook_u.has("backrefs") or (len(inside) > 1 and inside[1].has("backrefs")):
        comp -= 0.30
    last = units[p.end_idx]
    if not last.ends_sentence:
        comp -= 0.35
    if pay_u.is_question and not pay_u.has("payoff_markers"):
        comp -= 0.20
    # הקליפ מתחיל בפאנץ' עצמו – אין הכנה, והצופה לא יודע על מה מדובר
    no_setup = p.hook_idx == p.payoff_idx and hook_u.duration < 6.0
    if no_setup:
        comp -= 0.30
    sc.components["completeness"] = round(float(np.clip(comp, 0.0, 1.0)), 3)

    # ---- קשת ----
    sc.components["arc"] = round(_arc(inside, p, tl), 3)

    # ---- צפיפות ----
    speech = sum(u.duration for u in inside)
    speech_ratio = min(1.0, speech / dur)
    words = sum(len(u.words) or len(u.text.split()) for u in inside)
    wps = words / max(0.5, speech)
    rate_q = 1.0 if 1.4 <= wps <= 4.8 else 0.6
    filler = float(np.mean([u.filler_ratio for u in inside])) if inside else 0.0
    sc.components["density"] = round(float(np.clip(speech_ratio * rate_q * (1 - filler), 0, 1)), 3)

    # ---- האות המשולב הקיים ----
    sc.components["signal"] = round(float(tl.window_mean(tl.score, p.start, p.end)) if tl.n else 0.0, 3)

    # ---- קנסות ----
    pen: dict[str, float] = {}
    gaps = sum(max(0.0, b.start - a.end) for a, b in zip(inside, inside[1:]) if b.start - a.end > 1.0)
    dead = gaps / dur
    if dead > 0.08:
        pen["dead_air"] = min(0.30, 1.2 * dead)
    if filler > 0.12:
        pen["filler"] = min(0.20, (filler - 0.12) * 1.5)
    if hook_u.has("continuation"):
        pen["starts_mid_thought"] = 0.16
    if hook_u.has("backrefs"):
        pen["needs_earlier_context"] = 0.18
    elif any(u.has("backrefs") for u in inside[1:3]):
        pen["needs_earlier_context"] = 0.08
    if not last.ends_sentence:
        pen["ends_mid_sentence"] = 0.15
    if no_setup:
        pen["no_setup"] = 0.12
    off = sum(1 for u in inside if u.has("offtopic") or u.has("afk") or u.has("cta"))
    if off:
        pen["off_topic"] = min(0.40, 0.15 + 0.5 * off / len(inside))
    mid = inside[1:-1] if len(inside) > 2 else []
    sc.context_seconds = round(sum(u.duration for u in mid), 2)
    if mid and sc.context_seconds > 0.65 * dur:
        mid_interest = float(np.mean([max(u.lexical, u.energy) for u in mid]))
        if mid_interest < 0.3:
            pen["slow_middle"] = 0.12
    words_all = [w for u in inside for w in u.words]
    if words_all:
        sc.low_confidence = round(sum(1 for w in words_all if float(w.probability) < 0.45)
                                  / len(words_all), 3)
        if sc.low_confidence > 0.30:
            pen["uncertain_transcript"] = 0.08
    if dur < min_d:
        pen["shorter_than_requested"] = round(min(0.10, 0.10 * (min_d - dur) / max(1.0, min_d)), 3)
    sc.penalties = {k: round(v, 3) for k, v in pen.items()}

    # הסיפור עצמו (וו + פאנץ') הוא הליבה. שלמות, קשת וצפיפות לא מוסיפים
    # "נקודות חינם" לכל דיבור נקי – הם מכפיל על הליבה (0.55..1).
    c = sc.components
    core = CORE_WEIGHTS["hook"] * c["hook"] + CORE_WEIGHTS["payoff"] * c["payoff"]
    craft = (c["completeness"] + c["arc"] + c["density"]) / 3.0
    raw = core * (0.55 + 0.45 * craft) + SIGNAL_WEIGHT * c["signal"]
    # שניהם חייבים להיות שם: בונוס לפי החלש מבין השניים
    raw += 0.10 * min(hook_q, pay_q)
    raw -= sum(pen.values())
    sc.final = round(float(np.clip(raw, 0.0, 1.0)), 4)

    sc.gates = {"hook": hook_q >= MIN_HOOK, "payoff": pay_q >= MIN_PAYOFF,
                "complete": last.ends_sentence or p.end_reason != "sentence_end"}
    if not sc.gates["payoff"]:
        sc.rejection = "no_payoff"
    elif not sc.gates["hook"]:
        sc.rejection = "weak_hook"
    elif "off_topic" in pen and pen["off_topic"] >= 0.25:
        sc.rejection = "off_topic"
    elif sc.final < threshold:
        sc.rejection = "below_quality_bar"
    sc.passed = not sc.rejection
    return sc


def _arc(inside: Sequence[Unit], p: Proposal, tl: Timeline) -> float:
    """עניין שבונה אל הפאנץ': השיא בשליש האחרון, ולא דעיכה אחרי שיא מוקדם."""
    if len(inside) < 2:
        return 0.5
    vals = np.array([max(u.energy, u.lexical, u.interest) for u in inside], dtype=np.float32)
    pos = int(np.argmax(vals)) / max(1, len(vals) - 1)
    late = 1.0 - min(1.0, abs(pos - 0.8) / 0.8)
    first, rest = float(vals[: max(1, len(vals) // 2)].mean()), float(vals[len(vals) // 2:].mean())
    build = float(np.clip(0.5 + (rest - first), 0.0, 1.0))
    return float(np.clip(0.6 * late + 0.4 * build, 0.0, 1.0))


def rank_key(s: Scored) -> tuple[bool, float, float]:
    return (s.passed, s.final, s.components.get("payoff", 0.0))


def summarize(sc: Scored) -> dict[str, Any]:
    return {"final": sc.final, "components": dict(sc.components),
            "penalties": dict(sc.penalties), "gates": dict(sc.gates)}


def best_per_payoff(items: Sequence[Scored]) -> list[Scored]:
    """מכל ההצעות לאותו פאנץ' (פתיחות שונות) – הטובה ביותר."""
    best: dict[int, Scored] = {}
    for s in items:
        k = s.proposal.payoff_idx
        if k not in best or rank_key(s) > rank_key(best[k]):
            best[k] = s
    return list(best.values())


def pick_threshold(value: Optional[float]) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = 0.5
    return float(np.clip(v, 0.2, 0.9))
