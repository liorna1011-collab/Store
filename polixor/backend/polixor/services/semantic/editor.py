"""
The final editor: judge the complete product, repair it, reject only after repair fails.

For each finished Short the editor sees what a viewer would get – the cut
(times, internal cuts), the final subtitles with unresolved words marked,
what the viewer hears first, the title and pacing numbers – and answers ship /
repair / reject, with explicit story checks (opening, standalone, payoff,
ending, pacing). "ship" requires every check: a failed check turns it into a
repair (when a fix is named) or a reject. Repairs it can ask for:

  better_start / better_end   another sentence from the allowed options
  new_hook                    regenerate and re-rank the hooks
  rehear                      check named sentences against the audio again
  tighten                     cut dead air inside the clip

Deterministic checks run whatever the model says: a critical word (number,
negation, name, mixed script) left unresolved inside the evidence is re-heard
first and rejects the clip only if it is still unresolved; a hallucination
loop in the final words is re-heard; a clip outside the platform limits is
rejected. ONE repair round, then the clip is judged again: it ships only if
that verdict is "ship" – a clip still needing repair is rejected, never shipped
as is (no quota fills the package).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from . import prompts
from .boundaries import MAX_HARD, MIN_HARD, options, spans_of
from .candidates import Cand
from .provider import SemanticError, SemanticProvider
from .sentences import Sentence
from .validate import Validator

log = logging.getLogger("polixor.semantic.editor")

MAX_ROUNDS = 1          # one deliberate repair, then the verdict is final
DEAD_AIR = 0.9          # a silence this long inside a clip is cut when tightening


@dataclass
class Plan:
    cand: Cand
    choice: dict[str, Any]              # start_idx, end_idx, cut_idx, spans, duration
    final: dict[str, Any]               # asr_ensemble clip record
    hook: dict[str, Any]
    history: list[dict[str, Any]] = field(default_factory=list)
    verdict: str = ""
    reason: str = ""
    scores: dict[str, Any] = field(default_factory=dict)
    overlap_idx: list[int] = field(default_factory=list)   # sentences marked crosstalk / unintelligible


def subtitle_text(final: dict[str, Any]) -> str:
    return " ".join((f"⟦?{w['text']}⟧" if w.get("status") == "unresolved" else w["text"])
                    for w in final.get("words") or [])


def plain_text(final: dict[str, Any]) -> str:
    return " ".join(w["text"] for w in final.get("words") or [])


def pacing(final: dict[str, Any], spans: Sequence[Sequence[float]]) -> dict[str, float]:
    ws = final.get("words") or []
    dur = sum(b - a for a, b in spans) or 1.0
    gaps = [ws[i + 1]["start"] - ws[i]["end"] for i in range(len(ws) - 1)]
    long_gaps = [g for g in gaps if g >= DEAD_AIR]
    return {"duration": round(dur, 1), "words_per_second": round(len(ws) / dur, 2),
            "longest_pause": round(max(gaps, default=0.0), 2), "dead_air_seconds": round(sum(long_gaps), 1)}


def deterministic_problems(plan: Plan, sentences: Sequence[Sentence]) -> list[str]:
    out = []
    d = plan.choice.get("duration", 0.0)
    if d < MIN_HARD or d > MAX_HARD:
        out.append("duration_outside_platform_limits")
    ev = [sentences[k] for e in plan.cand.evidence for k in range(e["idx"], e.get("idx_end", e["idx"]) + 1)]
    for w in plan.final.get("words") or []:
        if w.get("status") == "unresolved" and w.get("critical") and any(
                s.start - 0.3 <= w["start"] <= s.end + 0.3 for s in ev):
            out.append(f"critical_unresolved:{w.get('critical')}")
            break
    if plan.final.get("loops"):
        out.append("loop")
    return out


def _product(plan: Plan, sentences: Sequence[Sentence], starts: list[int], ends: list[int]) -> str:
    c = plan.choice
    sp = c["spans"]
    cuts = ", ".join(f"{sentences[i].id}" for i in c.get("cut_idx") or []) or "none"
    pc = pacing(plan.final, sp)
    return (f"CUT: {sentences[c['start_idx']].id} → {sentences[c['end_idx']].id}, spans "
            + ", ".join(f"{a:.1f}-{b:.1f}" for a, b in sp) + f" ({c['duration']:.1f} s); internal cuts: {cuts}\n"
            f"TYPE: {plan.cand.type}; evidence: "
            + "; ".join(f"{e['role']} {e['id']} “{e['quote']}”" for e in plan.cand.evidence) + "\n"
            f"FINAL SUBTITLES: {subtitle_text(plan.final)}\n"
            f"WHAT THE VIEWER HEARS FIRST: {' '.join(plain_text(plan.final).split()[:14])}…\n"
            f"TITLE (post text only, not on the video): {plan.hook.get('title') or '(none)'}\n"
            f"PACING: {pc}\n"
            + (("CROSSTALK / UNINTELLIGIBLE inside the cut (cut them or reject if they carry the point): "
                + ", ".join(sentences[i].id for i in plan.overlap_idx
                            if c["start_idx"] <= i <= c["end_idx"] and i not in (c.get("cut_idx") or [])) + "\n")
               if any(c["start_idx"] <= i <= c["end_idx"] and i not in (c.get("cut_idx") or [])
                      for i in plan.overlap_idx) else "")
            + 
            "ALLOWED STARTS: " + ", ".join(f"{sentences[i].id} ({sentences[i].text[:60]})" for i in starts) + "\n"
            "ALLOWED ENDS: " + ", ".join(f"{sentences[j].id} ({sentences[j].text[:60]})" for j in ends))


def review(plan: Plan, sentences: Sequence[Sentence], provider: Optional[SemanticProvider], *,
           lo: int, hi: int,
           retranscribe: Callable[[list[list[float]], Optional[list[list[float]]]], dict[str, Any]],
           rebuild_hook: Callable[[Plan], dict[str, Any]]) -> Plan:
    """
    Runs the editor with repairs. `retranscribe(spans, focus)` returns the final
    transcript record for the clip (focus: windows to re-hear harder);
    `rebuild_hook(plan)` returns a new hook record.
    """
    v = Validator(sentences)
    for rnd in range(MAX_ROUNDS + 1):
        starts, ends = options(plan.cand, sentences, lo, hi)
        problems = deterministic_problems(plan, sentences)
        verdict, fixes, reason, scores, checks = "ship", [], "", {}, {}
        if provider is not None:
            system, user = prompts.editor_prompt(_product(plan, sentences, starts, ends))
            try:
                data = provider.complete_json("editor", system, user, prompts.EDITOR_SCHEMA, max_tokens=8000)
                verdict = str(data.get("verdict") or "reject")
                fixes = list(data.get("fixes") or [])
                reason = str(data.get("reason") or "")
                scores = data.get("scores") or {}
                checks = {k: bool(v) for k, v in (data.get("checks") or {}).items()}
            except SemanticError as exc:
                # no final judgment = not publish-ready (never shipped unjudged)
                log.warning("editor failed for %s: %s", plan.cand.key, exc)
                verdict, reason = "reject", f"editor unavailable: {exc}"
        failed = [k for k in prompts.CHECKS if checks and not checks.get(k, False)]
        if verdict == "ship" and failed:
            # the model's own checks overrule a lenient verdict
            verdict = "repair" if fixes else "reject"
            reason = (reason + " | " if reason else "") + "checks failed: " + ",".join(failed)
        # deterministic problems become repairs first
        if any(p.startswith("critical_unresolved") or p == "loop" for p in problems):
            ids = [e["id"] for e in plan.cand.evidence]
            fixes = [{"kind": "rehear", "sentence_ids": ids, "note": "deterministic: " + ",".join(problems)}] + fixes
            verdict = "repair" if verdict == "ship" else verdict
        if "duration_outside_platform_limits" in problems:
            verdict, reason = "reject", "duration outside platform limits"
        plan.history.append({"round": rnd, "verdict": verdict, "problems": problems, "fixes": fixes,
                             "reason": reason, "scores": scores, "checks": checks})
        plan.verdict, plan.reason, plan.scores = verdict, reason, scores
        if verdict == "ship" or (verdict == "reject" and not fixes) or rnd == MAX_ROUNDS:
            break
        if verdict == "reject" and fixes and rnd > 0:
            break
        changed = _apply(plan, fixes, sentences, v, starts, ends, retranscribe, rebuild_hook)
        if not changed:
            # nothing could be repaired: a clip that needs repair is not publish-ready
            plan.verdict = "reject"
            remaining = deterministic_problems(plan, sentences)
            plan.reason = reason + " | the requested repair was not possible" + (
                ": " + ",".join(remaining) if remaining else "")
            break
    if plan.verdict == "repair":
        # still not right after its one repair: rejected (never shipped as is)
        plan.verdict = "reject"
        remaining = deterministic_problems(plan, sentences)
        plan.reason += " | still needs repair after one round" + (": " + ",".join(remaining) if remaining else "")
    return plan


def _apply(plan: Plan, fixes: list[dict[str, Any]], sentences: Sequence[Sentence], v: Validator,
           starts: list[int], ends: list[int], retranscribe, rebuild_hook) -> bool:
    changed = False
    c = dict(plan.choice)
    focus: list[list[float]] = []
    new_hook = False
    for f in fixes:
        kind = f.get("kind")
        ids = [v.index(x) for x in f.get("sentence_ids") or []]
        ids = [i for i in ids if i is not None]
        if kind == "better_start" and ids and ids[0] in starts and ids[0] != c["start_idx"]:
            c["start_idx"] = ids[0]
            changed = True
        elif kind == "better_end" and ids and ids[0] in ends and ids[0] != c["end_idx"]:
            c["end_idx"] = ids[0]
            changed = True
        elif kind == "rehear" and ids:
            focus += [[sentences[i].start, sentences[i].end] for i in ids]
        elif kind == "new_hook":
            new_hook = True
        elif kind == "tighten":
            c["tighten"] = True
            ev = {k for e in plan.cand.evidence for k in range(e["idx"], e.get("idx_end", e["idx"]) + 1)}
            extra = [i for i in ids if c["start_idx"] < i < c["end_idx"] and i not in ev]
            c["cut_idx"] = sorted(set(c.get("cut_idx") or []) | set(extra))
            changed = True
    text_changed = heard_new = False
    if changed or focus:
        c["cut_idx"] = [k for k in c.get("cut_idx") or [] if c["start_idx"] < k < c["end_idx"]]
        spans = [list(x) for x in spans_of(sentences, c["start_idx"], c["end_idx"], c["cut_idx"])]
        final = retranscribe(spans, focus or None)
        if c.get("tighten"):
            spans = tighten(spans, final)
        c["spans"] = spans
        c["duration"] = round(sum(b - a for a, b in spans), 2)
        text_changed = plain_text(final) != plain_text(plan.final)
        heard_new = [w.get("status") for w in final.get("words") or []] != \
            [w.get("status") for w in plan.final.get("words") or []]
        plan.final = final
        plan.choice = c
    if new_hook or text_changed:
        plan.hook = rebuild_hook(plan)
    # a re-hearing that resolved nothing is not progress: the editor stops asking for it
    return bool(changed or new_hook or (focus and (text_changed or heard_new)))


def tighten(spans: list[list[float]], final: dict[str, Any]) -> list[list[float]]:
    """Cuts silences longer than DEAD_AIR between words (keeping 0.25 s of air on each side)."""
    ws = final.get("words") or []
    out: list[list[float]] = []
    for a, b in spans:
        inside = [w for w in ws if a - 0.3 <= w["start"] <= b + 0.3]
        cur = [a, b]
        for x, y in zip(inside, inside[1:]):
            if y["start"] - x["end"] >= DEAD_AIR:
                out.append([cur[0], round(x["end"] + 0.25, 3)])
                cur = [round(y["start"] - 0.25, 3), b]
        out.append(cur)
    return [s for s in out if s[1] - s[0] > 0.3]
