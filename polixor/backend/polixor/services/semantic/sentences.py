"""
Sentences and turns, reconstructed from the word-level transcript.

The ASR's own segments can glue several speakers and sentences together (the
fast model averaged ~23 s per segment on the news source), so a sentence also
ends at punctuation, at a pause, and when it grows too long – and never runs
across one of the recogniser's segment boundaries. Everything the
semantic layer says is anchored to these sentences: each has a stable ID
("s0042"), exact start/end times from its words, and its exact text, so a
model answer can be checked word for word.

Turns: without a speaker-diarization model a new turn is opened at a long
pause, or after a question that is followed by a pause. That is a guess
about *structure*, not about *who* spoke – `speaker` stays None unless a
diarization result is supplied, and the validator rejects any model claim
about who said what when there is no such evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Sequence

from ..transcribe import TranscriptResult, Word

MAX_WORDS = 40
PAUSE_SPLIT = 0.7          # a pause this long ends a sentence (with ≥ MIN_WORDS words)
MIN_WORDS = 3
TURN_PAUSE = 1.2           # a pause this long opens a new turn
QUESTION_TURN_PAUSE = 0.35
LOW_P = 0.5

_END = re.compile(r"[.?!…]+[\"'”״)]*$")
_QUESTION = re.compile(r"\?[\"'”״)]*$")


@dataclass
class Sentence:
    id: str
    start: float
    end: float
    text: str
    turn: int
    words: list[tuple[float, float, str]] = field(default_factory=list)
    question: bool = False
    pause_before: float = 0.0
    low_confidence: float = 0.0          # share of words with p < LOW_P (a weak signal only)
    flags: list[str] = field(default_factory=list)   # loop | unresolved | fast_asr
    speaker: Optional[str] = None        # only from diarization

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["words"] = [[round(a, 3), round(b, 3), t] for a, b, t in self.words]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Sentence":
        return cls(id=d["id"], start=float(d["start"]), end=float(d["end"]), text=d["text"],
                   turn=int(d.get("turn", 0)),
                   words=[(float(a), float(b), str(t)) for a, b, t in d.get("words") or []],
                   question=bool(d.get("question")), pause_before=float(d.get("pause_before", 0.0)),
                   low_confidence=float(d.get("low_confidence", 0.0)), flags=list(d.get("flags") or []),
                   speaker=d.get("speaker"))


def _ends(text: str) -> bool:
    return bool(_END.search(text.strip()))


def _split_long(words: list[Word]) -> list[list[Word]]:
    """A run longer than MAX_WORDS is cut at its longest internal pause (recursively)."""
    if len(words) <= MAX_WORDS:
        return [words]
    lo, hi = MIN_WORDS, len(words) - MIN_WORDS
    best = max(range(lo, hi), key=lambda i: (words[i].start - words[i - 1].end, -abs(i - len(words) / 2)))
    return _split_long(words[:best]) + _split_long(words[best:])


def build_sentences(transcript: Optional[TranscriptResult],
                    diarization: Optional[Sequence[tuple[float, float, str]]] = None) -> list[Sentence]:
    if transcript is None:
        return []
    words: list[Word] = []
    seg_end: set[int] = set()             # the recogniser's own segment boundaries are sentence boundaries too
    for s in transcript.segments:
        ws = [w for w in s.words if (w.text or "").strip()]
        words += ws
        if ws:
            seg_end.add(id(ws[-1]))
    words.sort(key=lambda w: w.start)
    groups: list[list[Word]] = []
    cur: list[Word] = []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt.start - w.end) if nxt is not None else 99.0
        if nxt is None or _ends(w.text) or id(w) in seg_end or (gap >= PAUSE_SPLIT and len(cur) >= MIN_WORDS):
            groups.extend(_split_long(cur))
            cur = []
    if cur:
        groups.extend(_split_long(cur))

    mixed = any(w.asr == "strong" for w in words)
    out: list[Sentence] = []
    turn = 0
    prev_end: Optional[float] = None
    prev_q = False
    for k, g in enumerate(groups):
        text = " ".join(w.text.strip() for w in g)
        pause = (g[0].start - prev_end) if prev_end is not None else 0.0
        if prev_end is not None and (pause >= TURN_PAUSE or (prev_q and pause >= QUESTION_TURN_PAUSE)):
            turn += 1
        q = bool(_QUESTION.search(text))
        flags = []
        if any(w.flag == "loop" for w in g):
            flags.append("loop")
        if mixed and not any(w.asr == "strong" for w in g):
            flags.append("fast_asr")          # only the fast model heard this sentence
        out.append(Sentence(
            id=f"s{k + 1:04d}", start=round(g[0].start, 3), end=round(g[-1].end, 3), text=text, turn=turn,
            words=[(w.start, w.end, w.text.strip()) for w in g], question=q, pause_before=round(max(0.0, pause), 3),
            low_confidence=round(sum(1 for w in g if w.probability < LOW_P) / len(g), 3), flags=flags))
        prev_end, prev_q = g[-1].end, q
    if diarization:
        _attach_speakers(out, diarization)
    return out


def _attach_speakers(sentences: list[Sentence], dia: Sequence[tuple[float, float, str]]) -> None:
    for s in sentences:
        best, label = 0.0, None
        for a, b, spk in dia:
            ov = max(0.0, min(b, s.end) - max(a, s.start))
            if ov > best:
                best, label = ov, spk
        if label is not None and best >= 0.6 * s.duration:
            s.speaker = label


def fingerprint(transcript: Optional[TranscriptResult]) -> str:
    """Content hash of a transcript (words and times) – the checkpoint key of everything built on it."""
    h = hashlib.sha1()
    if transcript is not None:
        for s in transcript.segments:
            for w in s.words:
                # the precision the transcript is saved with (Word.to_dict): a reloaded
                # transcript must hash exactly like the one in memory, or resume re-pays
                h.update(f"{round(w.start, 3):.3f}|{round(w.end, 3):.3f}|{w.text}|{w.asr}|{w.flag};"
                         .encode("utf-8"))
    return h.hexdigest()[:16]


def save(sentences: list[Sentence]) -> list[dict[str, Any]]:
    return [s.to_dict() for s in sentences]


def load(items: Sequence[dict[str, Any]]) -> list[Sentence]:
    return [Sentence.from_dict(d) for d in items]


def render(sentences: Sequence[Sentence], *, with_turns: bool = True) -> str:
    """The transcript as the model reads it: one sentence per line, ID, times and turn."""
    lines = []
    for s in sentences:
        tag = f"[{s.id} {s.start:.1f}-{s.end:.1f}" + (f" t{s.turn}" if with_turns else "") \
              + (f" {s.speaker}" if s.speaker else "") + ("" if not s.flags else " " + ",".join(s.flags)) + "]"
        lines.append(f"{tag} {s.text}")
    return "\n".join(lines)


def by_id(sentences: Sequence[Sentence]) -> dict[str, int]:
    return {s.id: i for i, s in enumerate(sentences)}


def dumps(sentences: Sequence[Sentence]) -> str:
    return json.dumps(save(list(sentences)), ensure_ascii=False)
