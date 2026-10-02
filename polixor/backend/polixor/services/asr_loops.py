"""
Hallucination loops in speech recognition.

Whisper-family models sometimes repeat one phrase many times ("איך אתה
מסביר את…" ×13) – with high confidence, so confidence filters never see it.
A loop is found from the words alone: the same n-gram (1–8 words) repeated
back to back at least LOOP_MIN_REPEATS times (6 for single words, since
"לא לא לא" is speech). The transcriber then re-hears the region with
anti-repetition decoding; when that also loops, the repeats are collapsed
to one copy and the words are flagged, never silently kept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

from .transcribe import Segment, Word

LOOP_MIN_REPEATS = 4
LOOP_MIN_REPEATS_UNIGRAM = 6
MAX_NGRAM = 8

_CLEAN = re.compile(r"[^\w֐-׿']+", re.UNICODE)


@dataclass
class Loop:
    start: float
    end: float
    ngram: int
    repeats: int
    first_end: float          # end time of the first copy (what is kept when collapsing)

    def to_dict(self) -> dict:
        return {"start": round(self.start, 2), "end": round(self.end, 2), "ngram": self.ngram,
                "repeats": self.repeats}


def _norm(text: str) -> str:
    return _CLEAN.sub("", (text or "").lower())


def find_loops(segments: Sequence[Segment]) -> list[Loop]:
    """Back-to-back repeated n-grams across the given segments (word level)."""
    words = [w for s in segments for w in s.words if _norm(w.text)]
    toks = [_norm(w.text) for w in words]
    loops: list[Loop] = []
    i = 0
    n_tok = len(toks)
    while i < n_tok:
        best: Optional[tuple[int, int]] = None           # (ngram, repeats)
        for n in range(1, MAX_NGRAM + 1):
            if i + 2 * n > n_tok:
                break
            gram = toks[i:i + n]
            reps = 1
            while toks[i + reps * n:i + (reps + 1) * n] == gram:
                reps += 1
            need = LOOP_MIN_REPEATS_UNIGRAM if n == 1 else LOOP_MIN_REPEATS
            if reps >= need and (best is None or reps * n > best[0] * best[1]):
                best = (n, reps)
        if best is None:
            i += 1
            continue
        n, reps = best
        last = i + n * reps - 1
        loops.append(Loop(start=words[i].start, end=words[last].end, ngram=n, repeats=reps,
                          first_end=words[i + n - 1].end))
        i = last + 1
    return loops


def collapse(segments: list[Segment], loop: Loop) -> list[Segment]:
    """Keeps the first copy of the looping phrase (flagged) and drops the rest."""
    out: list[Segment] = []
    for s in segments:
        if s.end <= loop.start or s.start >= loop.end:
            out.append(s)
            continue
        kept: list[Word] = []
        for w in s.words:
            if loop.start - 1e-3 <= w.start and w.end <= loop.end + 1e-3:
                if w.end <= loop.first_end + 1e-3:
                    kept.append(Word(w.start, w.end, w.text, w.probability, flag="loop", asr=w.asr))
                continue
            kept.append(w)
        if kept:
            out.append(Segment(start=kept[0].start, end=kept[-1].end, text=" ".join(w.text for w in kept),
                               words=kept, language=s.language, avg_logprob=s.avg_logprob,
                               no_speech_prob=s.no_speech_prob))
    return out


def replace_region(segments: list[Segment], a: float, b: float, new: Sequence[Segment]) -> list[Segment]:
    """Segments outside [a, b] stay; the region gets the new hypothesis."""
    keep = [s for s in segments if (s.start + s.end) / 2.0 < a or (s.start + s.end) / 2.0 > b]
    merged = keep + [s for s in new if a <= (s.start + s.end) / 2.0 <= b]
    return sorted(merged, key=lambda s: s.start)


def repair(segments: list[Segment], rehear) -> tuple[list[Segment], list[dict]]:
    """
    Finds loops; for each, `rehear(a, b)` returns another hypothesis of the
    region (anti-repetition decoding) or None. A loop-free, non-empty
    re-hearing replaces the region; otherwise the loop is collapsed.
    """
    report: list[dict] = []
    for lp in find_loops(segments):
        a, b = lp.start - 0.5, lp.end + 0.5
        alt = rehear(a, b) if rehear is not None else None
        if alt and not find_loops(alt) and any(w for s in alt for w in s.words):
            segments = replace_region(segments, a, b, alt)
            report.append({**lp.to_dict(), "action": "re-heard"})
        else:
            segments = collapse(segments, lp)
            report.append({**lp.to_dict(), "action": "collapsed"})
    return segments, report
