"""
Boundary optimisation: several start/end sentence combinations per Short.

The allowed starts run from a few sentences before the proposed start up to
the opening evidence (never after it); the allowed ends run from the closing
evidence (never before it – the payoff stays) to a few sentences after the
proposed end. Options outside the hard platform limits (MIN_HARD..MAX_HARD
seconds) are not offered; inside them duration is only a preference that the
model weighs against completeness. The model picks one start, one end and
whole sentences to cut inside; the code checks every ID.

Deterministic facts about each option (it starts on a turn, after a pause,
the previous sentence asks a question, the clip ends before a pause) are
shown to the model as weak features; they never decide by themselves.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from . import prompts
from .candidates import Cand
from .provider import SemanticError, SemanticProvider
from .sentences import Sentence, render
from .validate import Validator

log = logging.getLogger("polixor.semantic.boundaries")

EXTEND = 3          # sentences before/after the proposal that may be added
RECON_EXTEND = 12   # the reconstruction pass looks this far around the moment
MIN_HARD = 6.0      # shorter than this is not a clip
MAX_HARD = 175.0    # platform limit for vertical Shorts (≈3 min) with a safety margin
MAX_CUT_SHARE = 0.4


def duration(sentences: Sequence[Sentence], a: int, b: int, cuts: Sequence[int] = ()) -> float:
    cut = set(cuts)
    spans = spans_of(sentences, a, b, cut)
    return sum(y - x for x, y in spans)


def spans_of(sentences: Sequence[Sentence], a: int, b: int, cuts: Sequence[int] | set = ()) -> list[tuple[float, float]]:
    """Time spans of sentences a..b without the cut sentences (adjacent sentences join)."""
    cut = set(cuts)
    out: list[tuple[float, float]] = []
    run: Optional[list[float]] = None
    for i in range(a, b + 1):
        if i in cut:
            if run:
                out.append((run[0], run[1]))
                run = None
            continue
        s = sentences[i]
        if run is None:
            run = [s.start, s.end]
        else:
            run[1] = s.end
    if run:
        out.append((run[0], run[1]))
    return out


def _start_facts(sentences: Sequence[Sentence], i: int) -> str:
    s = sentences[i]
    prev = sentences[i - 1] if i > 0 else None
    f = []
    if prev is None or prev.turn != s.turn:
        f.append("opens a turn")
    if s.pause_before >= 0.6:
        f.append(f"after a {s.pause_before:.1f}s pause")
    if s.question:
        f.append("is a question")
    if prev is not None and prev.question and prev.turn != s.turn:
        f.append("previous sentence is a question it may answer")
    return ", ".join(f) or "mid-turn"


def _end_facts(sentences: Sequence[Sentence], i: int) -> str:
    s = sentences[i]
    nxt = sentences[i + 1] if i + 1 < len(sentences) else None
    f = []
    if nxt is None or nxt.turn != s.turn:
        f.append("closes a turn")
    if nxt is not None and nxt.pause_before >= 0.6:
        f.append(f"followed by a {nxt.pause_before:.1f}s pause")
    if s.question:
        f.append("ends on a question")
    return ", ".join(f) or "mid-turn"


def options(c: Cand, sentences: Sequence[Sentence], lo: int, hi: int) -> tuple[list[int], list[int]]:
    """Allowed start and end sentence indices for a candidate, inside [lo, hi]."""
    first_ev, last_ev = c.opening_idx, c.closing_idx
    starts = [i for i in range(max(lo, c.start_idx - EXTEND), min(first_ev, c.start_idx + 1) + 1)]
    ends = [i for i in range(max(last_ev, c.end_idx - 1), min(hi, c.end_idx + EXTEND) + 1)]
    starts = [i for i in starts if any(MIN_HARD <= duration(sentences, i, j) <= MAX_HARD for j in ends)] or \
        [min(c.start_idx, first_ev)]
    ends = [j for j in ends if any(MIN_HARD <= duration(sentences, i, j) <= MAX_HARD for i in starts)] or \
        [max(c.end_idx, last_ev)]
    return starts, ends


def optimise(provider: Optional[SemanticProvider], c: Cand, sentences: Sequence[Sentence], *,
             lo: int, hi: int, min_s: float, max_s: float) -> dict[str, Any]:
    """
    {start_idx, end_idx, cut_idx, reasons, source} – the model's validated
    choice, or the proposal itself (kept within the options) when no model
    answer can be used.
    """
    starts, ends = options(c, sentences, lo, hi)
    default = {"start_idx": c.start_idx if c.start_idx in starts else starts[-1],
               "end_idx": c.end_idx if c.end_idx in ends else ends[0],
               "cut_idx": list(c.cut_idx), "reasons": {}, "source": "proposal",
               "options": {"starts": len(starts), "ends": len(ends)}}
    if provider is None or (len(starts) == 1 and len(ends) == 1):
        return _fit_cuts(default, sentences, c)
    a, b = max(0, min(starts) - 2), min(len(sentences) - 1, max(ends) + 2)
    text = render(sentences[a:b + 1])
    st = "\n".join(f"{sentences[i].id} ({sentences[i].start:.1f}s; {_start_facts(sentences, i)}; "
                   f"clip ≈{duration(sentences, i, default['end_idx']):.0f}s): {sentences[i].text}" for i in starts)
    en = "\n".join(f"{sentences[j].id} ({sentences[j].end:.1f}s; {_end_facts(sentences, j)}; "
                   f"clip ≈{duration(sentences, default['start_idx'], j):.0f}s): {sentences[j].text}" for j in ends)
    ev = "\n".join(f"{e['role']}: {e['id']} “{e['quote']}”" for e in c.evidence)
    system, user = prompts.boundary_prompt(text, st, en, ev, min_s, max_s)
    try:
        data = provider.complete_json("boundaries", system, user, prompts.BOUNDARY_SCHEMA)
    except SemanticError as exc:
        log.warning("boundary choice failed for %s: %s", c.key, exc)
        return _fit_cuts({**default, "reasons": {"error": str(exc)}}, sentences, c)
    v = Validator(sentences)
    i, j = v.index(data.get("start_id")), v.index(data.get("end_id"))
    if i not in starts or j not in ends:
        return _fit_cuts({**default, "reasons": {"rejected": "ids_outside_options"}}, sentences, c)
    cuts = v.sentence_ids(data.get("cut_ids") or [], i, j)
    ev_idx = {k for e in c.evidence for k in range(e["idx"], e.get("idx_end", e["idx"]) + 1)}
    if cuts is None or any(k in ev_idx or k in (i, j) for k in cuts):
        cuts = [k for k in c.cut_idx if i < k < j]
        note = "cuts_rejected"
    else:
        note = ""
    out = {"start_idx": i, "end_idx": j, "cut_idx": cuts, "source": "model",
           "reasons": {"start": data.get("start_reason", ""), "end": data.get("end_reason", ""),
                       "cuts": data.get("cut_reason", ""), **({"note": note} if note else {})},
           "options": {"starts": len(starts), "ends": len(ends)}}
    return _fit_cuts(out, sentences, c)


def _fit_cuts(choice: dict[str, Any], sentences: Sequence[Sentence], c: Cand) -> dict[str, Any]:
    i, j = choice["start_idx"], choice["end_idx"]
    cuts = [k for k in choice.get("cut_idx") or [] if i < k < j]
    full = duration(sentences, i, j)
    if full > 0 and (full - duration(sentences, i, j, cuts)) / full > MAX_CUT_SHARE:
        cuts = []                    # cutting this much would change what the clip is
    choice["cut_idx"] = cuts
    choice["spans"] = [list(x) for x in spans_of(sentences, i, j, cuts)]
    choice["duration"] = round(duration(sentences, i, j, cuts), 2)
    return choice


def wide_options(c: Cand, sentences: Sequence[Sentence], lo: int, hi: int, *,
                 max_s: float = MAX_HARD) -> tuple[list[int], list[int]]:
    """
    The reconstruction pass's range: starts from RECON_EXTEND sentences before the proposal up to
    just before the closing evidence (a weak opening may be dropped), ends from the closing evidence
    up to RECON_EXTEND sentences after the proposal (a payoff the first cut stopped before).
    """
    starts = list(range(max(lo, c.start_idx - RECON_EXTEND), max(c.start_idx, c.closing_idx - 1) + 1))
    ends = list(range(c.closing_idx, min(hi, c.end_idx + RECON_EXTEND) + 1))
    cap = min(MAX_HARD, max_s * 1.5)
    starts = [i for i in starts if any(MIN_HARD <= duration(sentences, i, j) <= cap for j in ends if j > i)]
    ends = [j for j in ends if any(MIN_HARD <= duration(sentences, i, j) <= cap for i in starts if i < j)]
    if not starts or not ends:
        return options(c, sentences, lo, hi)
    return starts, ends


def reconstruct(provider: Optional[SemanticProvider], c: Cand, sentences: Sequence[Sentence], current: dict[str, Any],
                critique: str, *, lo: int, hi: int, min_s: float, max_s: float) -> Optional[dict[str, Any]]:
    """
    ONE intelligent rebuild of a strong moment's cut after the editor rejected the construction:
    a validated new {start_idx, end_idx, cut_idx, spans, duration}, or None (no model, the model
    says it cannot be fixed, or its answer is unusable). The evidence is never cut.
    """
    if provider is None:
        return None
    starts, ends = wide_options(c, sentences, lo, hi, max_s=max_s)
    a, b = max(0, min(starts) - 2), min(len(sentences) - 1, max(ends) + 2)
    text = render(sentences[a:b + 1])
    st = "\n".join(f"{sentences[i].id} ({sentences[i].start:.1f}s; {_start_facts(sentences, i)}): {sentences[i].text}"
                   for i in starts)
    en = "\n".join(f"{sentences[j].id} ({sentences[j].end:.1f}s; {_end_facts(sentences, j)}): {sentences[j].text}"
                   for j in ends)
    ev = "\n".join(f"{e['role']}: {e['id']} “{e['quote']}”" for e in c.evidence)
    cur = (f"{sentences[current['start_idx']].id} → {sentences[current['end_idx']].id} "
           f"({current.get('duration', 0):.0f} s), cut: "
           + (", ".join(sentences[i].id for i in current.get("cut_idx") or []) or "none"))
    system, user = prompts.reconstruct_prompt(text, st, en, ev, cur, critique, min_s, max_s)
    try:
        data = provider.complete_json("reconstruct", system, user, prompts.RECONSTRUCT_SCHEMA)
    except SemanticError as exc:
        log.warning("reconstruction failed for %s: %s", c.key, exc)
        return None
    if not data.get("fixable", True):
        return None
    v = Validator(sentences)
    i, j = v.index(data.get("start_id")), v.index(data.get("end_id"))
    if i not in starts or j not in ends or i >= j:
        return None
    cuts = v.sentence_ids(data.get("cut_ids") or [], i, j)
    ev_idx = {k for e in c.evidence for k in range(e["idx"], e.get("idx_end", e["idx"]) + 1)}
    if cuts is None or any(k in ev_idx or k in (i, j) for k in cuts):
        cuts = []
    out = {"start_idx": i, "end_idx": j, "cut_idx": cuts, "source": "reconstruct",
           "reasons": {"start": data.get("start_reason", ""), "end": data.get("end_reason", ""),
                       "cuts": data.get("cut_reason", "")},
           "options": {"starts": len(starts), "ends": len(ends), "wide": True}}
    out = _fit_cuts(out, sentences, c)
    if not (MIN_HARD <= out["duration"] <= MAX_HARD):
        return None
    return out
