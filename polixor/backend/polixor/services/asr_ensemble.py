"""
The final transcript of each Short – an ensemble, not a confidence score.

For every final clip:

  1. A  the strong model re-transcribes the whole clip (with a little padding);
  2. B  an independent second hypothesis: the cloud recogniser when the user
        configured it, otherwise a second local model of a different family;
  3. C  the full-source discovery transcript of the same span (free – it was
        decoded with different audio context);
  4. the hypotheses are aligned word by word; where they disagree the window
     is re-heard (D: the strong model on just that window, with the sentence
     before it as context and every heard alternative as a hint);
  5. a word variant heard by at least two hypotheses wins; otherwise the
     language model may choose – but ONLY between variants some hypothesis
     actually heard (validated: a choice that is not one of them is ignored);
     otherwise the word stays as A heard it and is marked unresolved;
  6. QA marks critical words – numbers, negations, names (words the
     hypotheses spell differently, project vocabulary), mixed Hebrew/English,
     loops – and counts unresolved critical words for the final editor.

The model confidence is recorded but decides nothing. Results are saved per
clip span (transcript.final.json) and reused by re-exports and regenerations.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from .transcribe import Segment, TranscriptResult, Word

log = logging.getLogger("polixor.asr_ensemble")

VERSION = 1
PAD = 0.6
REHEAR_PAD = 1.5
MAX_REHEARS = 24

_NIQQUD = re.compile(r"[֑-ׇ]")
_PUNCT = re.compile(r"[^\w֐-׿%']+", re.UNICODE)
_HE = re.compile(r"[א-ת]")
_LAT = re.compile(r"[A-Za-z]")
NEGATIONS = {"לא", "אין", "אל", "בלי", "אינו", "אינה", "אינם", "אף", "never", "not", "no", "don't", "didn't",
             "isn't", "wasn't", "won't", "can't", "nothing", "nobody"}
_NUMBER_WORDS = {"אחד", "אחת", "שתיים", "שניים", "שני", "שתי", "שלוש", "שלושה", "ארבע", "ארבעה", "חמש",
                 "חמישה", "שש", "שישה", "שבע", "שבעה", "שמונה", "תשע", "תשעה", "עשר", "עשרה", "עשרים",
                 "שלושים", "ארבעים", "חמישים", "מאה", "מאות", "אלף", "אלפים", "מיליון", "מיליארד", "מיליארדים"}

Engine = Callable[..., Optional[list[Segment]]]


def norm(text: str) -> str:
    t = _NIQQUD.sub("", text or "").replace("׳", "'").replace("״", '"').lower()
    return _PUNCT.sub("", t)


def critical_kind(text: str, vocabulary: Sequence[str] = ()) -> str:
    n = norm(text)
    if not n:
        return ""
    if any(ch.isdigit() for ch in n):
        return "number"
    bare = n[1:] if len(n) > 2 and n[0] in "והבכלמש" else n
    if n in NEGATIONS or bare in NEGATIONS:
        return "negation"
    if n in _NUMBER_WORDS or bare in _NUMBER_WORDS:
        return "number"
    if _HE.search(n) and _LAT.search(n):
        return "mixed_script"
    voc = {norm(v) for v in vocabulary for v in [v] + v.split()}
    if n in voc or bare in voc:
        return "name"
    # a near-miss of a known name ("ליברון" for "ליברמן") is a name too – and must be checked
    if len(bare) >= 4 and any(len(v) >= 4 and abs(len(v) - len(bare)) <= 2 and _edits(bare, v) <= (1 if len(v) < 6 else 2)
                              for v in voc):
        return "name"
    return ""


def _edits(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, y in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y))
        prev = cur
    return prev[-1]


@dataclass
class FinalWord:
    start: float
    end: float
    text: str
    status: str = "agreed"          # agreed | majority | adjudicated | unresolved | single
    alternatives: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    critical: str = ""
    p: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        d = {"start": round(self.start, 3), "end": round(self.end, 3), "text": self.text, "status": self.status}
        if self.alternatives:
            d["alts"] = self.alternatives
        if self.sources:
            d["src"] = self.sources
        if self.critical:
            d["critical"] = self.critical
        d["p"] = round(self.p, 3)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FinalWord":
        return cls(float(d["start"]), float(d["end"]), d["text"], d.get("status", "agreed"),
                   list(d.get("alts") or []), list(d.get("src") or []), d.get("critical", ""),
                   float(d.get("p", 1.0)))


def _ops(pivot: list[Word], other: list[Word]) -> list[tuple[str, int, int, int, int]]:
    return difflib.SequenceMatcher(a=[norm(w.text) for w in pivot], b=[norm(w.text) for w in other],
                                   autojunk=False).get_opcodes()


@dataclass
class Region:
    """A run of pivot words [i0, i1) where some hypothesis disagrees, with each hypothesis's reading."""
    i0: int
    i1: int
    variants: dict[str, list[str]] = field(default_factory=dict)   # hypothesis label -> words

    def options(self) -> dict[str, list[str]]:
        """normalised variant -> hypotheses that heard it ("" = heard nothing there)."""
        out: dict[str, list[str]] = {}
        for lab, ws in self.variants.items():
            out.setdefault(" ".join(n for n in (norm(w) for w in ws) if n), []).append(lab)
        return out


def _reading(ops, ws: list[Word], i0: int, i1: int) -> list[str]:
    """The other hypothesis's words for pivot words [i0, i1)."""
    out: list[str] = []
    for tag, a0, a1, b0, b1 in ops:
        if tag == "equal":
            lo, hi = max(a0, i0), min(a1, i1)
            if lo < hi:
                out += [w.text for w in ws[b0 + (lo - a0):b0 + (hi - a0)]]
        elif a0 == a1:                                   # inserted words belong to the word before
            if i0 <= a0 - 1 < i1 or (a0 == 0 and i0 == 0):
                out += [w.text for w in ws[b0:b1]]
        elif a0 < i1 and a1 > i0:
            out += [w.text for w in ws[b0:b1]]
    return out


def disagreements(pivot: list[Word], others: dict[str, list[Word]]) -> list[Region]:
    ops = {lab: _ops(pivot, ws) for lab, ws in others.items()}
    spans: list[tuple[int, int]] = []
    for lab, oc in ops.items():
        for tag, i0, i1, j0, j1 in oc:
            if tag == "equal":
                continue
            if i0 == i1:                                 # insertion: attach to the word before (or after)
                i0, i1 = (i0 - 1, i0) if i0 > 0 else (0, min(1, len(pivot)))
            if i1 > i0:
                spans.append((i0, i1))
    if not spans:
        return []
    spans.sort()
    merged = [list(spans[0])]
    for a, b in spans[1:]:
        if a < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out = []
    for a, b in merged:
        r = Region(a, b, {"A": [w.text for w in pivot[a:b]]})
        for lab, oc in ops.items():
            r.variants[lab] = _reading(oc, others[lab], a, b)
        out.append(r)
    return out


def build_clip(spans: Sequence[tuple[float, float]], *, strong: Engine, second: Optional[Engine],
               discovery: Optional[TranscriptResult], rehear: Optional[Engine],
               adjudicate: Optional[Callable[[list[dict[str, Any]]], dict[str, dict[str, Any]]]] = None,
               vocabulary: Sequence[str] = (), labels: Optional[dict[str, str]] = None,
               families: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """
    The final words of one clip (all of its spans) and the record of how they were decided.

    `families` maps hypothesis labels to model families (default: A, C and D are
    the strong model, B is independent). Agreement counts families, not passes:
    the strong model agreeing with itself is weak evidence – it was confidently
    wrong on names before. A variant wins outright only when two families heard
    it; otherwise the language model chooses between heard variants, and without
    it a same-family consensus stands for ordinary words while critical words
    (names, numbers, negations, mixed script) stay unresolved.
    """
    fam = {"A": "strong", "C": "strong", "D": "strong", "B": "second", **(families or {})}
    t0 = time.time()
    stats = {"words": 0, "agreed": 0, "majority": 0, "adjudicated": 0, "unresolved": 0, "single": 0,
             "regions": 0, "reheard": 0, "critical_unresolved": 0}
    pending: list[dict[str, Any]] = []
    all_cells: list[list[list[FinalWord]]] = []          # per span: one cell (list of words) per pivot word
    hyps_used: set[str] = set()
    for a, b in spans:
        A = [w for w in _words(strong(a - PAD, b + PAD)) if a - 0.25 <= (w.start + w.end) / 2 <= b + 0.25]
        if not A:
            # the strong model heard nothing here: the discovery words stay, marked single-source
            base = _words([Segment(start=a, end=b, text="", words=discovery.words_between(a, b))]) if discovery else []
            all_cells.append([[FinalWord(w.start, w.end, w.text, "single", [], ["C"],
                                         critical_kind(w.text, vocabulary), w.probability)] for w in base])
            if base:
                hyps_used.add("C")
            continue
        hyps_used.add("A")
        others: dict[str, list[Word]] = {}
        if second is not None:
            B = [w for w in _words(second(a - PAD, b + PAD)) if a - 0.25 <= (w.start + w.end) / 2 <= b + 0.25]
            if B:
                others["B"] = B
        if discovery is not None:
            C = _words([Segment(start=a, end=b, text="", words=discovery.words_between(a, b))])
            if C:
                others["C"] = C
        hyps_used.update(others)
        cells = [[FinalWord(w.start, w.end, w.text, "agreed" if others else "single", [], ["A"] + list(others),
                            critical_kind(w.text, vocabulary), w.probability)] for w in A]
        all_cells.append(cells)
        regions = disagreements(A, others) if others else []
        stats["regions"] += len(regions)
        for k, r in enumerate(regions):
            if rehear is not None and k < MAX_REHEARS:
                ra, rb = A[r.i0].start - REHEAR_PAD, A[r.i1 - 1].end + REHEAR_PAD
                context = " ".join(w.text for w in A[max(0, r.i0 - 12):r.i0])
                hint = ", ".join(sorted({" ".join(v) for v in r.variants.values() if v}))[:300]
                D = _words(rehear(ra, rb, prompt=context, hotwords=hint))
                if D:
                    # align the re-heard window to the pivot words of the same window
                    w0 = next(k for k in range(len(A)) if A[k].end > ra) if A[0].end <= ra else 0
                    w1 = max([k + 1 for k in range(len(A)) if A[k].start < rb] or [len(A)])
                    win = A[w0:w1]
                    r.variants["D"] = _reading(_ops(win, D), D, r.i0 - w0, r.i1 - w0)
                    stats["reheard"] += 1
                    hyps_used.add("D")
            opts = r.options()
            if len(opts) > 1:
                opts.pop("", None)                       # "heard nothing" never beats a heard variant
            alts = [_display(r, v) for v in opts]
            best_v, best_labs = max(opts.items(), key=lambda kv: (len({fam.get(x, x) for x in kv[1]}),
                                                                  len(kv[1]), "A" in kv[1]))
            crit = next((ck for v in r.variants.values() for t in v
                         for ck in [critical_kind(t, vocabulary)] if ck), "")
            if len(opts) == 1:
                _write(cells, r.i0, r.i1, A, _display(r, best_v), "agreed", alts, best_labs, crit)
            elif len({fam.get(x, x) for x in best_labs}) >= 2:
                _write(cells, r.i0, r.i1, A, _display(r, best_v), "majority", alts, best_labs, crit)
            else:
                pending.append({"id": f"d{len(pending) + 1}", "cells": cells, "i0": r.i0, "i1": r.i1, "pivot": A,
                                "alts": alts, "crit": crit, "best": _display(r, best_v), "best_labs": best_labs,
                                "heard_by": {_display(r, v): [fam.get(x, x) for x in labs] for v, labs in opts.items()},
                                "context": " ".join(w.text for w in A[max(0, r.i0 - 10):min(len(A), r.i1 + 10)]),
                                "disputed": " ".join(w.text for w in A[r.i0:r.i1])})
    # language-model adjudication – only between variants a hypothesis actually heard
    decisions: dict[str, dict[str, Any]] = {}
    if pending and adjudicate is not None:
        try:
            decisions = adjudicate([{"id": p["id"], "context": p["context"], "disputed": p["disputed"],
                                     "alternatives": p["alts"], "heard_by": p["heard_by"]} for p in pending]) or {}
        except Exception as exc:                          # noqa: BLE001
            log.warning("adjudication failed: %s", exc)
    for p in pending:
        d = decisions.get(p["id"]) or {}
        choice = norm(str(d.get("choice") or ""))
        match = next((x for x in p["alts"] if norm(x) == choice), None) if not d.get("unresolved") else None
        if match is not None:
            _write(p["cells"], p["i0"], p["i1"], p["pivot"], match, "adjudicated", p["alts"], ["model"], p["crit"])
        elif not d and not p["crit"] and len(p["best_labs"]) >= 2:
            # no judge: the strong model's repeated reading stands for an ordinary word
            _write(p["cells"], p["i0"], p["i1"], p["pivot"], p["best"], "majority", p["alts"], p["best_labs"], "")
        else:
            for cell in p["cells"][p["i0"]:p["i1"]]:
                for w in cell:
                    w.status, w.alternatives, w.critical = "unresolved", p["alts"], w.critical or p["crit"]
    out_words = [w for cells in all_cells for cell in cells for w in cell]
    for w in out_words:
        stats["words"] += 1
        stats[w.status] = stats.get(w.status, 0) + 1
        if w.status == "unresolved" and w.critical:
            stats["critical_unresolved"] += 1
    from . import asr_loops

    loops = asr_loops.find_loops([Segment(start=w.start, end=w.end, text=w.text,
                                          words=[Word(w.start, w.end, w.text, w.p)]) for w in out_words])
    return {"spans": [[round(a, 3), round(b, 3)] for a, b in spans],
            "words": [w.to_dict() for w in out_words], "stats": stats,
            "loops": [lp.to_dict() for lp in loops],
            "hypotheses": {k: (labels or {}).get(k, k) for k in sorted(hyps_used)},
            "seconds": round(time.time() - t0, 2)}


def _words(segs: Optional[Sequence[Segment]]) -> list[Word]:
    return [Word(w.start, w.end, w.text.strip(), w.probability, flag=w.flag, asr=w.asr)
            for s in segs or [] for w in s.words if (w.text or "").strip()]


def _display(r: Region, normalised: str) -> str:
    """The variant as one of the hypotheses actually wrote it (with its punctuation)."""
    for lab, ws in r.variants.items():
        if " ".join(n for n in (norm(w) for w in ws) if n) == normalised:
            return " ".join(ws)
    return normalised


def _write(cells: list[list[FinalWord]], i0: int, i1: int, pivot: list[Word], text: str, status: str,
           alts: list[str], sources: list[str], crit: str) -> None:
    """Writes the chosen variant over pivot words [i0, i1); new words share the region's time span."""
    toks = text.split()
    if [w.text for w in pivot[i0:i1]] == toks:
        for k in range(i0, i1):
            for w in cells[k]:
                w.status, w.sources = status, sources
                w.alternatives = alts if status != "agreed" else []
                w.critical = w.critical or crit
        return
    a, b = pivot[i0].start, pivot[i1 - 1].end
    n = max(1, len(toks))
    step = (b - a) / n
    cells[i0] = [FinalWord(a + k * step, a + (k + 1) * step - 0.01, t, status, alts, sources,
                           crit or critical_kind(t), 1.0) for k, t in enumerate(toks)]
    for k in range(i0 + 1, i1):
        cells[k] = []


# --------------------------------------------------------------------------
# storage and application
# --------------------------------------------------------------------------
def span_key(spans: Sequence[Sequence[float]]) -> str:
    return ";".join(f"{float(a):.2f}-{float(b):.2f}" for a, b in spans)


def load(path: Optional[Path]) -> dict[str, Any]:
    if path is None or not Path(path).exists():
        return {"version": VERSION, "clips": {}}
    try:
        data = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return {"version": VERSION, "clips": {}}
    if data.get("version") != VERSION:
        return {"version": VERSION, "clips": {}}
    return data


def save(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    tmp.replace(path)
    return path


def apply(transcript: TranscriptResult, data: dict[str, Any]) -> TranscriptResult:
    """The transcript with every finalised clip span replaced by its final words."""
    clips = (data or {}).get("clips") or {}
    if not clips:
        return transcript
    spans: list[tuple[float, float, list[Word]]] = []
    for rec in clips.values():
        words = [FinalWord.from_dict(w) for w in rec.get("words") or []]
        for a, b in rec.get("spans") or []:
            inside = [Word(w.start, w.end, w.text, w.p, flag="uncertain" if w.status == "unresolved" else "",
                           asr="final") for w in words if a - 0.3 <= (w.start + w.end) / 2 <= b + 0.3]
            if inside:
                spans.append((float(a), float(b), inside))
    if not spans:
        return transcript
    spans.sort(key=lambda x: x[0])
    segs: list[Segment] = []
    for s in transcript.segments:
        mid_in = any(a <= (s.start + s.end) / 2 <= b for a, b, _ in spans)
        if not mid_in:
            # keep words outside every final span
            keep = [w for w in s.words if not any(a <= (w.start + w.end) / 2 <= b for a, b, _ in spans)]
            if keep and len(keep) != len(s.words):
                segs.append(Segment(start=keep[0].start, end=keep[-1].end, text=" ".join(w.text for w in keep),
                                    words=keep, language=s.language))
            elif keep:
                segs.append(s)
    for a, b, ws in spans:
        segs.append(Segment(start=ws[0].start, end=ws[-1].end, text=" ".join(w.text for w in ws), words=ws,
                            language=transcript.language))
    segs.sort(key=lambda s: s.start)
    return TranscriptResult(segments=segs, language=transcript.language, duration=transcript.duration,
                            provider=transcript.provider, model=transcript.model, note=transcript.note,
                            meta=dict(transcript.meta or {}))


def adjudicator(provider) -> Callable[[list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    """The semantic model as a constrained judge between heard alternatives."""
    from .semantic import prompts

    def run(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        lines = []
        for it in items:
            sent = it["context"].replace(it["disputed"], f"⟦{it['disputed']}⟧", 1) if it["disputed"] else it["context"]
            heard = it.get("heard_by") or {}
            lines.append(f"{it['id']}: {sent}\n   alternatives: " + " | ".join(
                f"“{a}” (heard by: {', '.join(sorted(set(heard.get(a, []))) or ['?'])})" for a in it["alternatives"]))
        system, user = prompts.adjudicate_prompt("\n".join(lines))
        data = provider.complete_json("adjudicate", system, user, prompts.ADJUDICATE_SCHEMA, max_tokens=8000)
        return {str(d.get("id")): d for d in data.get("decisions") or []}

    return run
