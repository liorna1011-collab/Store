"""תזמור: יחידות → הצעות → ציון → כפילויות → בחירה + דוח."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np

from ... import i18n
from ...config import AppSettings
from ..scoring import Timeline
from ..transcribe import TranscriptResult
from . import review
from .dedupe import dedupe
from .score import Scored, best_per_payoff, pick_threshold, rank_key, score_proposal
from .story import propose
from .units import Unit, build_units

# כמה "כמעט" לשמור בדוח
MAX_NEAR_MISSES = 12
# "כמעט" = ציון בטווח הזה מתחת לרף (או עבר את הרף אבל נדחה מסיבה אחרת)
NEAR_MISS_MARGIN = 0.15


@dataclass
class IntelResult:
    selected: list[Any]                        # selection.Candidate
    review: dict[str, Any]
    notes: list[str] = field(default_factory=list)
    units: list[Unit] = field(default_factory=list)


def select_short_clips(tl: Timeline, transcript: Optional[TranscriptResult], *,
                       settings: AppSettings, language: Optional[str], limit: int,
                       extra_seeds: Sequence[dict[str, Any]] = (),
                       time_offset: float = 0.0,
                       units: Optional[list[Unit]] = None) -> Optional[IntelResult]:
    """
    בוחר קליפים קצרים לפי מבנה סיפור. מחזיר None כשאין תמלול עם דיבור
    (אז אין על מה לבנות סיפור, והקורא חוזר לבחירה לפי אותות).
    """
    from .. import selection

    if limit <= 0:
        return None
    units = units if units is not None else build_units(transcript, tl, language)
    if not units:
        return None

    min_d = float(settings.short_min_seconds)
    max_d = float(settings.short_max_seconds)
    threshold = pick_threshold(getattr(settings, "clip_min_quality", 0.5))

    peaks = selection.find_peaks(tl, min_distance_seconds=max(4.0, min_d * 0.6),
                                 sensitivity=settings.sensitivity, max_peaks=200) if tl.n else []
    peak_times = [i * tl.hop for i in peaks]
    chat_times = _chat_peaks(tl)

    proposals = propose(units, tl, min_d=min_d, max_d=max_d, extra_seeds=extra_seeds,
                        peak_times=peak_times, chat_times=chat_times)
    scored = [score_proposal(p, units, tl, min_d=min_d, threshold=threshold) for p in proposals]
    stories = best_per_payoff(scored)
    passed = [s for s in stories if s.passed]
    kept, removals = dedupe(passed, units)
    chosen, over = kept[:limit], kept[limit:]

    # ---- "כמעט" ----
    near_pool = [s for s in stories if not s.passed and s.final >= threshold - NEAR_MISS_MARGIN]
    near_pool.sort(key=rank_key, reverse=True)
    near: list[Scored] = []
    for s in near_pool:
        if any(min(s.end, k.end) - max(s.start, k.start) > 1.0 for k in chosen + near):
            continue
        near.append(s)
        if len(near) >= MAX_NEAR_MISSES:
            break

    # ---- מועמדים לפייפליין ----
    out = []
    for sc in sorted(chosen, key=lambda s: s.start):
        p = sc.proposal
        pay = units[p.payoff_idx]
        cand = selection._make_candidate(tl, transcript, p.start, p.end,
                                         (pay.start + pay.end) / 2.0, kind="short",
                                         language=language)
        cand.score = round(sc.final, 4)
        cand.reason = review.short_reason(sc)
        cand.quality = {"engine": "clip_intel", "review_id": review.record_id(sc),
                        "final": sc.final, "components": dict(sc.components),
                        "penalties": dict(sc.penalties),
                        "hook": units[p.hook_idx].text, "payoff": pay.text}
        out.append(cand)

    # ---- דוח ----
    shift = float(time_offset)
    # חלופות לאותו רגע (פאנץ' אחר / סיום אחר שחופף בזמן) אינן "כפילויות":
    # הן נשמרות על הרשומה של הקליפ שניצח, לצורך כיוונון
    alternatives: dict[str, list[dict[str, Any]]] = {}
    for r in removals:
        if r.kind == "time_overlap":
            alternatives.setdefault(review.record_id(r.kept), []).append(
                {"start": r.removed.start, "end": r.removed.end, "final_score": r.removed.final,
                 "payoff": units[r.removed.proposal.payoff_idx].text})
    same_content = [r for r in removals if r.kind == "same_content"]

    selected_records = []
    for s_ in chosen:
        rec = review.record(s_, units, status="selected")
        rec["overlapping_alternatives"] = sorted(
            alternatives.get(rec["id"], []), key=lambda a: -a["final_score"])[:5]
        selected_records.append(rec)
    near_records = [review.record(s, units, status="near_miss",
                                  rejection=review.rejection_for(s, threshold)) for s in near]
    near_records += [review.record(s, units, status="near_miss",
                                   rejection=review._t("reject.over_limit")) for s in over]
    dup_records = [review.record(r.removed, units, status="duplicate",
                                 rejection=review._t(f"reject.{r.kind}",
                                                     similarity=f"{r.similarity:.2f}"),
                                 duplicate_of=review.record_id(r.kept)) for r in same_content]
    rep = {
        "engine": "clip_intel", "version": review.REVIEW_VERSION,
        "language": language, "threshold": threshold, "time_offset": shift,
        "settings": {"min_seconds": min_d, "max_seconds": max_d, "max_clips": limit,
                     "sensitivity": settings.sensitivity},
        "stats": {"sentences": len(units), "proposals": len(proposals),
                  "stories": len(stories), "passed": len(passed),
                  "selected": len(chosen), "duplicates": len(same_content),
                  "overlapping_alternatives": len(removals) - len(same_content),
                  "near_misses": len(near_records)},
        "selected": selected_records, "near_misses": near_records, "duplicates": dup_records,
    }
    if shift:
        _shift_times(rep, shift)

    notes = [i18n.tr("clip_intel.note.summary", selected=len(chosen), stories=len(stories),
                     duplicates=len(same_content))]
    if not chosen:
        notes.append(i18n.tr("clip_intel.note.none_passed", threshold=f"{threshold:.2f}",
                             near=len(near_records)))
    return IntelResult(selected=out, review=rep, notes=notes, units=units)


def _chat_peaks(tl: Timeline) -> list[float]:
    if not tl.n or float(tl.chat.max()) < 0.3:
        return []
    c = tl.chat
    left = np.concatenate([[c[0]], c[:-1]])
    right = np.concatenate([c[1:], [c[-1]]])
    idx = np.flatnonzero((c >= left) & (c >= right) & (c >= 0.3))
    return [float(i * tl.hop) for i in idx]


def _shift_times(rep: dict[str, Any], shift: float) -> None:
    """זמנים בדוח יחסית לשידור המלא (כשמנתחים מקטע של שידור חי)."""
    for group in ("selected", "near_misses", "duplicates"):
        for r in rep[group]:
            for obj in (r, r["hook"], r["payoff"], r["boundaries"]):
                for k in ("start", "end"):
                    if k in obj:
                        obj[k] = round(obj[k] + shift, 3)
