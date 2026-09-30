"""
תזמור: אזורים → יחידות → הצעות → ציון → כפילויות → (חזותי) → בחירה + דוח.

שני שלבים, כדי שניתוח חזותי יקר ירוץ רק על המועמדים:
  analyze_stories()  כל מה שנשען על תמלול ואודיו – זול, רץ על כל הסרטון
  finalize()         התאמה לפי ניתוח חזותי של חלונות המועמדים (אם יש),
                     בחירה סופית ודוח
select_short_clips() מריץ את שניהם ברצף (כשהניתוח החזותי כבר קיים).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np

from ... import i18n
from ...config import AppSettings
from ..scoring import Timeline
from ..transcribe import TranscriptResult
from . import review
from .dedupe import Removal, dedupe
from .regions import Region, plan_regions
from .score import Scored, best_per_payoff, pick_threshold, rank_key, score_proposal
from .story import Proposal, propose
from .units import Unit, build_units
from .visual_check import apply_visual

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


@dataclass
class StoryAnalysis:
    """תוצאת השלב הזול – לפני ניתוח חזותי ולפני הבחירה הסופית."""
    tl: Timeline
    transcript: Optional[TranscriptResult]
    language: Optional[str]
    settings: AppSettings
    units: list[Unit]
    threshold: float
    proposals: int
    stories: list[Scored]
    ranked: list[Scored]                       # עברו את הרף, בלי כפילויות, לפי איכות
    removals: list[Removal]
    regions: list[Region]
    time_offset: float = 0.0

    def windows(self, n: int, pad: float = 2.0) -> list[tuple[float, float]]:
        """חלונות הזמן של n המועמדים המובילים (לניתוח חזותי ממוקד)."""
        return [(max(0.0, s.start - pad), min(self.tl.duration, s.end + pad))
                for s in self.ranked[:n]]


# --------------------------------------------------------------------------
def analyze_stories(tl: Timeline, transcript: Optional[TranscriptResult], *,
                    settings: AppSettings, language: Optional[str],
                    extra_seeds: Sequence[dict[str, Any]] = (),
                    time_offset: float = 0.0,
                    units: Optional[list[Unit]] = None) -> Optional[StoryAnalysis]:
    units = units if units is not None else build_units(transcript, tl, language)
    if not units:
        return None
    min_d = float(settings.short_min_seconds)
    max_d = float(settings.short_max_seconds)
    threshold = pick_threshold(getattr(settings, "clip_min_quality", 0.5))
    duration = tl.duration if tl.n else units[-1].end
    regions = plan_regions(duration, max_d)

    merged: dict[tuple[int, int, int], Proposal] = {}
    by_region: dict[int, set[tuple[int, int, int]]] = {}
    for reg in regions:
        idx = [i for i, u in enumerate(units) if u.start >= reg.start and u.end <= reg.end]
        if not idx:
            continue
        props = propose(units, tl, min_d=min_d, max_d=max_d, lo=idx[0], hi=idx[-1] + 1,
                        extra_seeds=[s for s in extra_seeds
                                     if reg.contains(float(s.get("start", 0)), float(s.get("end", 0)))],
                        peak_times=_region_peaks(tl, reg, settings, min_d),
                        chat_times=[t for t in _chat_peaks(tl) if reg.start <= t <= reg.end])
        keys = set()
        for p in props:
            key = (p.hook_idx, p.payoff_idx, p.end_idx)
            keys.add(key)
            if key in merged:
                merged[key].sources = list(dict.fromkeys(merged[key].sources + p.sources))
            else:
                merged[key] = p
        reg.proposals = len(keys)
        by_region[reg.index] = keys

    scored = [score_proposal(p, units, tl, min_d=min_d, threshold=threshold)
              for p in merged.values()]
    stories = best_per_payoff(scored)
    passed = [s for s in stories if s.passed]
    ranked, removals = dedupe(passed, units)

    for reg in regions:
        keys = by_region.get(reg.index, set())
        reg.stories = sum(1 for s in stories if _key(s) in keys)
        reg.passed = sum(1 for s in ranked if _key(s) in keys)

    return StoryAnalysis(tl=tl, transcript=transcript, language=language, settings=settings,
                         units=units, threshold=threshold, proposals=len(merged),
                         stories=stories, ranked=ranked, removals=removals,
                         regions=regions, time_offset=time_offset)


def finalize(an: StoryAnalysis, *, limit: int, visual: Any = None,
             visual_windows: Optional[Sequence[tuple[float, float]]] = None) -> IntelResult:
    """
    בחירה סופית. `visual` (VisualFeatures) משפיע על הדירוג רק בחלונות
    שנותחו בפועל (`visual_windows`; None = כל הסרטון נותח).
    """
    from .. import selection

    units, tl, threshold = an.units, an.tl, an.threshold
    ranked = list(an.ranked)
    visual_used = 0
    if visual is not None:
        for s in ranked:
            if apply_visual(s, visual, visual_windows, threshold):
                visual_used += 1
        ranked.sort(key=rank_key, reverse=True)
        # קנס חזותי יכול להוריד מועמד מתחת לרף
        dropped = [s for s in ranked if not s.passed]
        ranked = [s for s in ranked if s.passed]
    else:
        dropped = []
    chosen, over = ranked[:limit], ranked[limit:]
    chosen_keys = {_key(s) for s in chosen}
    for reg in an.regions:
        reg.selected = sum(1 for s in chosen if reg.contains(s.start, s.end))

    # ---- "כמעט" ----
    near_pool = [s for s in an.stories
                 if not s.passed and s.final >= threshold - NEAR_MISS_MARGIN and _key(s) not in chosen_keys]
    near_pool += dropped
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
        cand = selection._make_candidate(tl, an.transcript, p.start, p.end,
                                         (pay.start + pay.end) / 2.0, kind="short",
                                         language=an.language)
        cand.score = round(sc.final, 4)
        cand.reason = review.short_reason(sc)
        cand.quality = {"engine": "clip_intel", "review_id": review.record_id(sc),
                        "final": sc.final, "components": dict(sc.components),
                        "penalties": dict(sc.penalties),
                        "hook": units[p.hook_idx].text, "payoff": pay.text}
        out.append(cand)

    # ---- דוח ----
    alternatives: dict[str, list[dict[str, Any]]] = {}
    for r in an.removals:
        if r.kind == "time_overlap":
            # חלופות לאותו רגע (פאנץ'/סיום אחר שחופף) אינן "כפילויות"
            alternatives.setdefault(review.record_id(r.kept), []).append(
                {"start": r.removed.start, "end": r.removed.end, "final_score": r.removed.final,
                 "payoff": units[r.removed.proposal.payoff_idx].text})
    same_content = [r for r in an.removals if r.kind == "same_content"]

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
    s = an.settings
    rep = {
        "engine": "clip_intel", "version": review.REVIEW_VERSION,
        "language": an.language, "threshold": threshold, "time_offset": an.time_offset,
        "settings": {"min_seconds": float(s.short_min_seconds),
                     "max_seconds": float(s.short_max_seconds), "max_clips": limit,
                     "sensitivity": s.sensitivity},
        "stats": {"sentences": len(units), "proposals": an.proposals,
                  "stories": len(an.stories), "passed": len(an.ranked) + len(an.removals),
                  "selected": len(chosen), "duplicates": len(same_content),
                  "overlapping_alternatives": len(an.removals) - len(same_content),
                  "near_misses": len(near_records), "regions": len(an.regions),
                  "visual_checked": visual_used},
        "regions": [r.to_dict() for r in an.regions],
        "selected": selected_records, "near_misses": near_records, "duplicates": dup_records,
    }
    if an.time_offset:
        _shift_times(rep, an.time_offset)

    notes = [i18n.tr("clip_intel.note.summary", selected=len(chosen), stories=len(an.stories),
                     duplicates=len(same_content))]
    if not chosen:
        notes.append(i18n.tr("clip_intel.note.none_passed", threshold=f"{threshold:.2f}",
                             near=len(near_records)))
    return IntelResult(selected=out, review=rep, notes=notes, units=units)


def select_short_clips(tl: Timeline, transcript: Optional[TranscriptResult], *,
                       settings: AppSettings, language: Optional[str], limit: int,
                       extra_seeds: Sequence[dict[str, Any]] = (),
                       time_offset: float = 0.0,
                       units: Optional[list[Unit]] = None,
                       visual: Any = None) -> Optional[IntelResult]:
    """
    בוחר קליפים קצרים לפי מבנה סיפור. מחזיר None כשאין תמלול עם דיבור
    (אז אין על מה לבנות סיפור, והקורא חוזר לבחירה לפי אותות).
    """
    if limit <= 0:
        return None
    an = analyze_stories(tl, transcript, settings=settings, language=language,
                         extra_seeds=extra_seeds, time_offset=time_offset, units=units)
    if an is None:
        return None
    return finalize(an, limit=limit, visual=visual)


# --------------------------------------------------------------------------
def _key(s: Scored) -> tuple[int, int, int]:
    p = s.proposal
    return (p.hook_idx, p.payoff_idx, p.end_idx)


def _region_peaks(tl: Timeline, reg: Region, settings: AppSettings, min_d: float) -> list[float]:
    """שיאי הציון המשולב בתוך האזור, ביחס לאזור עצמו."""
    from .. import selection

    if not tl.n:
        return []
    i0, i1 = tl.idx(reg.start), min(tl.n, tl.idx(reg.end) + 1)
    if i1 - i0 < 3:
        return []
    sub = Timeline(hop=tl.hop, duration=reg.end - reg.start, score=tl.score[i0:i1])
    peaks = selection.find_peaks(sub, min_distance_seconds=max(4.0, min_d * 0.6),
                                 sensitivity=settings.sensitivity, max_peaks=40)
    return [reg.start + i * tl.hop for i in peaks]


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
    for r in rep.get("regions") or []:
        r["start"] = round(r["start"] + shift, 2)
        r["end"] = round(r["end"] + shift, 2)
