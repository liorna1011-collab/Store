"""
Final timing of subtitle cues – the last step before a cue is burned in.

Cues are grouped from word timings, and speech-recognition word timings have
known, systematic faults: the last word of a phrase is often stretched into
the following silence (the cue then hangs on screen after the speaker
stopped), a cue appears on the exact frame the word starts (readers perceive
that as late), and short phrases make cards that flash for a few frames.

finalize() fixes these with fixed, explainable rules, per segment of the clip
(never across an internal cut):

  * a word longer than is plausible for its length ends where it plausibly
    ends (the cue no longer lingers on a stretched word)
  * a cue appears LEAD seconds before its first word – never before the
    previous cue has gone, never before the segment starts
  * a cue stays HOLD seconds after its last word, or bridges a short gap to
    the next cue (no blink between consecutive cards)
  * a card shorter than MIN_CARD is lengthened into free time, or merged
    with its neighbour when there is none
  * cues never overlap

check() reports what is still wrong (deterministic QA, stored with the clip).
The text never changes here.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence

LEAD = 0.08            # a card appears just before the word: perceived as on time
HOLD = 0.25            # and stays a moment after the last word
BRIDGE = 0.45          # a gap shorter than this between cards is closed (no blink)
MIN_CARD = 0.7         # shorter cards are hard to read
MAX_LINGER = 0.6       # QA: a card staying longer than this after its last word lingers


def plausible_seconds(text: str) -> float:
    """The longest believable duration of one spoken word (a stretched word ends here)."""
    n = len(re.sub(r"\W", "", text or "")) or 1
    return float(min(1.6, max(0.45, 0.11 * n + 0.3)))


def _f(w: dict[str, Any], k: str, default: float) -> float:
    try:
        return float(w.get(k, default))
    except (TypeError, ValueError):
        return default


def finalize(cues: list, *, duration: Optional[float] = None) -> list:
    """Fixes the timing of cues (subtitles.Cue) of one segment in place and returns them, sorted."""
    cues = [c for c in cues if (c.text or "").strip()]
    cues.sort(key=lambda c: c.start)
    end_limit = duration if duration is not None else float("inf")
    for c in cues:
        ws = c.words or []
        for w in ws:
            s, e = _f(w, "start", c.start), _f(w, "end", c.end)
            cap = s + plausible_seconds(str(w.get("text", "")))
            if e > cap:
                w["end"] = round(cap, 3)
        if ws:
            first = _f(ws[0], "start", c.start)
            last = max(_f(w, "end", c.end) for w in ws)
            c.start, c.end = first, last
    for i, c in enumerate(cues):
        prev_end = cues[i - 1].end if i else 0.0
        c.start = max(prev_end, 0.0, c.start - LEAD)
        nxt = cues[i + 1].start - LEAD if i + 1 < len(cues) else end_limit
        if nxt - c.end <= BRIDGE:
            c.end = max(c.end, nxt)
        else:
            c.end = c.end + HOLD
        c.end = min(c.end, end_limit, nxt if nxt > c.start else c.end)
        if c.end - c.start < MIN_CARD:
            c.end = min(max(c.end, c.start + MIN_CARD), end_limit, nxt if nxt > c.start else end_limit)
    # a card that is still a flash merges into its neighbour (the previous one, or the next one
    # when it is first) – text order kept, nothing is dropped
    out: list = []
    carry = None
    for c in cues:
        if carry is not None:
            c.text = f"{carry.text} {c.text}".strip()
            c.words = list(carry.words or []) + list(c.words or [])
            c.start = carry.start
            carry = None
        short = c.end - c.start < MIN_CARD * 0.6
        if short and out and c.start - out[-1].end < BRIDGE:
            p = out[-1]
            p.text = f"{p.text} {c.text}".strip()
            p.words = list(p.words or []) + list(c.words or [])
            p.end = max(p.end, c.end)
            continue
        if short and not out and c is not cues[-1]:
            nxt = cues[cues.index(c) + 1]
            if nxt.start - c.end < BRIDGE:
                carry = c
                continue
        out.append(c)
    for c in out:
        c.start, c.end = round(c.start, 3), round(max(c.end, c.start + 0.2), 3)
    return out


def check(cues: Sequence, *, duration: Optional[float] = None) -> dict[str, Any]:
    """
    Deterministic timing QA of the final cues (clip time). Problems:
    early (card well before its first word), late (card after its first word),
    linger (card stays long after its last word), flash (too short to read),
    overlap, outside (past the end of the clip).
    """
    problems: list[dict[str, Any]] = []
    for i, c in enumerate(cues):
        ws = c.words or []
        if ws:
            first = _f(ws[0], "start", c.start)
            last = max(_f(w, "end", c.end) for w in ws)
            if c.start < first - 0.3:
                problems.append({"cue": i, "kind": "early", "by": round(first - c.start, 2)})
            if c.start > first + 0.05:
                problems.append({"cue": i, "kind": "late", "by": round(c.start - first, 2)})
            nxt = cues[i + 1].start if i + 1 < len(cues) else None
            bridged = nxt is not None and nxt - c.end < 0.05 and nxt - last <= BRIDGE + HOLD + LEAD
            if c.end - last > MAX_LINGER and not bridged:
                problems.append({"cue": i, "kind": "linger", "by": round(c.end - last, 2)})
        if c.end - c.start < 0.5:
            problems.append({"cue": i, "kind": "flash", "by": round(c.end - c.start, 2)})
        if i + 1 < len(cues) and c.end > cues[i + 1].start + 1e-3:
            problems.append({"cue": i, "kind": "overlap", "by": round(c.end - cues[i + 1].start, 2)})
        if duration is not None and c.end > duration + 0.05:
            problems.append({"cue": i, "kind": "outside", "by": round(c.end - duration, 2)})
    kinds: dict[str, int] = {}
    for p in problems:
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
    return {"cues": len(cues), "problems": len(problems), "kinds": kinds, "examples": problems[:8],
            "ok": not problems}
