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
import time
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
RETRY_PAUSE = 3.0
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
    repaired: bool = False
    rejection: dict[str, Any] = field(default_factory=dict)     # classify(): why it did not ship
    overlap_idx: list[int] = field(default_factory=list)   # sentences marked crosstalk / unintelligible
    packaging: dict[str, Any] = field(default_factory=dict)  # titles + caption from the shipping verdict


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


EDITOR_ATTEMPTS = 2     # an infrastructure failure is retried once before the clip is "not evaluated"
CONSTRUCTION = ("opening_hooks", "standalone", "payoff", "clean_ending", "pacing")
CATEGORY = {"opening_hooks": "weak_hook", "standalone": "missing_context", "payoff": "missing_payoff",
            "clean_ending": "bad_ending", "pacing": "pacing"}


def _ask(provider: SemanticProvider, plan: Plan, sentences: Sequence[Sentence], starts: list[int],
         ends: list[int], *, escalate: bool = False) -> tuple[Optional[dict[str, Any]], Optional[Exception]]:
    system, user = prompts.editor_prompt(_product(plan, sentences, starts, ends))
    err: Optional[Exception] = None
    for attempt in range(EDITOR_ATTEMPTS):
        try:
            # a rebuilt cut is judged by the senior editor (escalation): the decision that turns a
            # strong-but-badly-cut moment into a delivered Short is the one worth the best model
            return provider.complete_json("editor", system, user, prompts.EDITOR_SCHEMA, max_tokens=8000,
                                          **({"tier": "premium"} if escalate else {})), None
        except SemanticError as exc:
            err = exc
            log.warning("editor attempt %d failed for %s: %s", attempt + 1, plan.cand.key, exc)
            if attempt + 1 < EDITOR_ATTEMPTS:
                time.sleep(RETRY_PAUSE)
    return None, err


def _interest(scores: dict[str, Any]) -> Optional[int]:
    v = (scores or {}).get("interest")
    try:
        return int(v.get("score") if isinstance(v, dict) else v)
    except (TypeError, ValueError):
        return None


def review(plan: Plan, sentences: Sequence[Sentence], provider: Optional[SemanticProvider], *,
           lo: int, hi: int,
           retranscribe: Callable[[list[list[float]], Optional[list[list[float]]]], dict[str, Any]],
           rebuild_hook: Callable[[Plan], dict[str, Any]],
           reconstruct: Optional[Callable[[Plan, str], Optional[dict[str, Any]]]] = None,
           wide: Optional[Callable[[], tuple[list[int], list[int]]]] = None) -> Plan:
    """
    The final editor: judge → (one targeted repair of a strong moment) → judge again → ship or reject.

      * ship needs the model's "ship" AND every story check;
      * a moment worth keeping (interest > 0) whose construction failed gets ONE repair: an
        intelligent RECONSTRUCTION of the cut over a much wider window, guided by the editor's
        critique (reconstruct: move the start, bring in context, extend to the payoff, drop
        setup or a dead ending, cut dead stretches); without it, the model's own fix (moved to
        the nearest allowed boundary) or a fix derived from the failed checks;
      * a weak moment (interest 0) is rejected without a repair;
      * the editor being unreachable is NOT a rejection: verdict "unreviewed" (retried later);
      * every rejection is classified (plan.rejection) – the reason a clip did not ship.
    """
    v = Validator(sentences)
    rebuilt = False
    for rnd in range(MAX_ROUNDS + 1):
        starts, ends = (wide() if (rebuilt and wide is not None) else options(plan.cand, sentences, lo, hi))
        problems = deterministic_problems(plan, sentences)
        if provider is None:
            data, err = {"verdict": "ship", "checks": {}, "fixes": [], "reason": "", "scores": {}}, None
        else:
            data, err = _ask(provider, plan, sentences, starts, ends, escalate=rebuilt)
        if err is not None or data is None:
            plan.history.append({"round": rnd, "verdict": "unreviewed", "reason": f"editor unavailable: {err}"})
            plan.verdict, plan.reason = "unreviewed", f"editor unavailable: {err}"
            plan.rejection = {"category": "editor_unavailable", "failed_checks": [], "repaired": plan.repaired,
                              "near_pass": False}
            return plan
        verdict = str(data.get("verdict") or "reject")
        fixes = list(data.get("fixes") or [])
        reason = str(data.get("reason") or "")
        scores = data.get("scores") or {}
        checks = {k: bool(x) for k, x in (data.get("checks") or {}).items()}
        failed = [k for k in prompts.CHECKS if checks and not checks.get(k, False)]
        if verdict == "ship" and failed:
            verdict = "repair"                      # the model's own checks overrule a lenient verdict
            reason = (reason + " | " if reason else "") + "checks failed: " + ",".join(failed)
        if "duration_outside_platform_limits" in problems:
            verdict, reason = "reject", "duration outside platform limits"
        plan.history.append({"round": rnd, "verdict": verdict, "problems": problems, "fixes": fixes,
                             "reason": reason, "scores": scores, "checks": checks, "failed": failed})
        plan.verdict, plan.reason, plan.scores = verdict, reason, scores
        if verdict == "ship":
            plan.packaging = dict(data.get("packaging") or {})
        if verdict == "ship" or rnd == MAX_ROUNDS:
            break
        interest = _interest(scores)
        if interest == 0 or "duration_outside_platform_limits" in problems:
            break                                   # a weak moment is not worth a repair
        if verdict == "reject" and not failed and not fixes:
            break                                   # rejected for what it is, not how it is cut
        changed = False
        if reconstruct is not None and (failed or verdict == "repair"):
            critique = (f"verdict {verdict}; failed checks: {', '.join(failed) or 'none'}; reason: {reason}; "
                        "suggested fixes: " + ("; ".join(f"{f.get('kind')} {' '.join(f.get('sentence_ids') or [])} "
                                                         f"{f.get('note') or ''}".strip() for f in fixes) or "none"))
            choice = reconstruct(plan, critique)
            if choice is not None and (choice["start_idx"], choice["end_idx"], choice.get("cut_idx")) != (
                    plan.choice["start_idx"], plan.choice["end_idx"], plan.choice.get("cut_idx")):
                final = retranscribe(choice["spans"], None)
                old_text = plain_text(plan.final)
                plan.choice, plan.final = choice, final
                if plain_text(final) != old_text:
                    plan.hook = rebuild_hook(plan)
                plan.history[-1]["reconstructed"] = {"start": sentences[choice["start_idx"]].id,
                                                     "end": sentences[choice["end_idx"]].id,
                                                     "cuts": [sentences[i].id for i in choice.get("cut_idx") or []],
                                                     "duration": choice["duration"]}
                changed = rebuilt = True
        if changed:
            plan.repaired = True
            continue
        mapped = _map_fixes(fixes, v, starts, ends)
        changed = _apply(plan, mapped, sentences, v, starts, ends, retranscribe, rebuild_hook)
        if not changed:
            changed = _apply(plan, _derived_fixes(plan, failed, sentences, starts, ends), sentences, v,
                             starts, ends, retranscribe, rebuild_hook)
        if not changed:
            plan.reason += " | no repair was possible"
            break
        plan.repaired = True
    if plan.verdict != "ship":
        plan.verdict = "reject"
        plan.rejection = classify(plan)
        if plan.rejection["near_pass"]:
            plan.verdict = "near_pass"              # rejected, but the closest call: shown for attention only
    return plan


def classify(plan: Plan) -> dict[str, Any]:
    """Why a clip did not ship: one category + the failed checks (for the report and the user)."""
    last = plan.history[-1] if plan.history else {}
    failed = list(last.get("failed") or [])
    problems = list(last.get("problems") or [])
    interest = _interest(last.get("scores") or {})
    if last.get("verdict") == "unreviewed":
        cat = "editor_unavailable"
    elif "duration_outside_platform_limits" in problems:
        cat = "boundary"
    elif any(p.startswith("critical_unresolved") or p == "loop" for p in problems):
        cat = "subtitle_uncertainty"
    elif interest == 0:
        cat = "weak_moment"
    elif failed:
        cat = CATEGORY.get(failed[0], "other")
    else:
        cat = "editor_rejected"
    if plan.repaired and cat not in ("editor_unavailable", "weak_moment"):
        cat = "repair_failed:" + cat
    base = cat.split(":")[-1]
    kind = ("infrastructure" if base == "editor_unavailable" else "content" if base in ("weak_moment", "editor_rejected")
            else "transcript" if base == "subtitle_uncertainty" else "construction")
    return {"category": cat, "kind": kind, "failed_checks": failed, "repaired": plan.repaired,
            "reconstructed": any(h.get("reconstructed") for h in plan.history),
            # good content, construction still imperfect after the one repair: delivered as NEEDS
            # REVIEW (never "ready"), so a strong moment is never silently lost to its cut
            "near_pass": bool((interest or 0) >= 1 and not problems and 0 < len(failed) <= 2
                              and kind == "construction")}


def _map_fixes(fixes: list[dict[str, Any]], v: Validator, starts: list[int], ends: list[int]) -> list[dict[str, Any]]:
    """A boundary the model asked for outside the allowed options moves to the nearest allowed one."""
    out = []
    for f in fixes:
        kind = f.get("kind")
        ids = [i for i in (v.index(x) for x in f.get("sentence_ids") or []) if i is not None]
        if kind in ("better_start", "better_end") and ids:
            pool = starts if kind == "better_start" else ends
            if pool and ids[0] not in pool:
                near = min(pool, key=lambda i: abs(i - ids[0]))
                f = {**f, "sentence_ids": [v.sentences[near].id], "note": (f.get("note") or "") + " (nearest allowed)"}
        out.append(f)
    return out


def _derived_fixes(plan: Plan, failed: list[str], sentences: Sequence[Sentence], starts: list[int],
                   ends: list[int]) -> list[dict[str, Any]]:
    c = plan.choice
    fixes: list[dict[str, Any]] = []
    if "payoff" in failed or "clean_ending" in failed:
        later = [j for j in ends if j > c["end_idx"]]
        if later:
            fixes.append({"kind": "better_end", "sentence_ids": [sentences[min(later)].id], "note": "derived: extend"})
    if "standalone" in failed:
        earlier = [i for i in starts if i < c["start_idx"]]
        if earlier:
            fixes.append({"kind": "better_start", "sentence_ids": [sentences[max(earlier)].id],
                          "note": "derived: one sentence of context"})
    elif "opening_hooks" in failed:
        later = [i for i in starts if i > c["start_idx"]]
        if later:
            fixes.append({"kind": "better_start", "sentence_ids": [sentences[min(later)].id],
                          "note": "derived: start on the point"})
    if "pacing" in failed:
        fixes.append({"kind": "tighten", "sentence_ids": [], "note": "derived: cut dead air"})
    return fixes


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
        elif kind == "tighten" and not c.get("tighten"):
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
    return bool(changed or (focus and (text_changed or heard_new)))


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
