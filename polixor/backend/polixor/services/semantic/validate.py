"""
Deterministic validation of everything the language model says.

The model reads sentences with IDs and times; every claim it makes must
point back to them. This module rejects, with a reason:

  * missing or unknown sentence IDs, or a range that runs backwards;
  * quotes that are not in the cited sentence (after normalising
    punctuation and niqqud) – the model may not invent or paraphrase words;
  * evidence out of order (the payoff before the setup, the answer before
    the question);
  * timestamps that do not belong to the cited sentences;
  * a structured moment without its closing evidence (a question with no
    answer, an accusation with no response);
  * any claim about who said something when the transcript has no speaker
    diarization, or a claim that contradicts it.

Nothing here judges quality; it only decides whether an answer is grounded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from .sentences import Sentence

# candidate types and the order of their evidence roles (the last role closes the moment)
TYPES: dict[str, tuple[str, ...]] = {
    "question_answer": ("question", "answer"),
    "claim_explanation": ("claim", "explanation"),
    "accusation_response": ("accusation", "response"),
    "disagreement": ("disagreement", "argument", "conclusion"),
    "setup_payoff": ("setup", "payoff"),
    "opinion_evidence_verdict": ("opinion", "evidence", "verdict"),
    "story": ("story", "turn", "conclusion"),
    "strong_quote": ("quote",),
    "emotional": ("moment",),
    "surprising": ("moment",),
    "useful": ("moment",),
}
# roles that may be skipped (a verdict can follow an opinion without separate evidence)
OPTIONAL_ROLES = {"argument", "evidence", "turn", "explanation"}
TIME_SLACK = 1.0

_NIQQUD = re.compile(r"[֑-ׇ]")
_TOK = re.compile(r"[\w֐-׿']+", re.UNICODE)


def tokens(text: str) -> list[str]:
    t = _NIQQUD.sub("", text or "").replace("׳", "'").replace("״", '"').lower()
    t = re.sub(r"(\d)[,.](\d{3})", r"\1\2", t)
    return [x.strip("'") for x in _TOK.findall(t) if x.strip("'")]


_HE_PREFIX = "והבכלמש"


def same_word(a: str, b: str) -> bool:
    """Equal, or equal up to one Hebrew prefix letter (ו/ה/ב/כ/ל/מ/ש) – "ואין" quotes "אין"."""
    if a == b:
        return True
    if len(a) == len(b) + 1 and len(b) >= 2 and a[0] in _HE_PREFIX and a[1:] == b:
        return True
    return len(b) == len(a) + 1 and len(a) >= 2 and b[0] in _HE_PREFIX and b[1:] == a


def contains(hay: Sequence[str], needle: Sequence[str]) -> bool:
    n = len(needle)
    if n == 0 or n > len(hay):
        return False
    return any(all(same_word(hay[i + k], needle[k]) for k in range(n)) for i in range(len(hay) - n + 1))


def quote_in(quote: str, sentences: Sequence[Sentence], i: int) -> bool:
    """The quote's words, in order, inside sentence i (or running on into sentence i+1)."""
    return locate(quote, sentences, i) is not None


def locate(quote: str, sentences: Sequence[Sentence], i: int) -> Optional[tuple[int, int]]:
    """
    The sentences that really hold the quote, given the cited sentence i:
    (i, i) inside it; (i+1, i+1) when the model cited the sentence before;
    (i, i+1) when the quote runs from i into i+1. None: not there.
    """
    q = tokens(quote)
    if not q:
        return None
    hay = tokens(sentences[i].text)
    if contains(hay, q):
        return i, i
    if i + 1 < len(sentences):
        nxt = tokens(sentences[i + 1].text)
        if contains(nxt, q):
            return i + 1, i + 1
        if contains(hay + nxt, q):
            return i, i + 1
    return None


@dataclass
class Checked:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    start_idx: int = -1
    end_idx: int = -1
    evidence: list[dict[str, Any]] = field(default_factory=list)   # [{role, idx, id, quote}]


class Validator:
    def __init__(self, sentences: Sequence[Sentence]) -> None:
        self.sentences = list(sentences)
        self.idx = {s.id: i for i, s in enumerate(self.sentences)}
        self.diarized = any(s.speaker for s in self.sentences)

    # ---- pieces ----
    def index(self, sid: Any) -> Optional[int]:
        return self.idx.get(str(sid or "").strip())

    def span(self, start_id: Any, end_id: Any) -> tuple[Optional[int], Optional[int], list[str]]:
        a, b = self.index(start_id), self.index(end_id)
        if a is None or b is None:
            return None, None, ["missing_or_unknown_ids"]
        if b < a:
            return None, None, ["range_backwards"]
        return a, b, []

    def times_ok(self, item: dict[str, Any], a: int, b: int) -> bool:
        """Times the model repeats must belong to the cited sentences (±TIME_SLACK)."""
        s0, s1 = self.sentences[a], self.sentences[b]
        for key, ref in (("start", s0.start), ("end", s1.end)):
            v = item.get(key)
            if v is None:
                continue
            try:
                if abs(float(v) - ref) > TIME_SLACK:
                    return False
            except (TypeError, ValueError):
                return False
        return True

    def speaker_ok(self, item: dict[str, Any], evidence: Sequence[dict[str, Any]]) -> bool:
        claims = [e for e in evidence if str(e.get("speaker") or "").strip()]
        if item.get("speaker"):
            claims.append({"speaker": item["speaker"], "idx": None})
        if not claims:
            return True
        if not self.diarized:
            return False
        for e in claims:
            if e.get("idx") is not None and self.sentences[e["idx"]].speaker != str(e["speaker"]).strip():
                return False
        return True

    # ---- a typed candidate ----
    def candidate(self, item: dict[str, Any]) -> Checked:
        reasons: list[str] = []
        kind = str(item.get("type") or "")
        if kind not in TYPES:
            return Checked(False, ["unknown_type"])
        a, b, why = self.span(item.get("start_id"), item.get("end_id"))
        if why:
            return Checked(False, why)
        assert a is not None and b is not None
        if not self.times_ok(item, a, b):
            reasons.append("impossible_timestamps")
        ev_out: list[dict[str, Any]] = []
        for e in item.get("evidence") or []:
            i = self.index(e.get("sentence_id"))
            if i is None:
                reasons.append("evidence_unknown_id")
                continue
            quote = str(e.get("quote") or "")
            where = locate(quote, self.sentences, i)
            if where is None:
                reasons.append("invented_quote")
                continue
            i, i_end = where
            if not (a <= i and i_end <= b):
                reasons.append("evidence_outside_span")
                continue
            ev_out.append({"role": str(e.get("role") or ""), "idx": i, "idx_end": i_end,
                           "id": self.sentences[i].id, "quote": quote, "speaker": e.get("speaker")})
        if not ev_out:
            reasons.append("no_evidence")
        roles = TYPES[kind]
        order = {r: k for k, r in enumerate(roles)}
        seen = [e for e in ev_out if e["role"] in order]
        if any(e["role"] not in order for e in ev_out):
            reasons.append("unknown_role")
        # evidence in role order: setup before payoff, question before answer
        for x, y in zip(seen, seen[1:]):
            if order[x["role"]] > order[y["role"]] or (order[x["role"]] < order[y["role"]] and x["idx"] > y["idx"]):
                reasons.append("reordered_evidence")
                break
        needed = [r for r in roles if r not in OPTIONAL_ROLES]
        have = {e["role"] for e in seen}
        if any(r not in have for r in needed):
            reasons.append("missing_" + next(r for r in needed if r not in have))
        if not self.speaker_ok(item, ev_out):
            reasons.append("unsupported_speaker_attribution")
        return Checked(not reasons, sorted(set(reasons), key=reasons.index), a, b, ev_out)

    # ---- a topic ----
    def topic(self, item: dict[str, Any]) -> Checked:
        a, b, why = self.span(item.get("start_id"), item.get("end_id"))
        if why:
            return Checked(False, why)
        assert a is not None and b is not None
        reasons = [] if self.times_ok(item, a, b) else ["impossible_timestamps"]
        if not str(item.get("title") or "").strip():
            reasons.append("no_title")
        return Checked(not reasons, reasons, a, b)

    def sentence_ids(self, ids: Sequence[Any], lo: int, hi: int) -> Optional[list[int]]:
        """IDs that must all exist inside [lo, hi] (internal cuts, junk ranges)."""
        out = []
        for sid in ids:
            i = self.index(sid)
            if i is None or not (lo <= i <= hi):
                return None
            out.append(i)
        return sorted(set(out))
