"""
רשומות הסבר – לכל קליפ שנבחר, לכל "כמעט" שנדחה ולכל כפילות שהוסרה.

המטרה: לכוונן את המנוע לפי תוצאות אמיתיות ולא לפי ניחוש. כל רשומה
אומרת למה הקטע הוצע, מה הוו, מה ההקשר הנדרש, מה הפאנץ', אילו רכיבים
תרמו לציון ואילו קנסות הורידו, מה הציון הסופי ולמה הגבולות נבחרו
היכן שנבחרו. כל סיבה נשמרת גם כמפתח (לניתוח) וגם כטקסט (לקריאה).
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from ... import i18n
from ...util.text import truncate
from .score import Scored
from .units import Unit

REVIEW_VERSION = 1


def _t(key: str, **params: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"key": key, "text": i18n.tr(f"clip_intel.{key}", **params)}
    if params:
        out["params"] = {k: str(v) for k, v in params.items()}
    return out


def localize(review: dict[str, Any]) -> dict[str, Any]:
    """
    מתרגם מחדש את כל הטקסטים בדוח לשפה הנוכחית (לפי המפתחות השמורים).
    כך צופה באנגלית רואה הסבר באנגלית גם אם הפרויקט נוצר בעברית.
    """
    def re_t(obj: Any) -> None:
        if isinstance(obj, dict) and "key" in obj and "text" in obj:
            obj["text"] = i18n.tr(f"clip_intel.{obj['key']}", **(obj.get("params") or {}))

    for group in ("selected", "near_misses", "duplicates"):
        for r in review.get(group) or []:
            for x in r.get("proposed_by") or []:
                re_t(x)
            for part in ("hook", "payoff"):
                for field_name in ("reasons", "problems"):
                    for x in (r.get(part) or {}).get(field_name) or []:
                        re_t(x)
            b = r.get("boundaries") or {}
            re_t(b.get("start_reason"))
            re_t(b.get("end_reason"))
            re_t(r.get("rejection"))
            for c in r.get("components") or []:
                c["label"] = i18n.tr(f"clip_intel.component.{c['key']}")
            for c in r.get("penalties") or []:
                c["label"] = i18n.tr(f"clip_intel.penalty.{c['key']}")
    return review


def record(sc: Scored, units: Sequence[Unit], *, status: str,
           rejection: Optional[dict[str, Any]] = None,
           duplicate_of: Optional[str] = None) -> dict[str, Any]:
    p = sc.proposal
    hook_u, pay_u = units[p.hook_idx], units[p.payoff_idx]
    middle = units[p.hook_idx + 1: p.payoff_idx]
    tail = units[p.payoff_idx + 1: p.end_idx + 1]
    return {
        "id": record_id(sc),
        "status": status,                          # selected | near_miss | duplicate
        "start": p.start, "end": p.end, "duration": round(p.duration, 2),
        "final_score": sc.final,
        "proposed_by": [_t(f"source.{s}") for s in p.sources],
        "hook": {
            "text": hook_u.text, "start": round(hook_u.start, 3), "end": round(hook_u.end, 3),
            "score": sc.components.get("hook", 0.0),
            "reasons": [_t(f"why.{r}") for r in sc.hook_reasons],
            "problems": [_t(f"problem.{r}") for r in sc.hook_problems],
            "detail": dict(hook_u.hook_detail),
        },
        "context": {
            "text": truncate(" ".join(u.text for u in middle), 600),
            "sentences": len(middle), "seconds": sc.context_seconds,
        },
        "payoff": {
            "text": pay_u.text, "start": round(pay_u.start, 3), "end": round(pay_u.end, 3),
            "score": sc.components.get("payoff", 0.0),
            "reasons": [_t(f"why.{r}") for r in sc.payoff_reasons],
            "tail": " ".join(u.text for u in tail),
        },
        "components": [{"key": k, "label": i18n.tr(f"clip_intel.component.{k}"), "value": v}
                       for k, v in sc.components.items()],
        "penalties": [{"key": k, "label": i18n.tr(f"clip_intel.penalty.{k}"), "value": v}
                      for k, v in sc.penalties.items()],
        "gates": dict(sc.gates),
        "boundaries": {
            "start": p.start, "end": p.end,
            "start_reason": _t(f"boundary.start.{p.start_reason}"),
            "end_reason": _t(f"boundary.end.{p.end_reason}"),
        },
        "low_confidence_words": sc.low_confidence,
        "visual": dict(sc.visual) or None,
        "rejection": rejection,
        "duplicate_of": duplicate_of,
    }


def record_id(sc: Scored) -> str:
    return f"{sc.proposal.start:.2f}-{sc.proposal.end:.2f}"


def rejection_for(sc: Scored, threshold: float) -> dict[str, Any]:
    key = sc.rejection or "below_quality_bar"
    params: dict[str, Any] = {}
    if key == "below_quality_bar":
        params = {"score": f"{sc.final:.2f}", "threshold": f"{threshold:.2f}"}
    return _t(f"reject.{key}", **params)


def short_reason(sc: Scored) -> str:
    """הנימוק הקצר שמוצג על הקליפ."""
    none = i18n.tr("clip_intel.reason_none")
    hook = i18n.tr(f"clip_intel.why.{sc.hook_reasons[0]}") if sc.hook_reasons else none
    payoff = next((i18n.tr(f"clip_intel.why.{r}") for r in sc.payoff_reasons
                   if r != "payoff_early"), none)
    return i18n.tr("clip_intel.reason", hook=hook, payoff=payoff, score=f"{sc.final:.2f}")
