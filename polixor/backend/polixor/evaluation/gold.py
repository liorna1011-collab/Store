"""
Scoring a run against a human gold reference.

A gold file (docs/GOLD_SCHEMA.md) says which moments of one source an editor
would ship, which are maybes, which must never be chosen, which topics make
long-form videos, and which words (names, numbers, key phrases) a correct
subtitle must contain. Words nobody has verified by ear are marked
"unresolved" and are never scored.

The metrics do not use the clip engine in any way: a run is judged only by
what it shipped (time ranges, the final subtitle text, the on-screen hook),
what was in its candidate pool, and what it declared about itself (semantic
or degraded mode, how much of the source it heard with the strong model).

Text in a committed gold file is stored as salted per-token hashes, so real
transcript excerpts never enter the repository; a local gold file may keep
plain text (both forms are read the same way).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

SCHEMA = "polixor.gold/1"
LABELS = ("ship", "maybe", "hold", "no")
CREDIT = {"ship": 1.0, "maybe": 0.5, "hold": 0.0, "no": 0.0}

# a clip "is" a gold moment when it contains the moment's hook and payoff
# (±ANCHOR_SLACK) and at least half of the clip lies inside the moment
ANCHOR_SLACK = 0.5
MIN_INSIDE = 0.5
# without anchors (ratings of earlier clips): enough of the moment is covered
MIN_COVER = 0.6
# the pool "has" a moment when some candidate covers its payoff and 40% of it
POOL_COVER = 0.4
DUPLICATE_IOU = 0.3
NEGATIVE_SHARE = 0.5

_NIQQUD = re.compile(r"[֑-ׇ]")
_TOKEN = re.compile(r"[\w֐-׿']+", re.UNICODE)
_THOUSANDS = re.compile(r"(\d)[,٬](\d{3})")
_HE_PREFIX = "והבכלמש"


# --------------------------------------------------------------------------
# text
# --------------------------------------------------------------------------
def normalize_tokens(text: str) -> list[str]:
    text = _NIQQUD.sub("", text or "").replace("׳", "'").replace("״", '"')
    text = _THOUSANDS.sub(r"\1\2", text).lower()
    return [t.strip("'") for t in _TOKEN.findall(text) if t.strip("'")]


def variants(tok: str) -> list[str]:
    """The token and the token without one or two Hebrew prefix letters (ו, ה, ב, ל...)."""
    out = [tok]
    rest = tok
    for _ in range(2):
        if len(rest) > 3 and rest[0] in _HE_PREFIX:
            rest = rest[1:]
            out.append(rest)
        else:
            break
    return out


def token_hash(tok: str, salt: str) -> str:
    return hashlib.sha1(f"{salt}|{tok}".encode("utf-8")).hexdigest()[:10]


@dataclass
class Phrase:
    """Words that must appear, in order, in correct subtitles."""
    hashes: list[str]
    text: str = ""                  # only in local gold files

    @classmethod
    def parse(cls, raw: Any, salt: str) -> Optional["Phrase"]:
        if isinstance(raw, str):
            raw = {"text": raw}
        if not isinstance(raw, dict):
            return None
        if raw.get("text"):
            toks = normalize_tokens(raw["text"])
            return cls([token_hash(t, salt) for t in toks], raw["text"]) if toks else None
        if raw.get("tokens"):
            return cls([str(h) for h in raw["tokens"]])
        return None

    def found_in(self, tokens: list[str], salt: str) -> bool:
        n = len(self.hashes)
        if not n or len(tokens) < n:
            return False
        hv = [{token_hash(v, salt) for v in variants(t)} for t in tokens]
        return any(all(self.hashes[k] in hv[i + k] for k in range(n))
                   for i in range(len(tokens) - n + 1))


# --------------------------------------------------------------------------
# gold
# --------------------------------------------------------------------------
@dataclass
class Anchor:
    role: str                       # hook | payoff | other
    t: float
    t_end: float
    phrase: Optional[Phrase] = None


@dataclass
class Moment:
    id: str
    start: float
    end: float
    label: str
    score: Optional[float] = None
    kind: str = ""
    anchors: list[Anchor] = field(default_factory=list)
    cuts: list[tuple[float, float]] = field(default_factory=list)
    topic: str = ""
    note: str = ""
    unresolved: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.01, self.end - self.start)

    def anchor(self, role: str) -> Optional[Anchor]:
        return next((a for a in self.anchors if a.role == role), None)


@dataclass
class Term:
    kind: str                       # name | number | negation | phrase
    phrases: list[Phrase]           # any accepted spelling counts as correct
    times: list[float]
    status: str = "verified"        # verified | unresolved
    note: str = ""

    def found_in(self, tokens: list[str], salt: str) -> bool:
        return any(p.found_in(tokens, salt) for p in self.phrases)


@dataclass
class Gold:
    id: str
    status: str
    coverage: str                   # full | partial
    duration: float
    language: str
    moments: list[Moment]
    negatives: list[dict[str, Any]]
    topics: list[dict[str, Any]]
    terms: list[Term]
    unresolved: list[dict[str, Any]]
    salt: str
    raw: dict[str, Any]
    match: dict[str, Any] = field(default_factory=dict)

    @property
    def ship(self) -> list[Moment]:
        return [m for m in self.moments if m.label == "ship"]


def _text_of(node: dict[str, Any]) -> Any:
    """The words of an anchor or term: plain text (local file) or token hashes (committed file)."""
    if node.get("phrase") or node.get("text"):
        return node.get("phrase") or node.get("text")
    return {"tokens": node["tokens"]} if node.get("tokens") else None


def load_gold(path: Path) -> Gold:
    data = json.loads(Path(path).read_text("utf-8"))
    return parse_gold(data)


def parse_gold(data: dict[str, Any]) -> Gold:
    if data.get("schema") != SCHEMA:
        raise ValueError(f"not a {SCHEMA} file")
    gid = str(data["id"])
    salt = str(data.get("salt") or gid)
    src = data.get("source") or {}
    moments = []
    for m in data.get("moments") or []:
        label = str(m.get("label", "no"))
        if label not in LABELS:
            raise ValueError(f"{m.get('id')}: unknown label {label}")
        anchors = []
        for a in m.get("anchors") or []:
            t = float(a["t"])
            words = _text_of(a)
            anchors.append(Anchor(str(a.get("role", "other")), t, float(a.get("t_end", t)),
                                  Phrase.parse(words, salt) if words else None))
        moments.append(Moment(
            id=str(m["id"]), start=float(m["start"]), end=float(m["end"]), label=label,
            score=m.get("score"), kind=str(m.get("kind", "")), anchors=anchors,
            cuts=[(float(a), float(b)) for a, b in m.get("cuts") or []],
            topic=str(m.get("topic", "")), note=str(m.get("note", "")),
            unresolved=[str(u) for u in m.get("unresolved") or []]))
    terms = []
    for t in data.get("terms") or []:
        phs = [Phrase.parse(x, salt) for x in [_text_of(t)] + list(t.get("accept") or []) if x]
        phs = [p for p in phs if p is not None]
        if not phs:
            continue
        times = t.get("t")
        times = [float(x) for x in (times if isinstance(times, list) else [times])] if times is not None else []
        terms.append(Term(str(t.get("kind", "phrase")), phs, times, str(t.get("status", "verified")),
                          str(t.get("note", ""))))
    return Gold(id=gid, status=str(data.get("status", "engineering_reference")),
                coverage=str(data.get("coverage", "full")), duration=float(src.get("duration") or 0.0),
                language=str(src.get("language") or ""), moments=moments,
                negatives=list(data.get("negatives") or []), topics=list(data.get("topics") or []),
                terms=terms, unresolved=list(data.get("unresolved") or []), salt=salt, raw=data,
                match=dict(src.get("match") or {}))


def redact(data: dict[str, Any]) -> dict[str, Any]:
    """A copy of a local gold file with every text replaced by salted token hashes."""
    out = json.loads(json.dumps(data, ensure_ascii=False))
    salt = str(out.get("salt") or out["id"])

    def red(node: Any) -> Any:
        if isinstance(node, dict):
            res = {}
            for k, v in node.items():
                if k in ("text", "phrase") and isinstance(v, str):
                    res["tokens"] = [token_hash(t, salt) for t in normalize_tokens(v)]
                elif k in ("alternatives", "accept") and isinstance(v, list):
                    res[k] = [{"tokens": [token_hash(t, salt) for t in normalize_tokens(x)]}
                              if isinstance(x, str) else red(x) for x in v]
                elif k == "local_only":
                    continue
                else:
                    res[k] = red(v)
            return res
        if isinstance(node, list):
            return [red(x) for x in node]
        return node

    out = red(out)
    out["redacted"] = True
    return out


def find_gold(media: Path, duration: float, folders: Iterable[Path]) -> Optional[Path]:
    """The gold file of a source: by file name and duration (±3 s); local files first."""
    name = media.name.lower()
    for folder in folders:
        if not folder.is_dir():
            continue
        for p in sorted(folder.glob("*.gold.json")):
            try:
                g = json.loads(p.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            if g.get("status") == "draft_unreviewed":
                continue                      # a draft is never used for scoring until a person reviews it
            m = (g.get("source") or {}).get("match") or {}
            names = [str(x).lower() for x in m.get("name_contains") or []]
            dur = float((g.get("source") or {}).get("duration") or 0.0)
            if names and any(x in name for x in names) and (not duration or not dur
                                                            or abs(dur - duration) <= 3.0):
                return p
    return None


# --------------------------------------------------------------------------
# a run, as the evaluation sees it
# --------------------------------------------------------------------------
@dataclass
class Clip:
    start: float
    end: float
    text: str = ""                  # the final subtitle text of the rendered clip
    hook: str = ""                  # on-screen editorial hook ("" = none)
    title: str = ""
    kind: str = "short"
    segments: list[tuple[float, float]] = field(default_factory=list)

    @property
    def spans(self) -> list[tuple[float, float]]:
        return self.segments or [(self.start, self.end)]

    @property
    def duration(self) -> float:
        return sum(max(0.0, b - a) for a, b in self.spans)

    def covers(self, t: float, slack: float = 0.0) -> bool:
        return any(a - slack <= t <= b + slack for a, b in self.spans)

    def overlap(self, a: float, b: float) -> float:
        return sum(max(0.0, min(b, y) - max(a, x)) for x, y in self.spans)


@dataclass
class Run:
    label: str
    clips: list[Clip]
    pool: list[tuple[float, float]] = field(default_factory=list)
    pool_known: bool = False
    mode: Optional[str] = None      # semantic | degraded | None = not declared
    mode_reason: str = ""
    clips_labelled: bool = False    # every clip review carries the mode
    strong_seconds: Optional[float] = None
    semantic_seconds: Optional[float] = None
    source_seconds: float = 0.0
    longs: list[Clip] = field(default_factory=list)


# --------------------------------------------------------------------------
# matching
# --------------------------------------------------------------------------
def _iou(c: Clip, m: Moment) -> float:
    inter = c.overlap(m.start, m.end)
    return inter / max(0.01, c.duration + m.duration - inter)


def matches(c: Clip, m: Moment) -> bool:
    inside = c.overlap(m.start, m.end) / max(0.01, c.duration)
    if inside < MIN_INSIDE:
        return False
    key = [a for a in m.anchors if a.role in ("hook", "payoff")]
    if key:
        return all(c.covers(a.t, ANCHOR_SLACK) and c.covers(a.t_end, ANCHOR_SLACK) for a in key)
    return c.overlap(m.start, m.end) / m.duration >= MIN_COVER


def pool_has(pool: list[tuple[float, float]], m: Moment) -> bool:
    pay = m.anchor("payoff")
    for a, b in pool:
        inter = max(0.0, min(b, m.end) - max(a, m.start))
        if inter / m.duration < POOL_COVER:
            continue
        if pay is None or (a - ANCHOR_SLACK <= pay.t and b + ANCHOR_SLACK >= pay.t_end):
            return True
    return False


def best_match(c: Clip, moments: list[Moment]) -> Optional[Moment]:
    hits = [m for m in moments if matches(c, m)]
    return max(hits, key=lambda m: (_iou(c, m), CREDIT[m.label])) if hits else None


# --------------------------------------------------------------------------
# grounding of on-screen hooks (independent of the engine's own check)
# --------------------------------------------------------------------------
_STOP = set("""את של על עם זה זו זאת הוא היא הם הן אני אתה את אנחנו לא כן מה מי איך למה כי אם גם רק עוד כל
יש אין היה היו הייתה להיות אבל או אז כבר פה שם כמו לי לך לו לה לנו להם ממש די הרבה יותר פחות אחד אחת
the a an of to in on at for with and or but is are was were be been it this that these those he she they we you i
not no yes what who how why if so too very just""".split())


def hook_grounding(hook: str, clip_text: str) -> dict[str, Any]:
    htoks = [t for t in normalize_tokens(hook) if t not in _STOP]
    ctoks = normalize_tokens(clip_text)
    cset = {v for t in ctoks for v in variants(t)}
    stems = {v[:4] for v in cset if len(v) >= 4}
    missing, numbers = [], []
    for t in htoks:
        vs = variants(t)
        ok = any(v in cset for v in vs) or any(len(v) >= 4 and v[:4] in stems for v in vs)
        if not ok:
            (numbers if any(ch.isdigit() for ch in t) else missing).append(t)
    return {"grounded": bool(htoks) and not numbers and not missing,
            "mostly_grounded": bool(htoks) and not numbers and len(missing) <= 1,
            "content_words": len(htoks), "ungrounded": len(missing) + len(numbers),
            "ungrounded_numbers": len(numbers)}


# --------------------------------------------------------------------------
# evaluation
# --------------------------------------------------------------------------
def evaluate(gold: Gold, run: Run) -> dict[str, Any]:
    shorts = [c for c in run.clips if c.kind == "short"]
    salt = gold.salt
    per_clip: list[dict[str, Any]] = []
    matched: dict[str, list[int]] = {}
    credit_sum = 0.0
    rated = 0
    neg_hits = 0
    for i, c in enumerate(shorts):
        m = best_match(c, gold.moments)
        neg = [n for n in gold.negatives
               if c.overlap(float(n["start"]), float(n["end"])) / max(0.01, c.duration) >= NEGATIVE_SHARE]
        rec: dict[str, Any] = {"start": round(c.start, 2), "end": round(c.end, 2),
                               "duration": round(c.duration, 1), "moment": m.id if m else None,
                               "label": m.label if m else ("negative" if neg else "unmatched")}
        if neg:
            neg_hits += 1
            rec["negative"] = [str(n.get("id") or n.get("reason")) for n in neg]
        if m is not None:
            matched.setdefault(m.id, []).append(i)
            credit_sum += CREDIT[m.label]
            rated += 1
            rec["start_error"] = round(c.start - m.start, 2)
            rec["end_error"] = round(c.end - m.end, 2)
        elif gold.coverage == "full" or neg:
            rated += 1                       # a full map rates every clip; unmatched = not shippable
        per_clip.append(rec)

    n = len(shorts)
    ship = gold.ship
    ship_hit = [m.id for m in ship if m.id in matched]
    pool = run.pool + [(c.start, c.end) for c in shorts]
    pool_hit = [m.id for m in ship if pool_has(pool, m)]

    # boundaries of matched clips (ship and maybe)
    errs = [(abs(r["start_error"]), abs(r["end_error"])) for r in per_clip if "start_error" in r]
    payoff_cut = 0
    for r, c in zip(per_clip, shorts):
        mid = r.get("moment")
        m = next((x for x in gold.moments if x.id == mid), None)
        pay = m.anchor("payoff") if m else None
        if pay is not None and not c.covers(pay.t_end, ANCHOR_SLACK):
            payoff_cut += 1

    # subtitle text: verified phrases and terms inside the shipped clips
    phrase_total = phrase_ok = 0
    term_stats: dict[str, dict[str, int]] = {}
    unresolved_inside = 0
    misses: list[dict[str, Any]] = []
    for c in shorts:
        toks = normalize_tokens(c.text)
        for m in gold.moments:
            for a in m.anchors:
                if a.phrase is None or not (c.covers(a.t) and c.covers(a.t_end)):
                    continue
                phrase_total += 1
                if a.phrase.found_in(toks, salt):
                    phrase_ok += 1
                else:
                    misses.append({"moment": m.id, "role": a.role, "t": a.t})
        for t in gold.terms:
            inside = [x for x in t.times if c.covers(x)]
            if not inside:
                continue
            if t.status != "verified":
                unresolved_inside += 1
                continue
            st = term_stats.setdefault(t.kind, {"total": 0, "correct": 0})
            st["total"] += 1
            if t.found_in(toks, salt):
                st["correct"] += 1
            else:
                misses.append({"term": t.kind, "t": inside[0]})

    hooks = [hook_grounding(c.hook, c.text) for c in shorts if c.hook]
    dup_pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            a, b = shorts[i], shorts[j]
            inter = sum(b.overlap(x, y) for x, y in a.spans)
            iou = inter / max(0.01, a.duration + b.duration - inter)
            if iou >= DUPLICATE_IOU:
                dup_pairs.append((i, j))
    for mid, idx in matched.items():
        for k in range(1, len(idx)):
            if (idx[0], idx[k]) not in dup_pairs:
                dup_pairs.append((idx[0], idx[k]))

    src = run.source_seconds or gold.duration
    longs = _longform(gold, run.longs)
    return {
        "gold": gold.id, "gold_status": gold.status, "coverage_of_gold": gold.coverage,
        "clips": n,
        "precision": (round(credit_sum / rated, 3) if rated else None),
        "precision_basis": f"{rated} rated clip(s)" + ("" if gold.coverage == "full"
                                                        else f", {n - rated} unrated"),
        "recall_pool": round(len(pool_hit) / len(ship), 3) if ship else None,
        "recall_pool_known": run.pool_known,
        "recall_ship": round(len(ship_hit) / len(ship), 3) if ship else None,
        "ship_found": ship_hit, "ship_missed": [m.id for m in ship if m.id not in matched],
        "maybe_found": [m.id for m in gold.moments if m.label == "maybe" and m.id in matched],
        "negative_hits": neg_hits,
        "boundary": {"matched": len(errs),
                     "mean_start_error": round(sum(e[0] for e in errs) / len(errs), 2) if errs else None,
                     "mean_end_error": round(sum(e[1] for e in errs) / len(errs), 2) if errs else None,
                     "payoff_cut": payoff_cut},
        "transcript": {"phrases": phrase_total, "phrases_correct": phrase_ok,
                       "phrase_accuracy": round(phrase_ok / phrase_total, 3) if phrase_total else None,
                       "misses": misses[:20]},
        "names_numbers": {k: {**v, "accuracy": round(v["correct"] / v["total"], 3) if v["total"] else None}
                          for k, v in term_stats.items()},
        "unresolved_in_clips": unresolved_inside,
        "hook_grounding": {"hooks": len(hooks), "grounded": sum(1 for h in hooks if h["grounded"]),
                           "mostly_grounded": sum(1 for h in hooks if h["mostly_grounded"]),
                           "rate": round(sum(1 for h in hooks if h["grounded"]) / len(hooks), 3) if hooks else None},
        "duplicates": {"pairs": len(dup_pairs), "rate": round(len(dup_pairs) / n, 3) if n else 0.0},
        "source_coverage": {
            "strong_asr": round(run.strong_seconds / src, 3) if (run.strong_seconds is not None and src) else None,
            "semantic": round(run.semantic_seconds / src, 3) if (run.semantic_seconds is not None and src) else None},
        "mode": {"declared": run.mode or "not declared", "reason": run.mode_reason,
                 "visible": bool(run.mode) and (run.mode != "degraded" or run.clips_labelled or n == 0)},
        "longform": longs,
        "per_clip": per_clip,
    }


def _longform(gold: Gold, longs: list[Clip]) -> Optional[dict[str, Any]]:
    if not gold.topics:
        return None
    found = []
    for t in gold.topics:
        a, b = float(t["start"]), float(t["end"])
        for c in longs:
            inter = c.overlap(a, b)
            cover = inter / max(0.01, b - a)
            spill = (c.duration - inter) / max(0.01, c.duration)
            if cover >= 0.6 and spill <= 0.4:
                found.append(str(t["id"]))
                break
    return {"topics": len(gold.topics), "videos": len(longs), "topics_found": found,
            "recall": round(len(found) / len(gold.topics), 3)}


# --------------------------------------------------------------------------
# report lines
# --------------------------------------------------------------------------
ROWS = [
    ("clips", "Shorts produced"),
    ("precision", "Precision (ship = 1, maybe = ½)"),
    ("recall_ship", "Recall of ship moments (shipped)"),
    ("recall_pool", "Recall of ship moments (candidate pool)"),
    ("negative_hits", "Clips on a must-not-choose region"),
    ("boundary.mean_start_error", "Mean start error vs gold (s)"),
    ("boundary.mean_end_error", "Mean end error vs gold (s)"),
    ("boundary.payoff_cut", "Clips that cut the payoff"),
    ("transcript.phrase_accuracy", "Key phrases correct in subtitles"),
    ("names_numbers.name.accuracy", "Names correct"),
    ("names_numbers.number.accuracy", "Numbers correct"),
    ("hook_grounding.rate", "On-screen hooks fully grounded in the subtitles"),
    ("duplicates.rate", "Duplicate rate"),
    ("source_coverage.strong_asr", "Source heard by the strong model"),
    ("source_coverage.semantic", "Source read by the semantic layer"),
    ("mode.declared", "Intelligence mode"),
    ("longform.recall", "Gold topics with a long-form video"),
]


def _get(d: dict[str, Any], dotted: str) -> Any:
    cur: Any = d
    for k in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def markdown(results: dict[str, dict[str, Any]]) -> list[str]:
    labs = list(results)
    if not labs:
        return []
    g = next(iter(results.values()))
    lines = [f"## Gold evaluation – `{g['gold']}` ({g['gold_status']}, {g['coverage_of_gold']} map)", "",
             "| Metric | " + " | ".join(x.upper() for x in labs) + " |", "|---|" + "---|" * len(labs)]
    for key, name in ROWS:
        vals = [_get(results[lab], key) for lab in labs]
        if all(v is None for v in vals):
            continue
        lines.append(f"| {name} | " + " | ".join("–" if v is None else str(v) for v in vals) + " |")
    for lab in labs:
        r = results[lab]
        lines.append("")
        lines.append(f"- {lab.upper()}: found {', '.join(r['ship_found']) or 'none'}; "
                     f"missed {', '.join(r['ship_missed']) or 'none'}; precision basis {r['precision_basis']}"
                     + ("" if r["recall_pool_known"] else "; candidate pool not recorded (pool = shipped clips)")
                     + (f"; {r['unresolved_in_clips']} unresolved word(s) inside clips, not scored"
                        if r["unresolved_in_clips"] else ""))
    return lines
