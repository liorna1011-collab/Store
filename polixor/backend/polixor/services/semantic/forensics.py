"""
Why did a run's candidates not become Shorts? The EARLIEST real failure of every candidate.

Reads only what a run already recorded (intel_report.json: the pool, the ranking decisions,
the editor's verdicts and checks, provider failures) – nothing is re-run, no model is called.
Works for reports written before RC1 too (their ranking decisions carry the scores, including
the share of "no" verdicts).

Per candidate, one category from:

  weak_moment          the judges or the editor found the MOMENT weak (majority "no", a very
                       low score, editor interest 0 / rejected for what it is)
  bad_boundaries       strong moment, the cut failed (ending, duration)
  missing_context      strong moment, a stranger could not follow it
  missing_payoff       strong moment, the cut stopped before its point
  weak_opening         strong moment, the first seconds do not hook
  pacing               strong moment, dead stretches inside
  repair_failed        a construction failure that the one repair did not fix (with the reason)
  editor_disagreement  the ranking judges said "ship" but the editor rejected the content
  model_failure        the editor could not be reached (provider / network): never judged
  transcript_problem   a critical word could not be confirmed in the final subtitles
  duplicate            the same moment as a candidate already chosen
  strict_gate          a strong-enough moment dropped by a ranking rule before any editor saw it
                       (pre-RC1: "maybe" verdicts counted as rejection; an absolute bar on a
                       rank-relative score; topic spread used as a veto)
  not_reached          ranked, eligible, but the editing budget was spent on others
  shipped              became a Short

and an aggregate: genuinely weak / construction failures / model & infrastructure / overly
strict gate / duplicates / transcript / shipped / not reached.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

BUCKET = {
    "weak_moment": "genuinely_weak", "editor_disagreement": "genuinely_weak",
    "bad_boundaries": "construction", "missing_context": "construction", "missing_payoff": "construction",
    "weak_opening": "construction", "pacing": "construction", "repair_failed": "construction",
    "model_failure": "infrastructure", "transcript_problem": "transcript", "duplicate": "duplicate",
    "strict_gate": "overly_strict_gate", "not_reached": "not_reached", "shipped": "shipped",
}
EDITOR = {"weak_hook": "weak_opening", "missing_context": "missing_context", "missing_payoff": "missing_payoff",
          "bad_ending": "bad_boundaries", "boundary": "bad_boundaries", "pacing": "pacing",
          "subtitle_uncertainty": "transcript_problem", "editor_unavailable": "model_failure",
          "weak_moment": "weak_moment", "editor_rejected": "weak_moment", "other": "weak_moment"}
OLD_FLOOR = 0.25


def _ranking_category(d: dict[str, Any]) -> tuple[str, str]:
    why = str(d.get("decision") or "")
    sc = d.get("scores") or {}
    no = float(sc.get("no_share") or 0.0)
    final = float(sc.get("final") or 0.0)
    if why.startswith("duplicate_of:"):
        return "duplicate", f"same moment as {why.split(':', 1)[1]}"
    if why == "judges_rejected":
        if no > 0.5:
            return "weak_moment", f"{round(no * 100)}% of the judges said no"
        return "strict_gate", (f"only {round(no * 100)}% said no – dropped because too few said 'ship' "
                               "(\"maybe\" counted as rejection) before the editor saw it")
    if why == "below_bar":
        if final < OLD_FLOOR:
            return "weak_moment", f"score {final:.2f}"
        return "strict_gate", f"score {final:.2f} under the old absolute bar 0.55 (rank-relative score)"
    if why == "topic_diversity":
        return "strict_gate", "another clip from the same topic was preferred (spread used as a veto)"
    if why == "limit":
        return "not_reached", "editing budget spent on higher-ranked candidates"
    return "", ""


def classify(report: dict[str, Any]) -> dict[str, Any]:
    pool = {c["key"]: c for c in report.get("pool") or []}
    decisions = {d["key"]: d for d in report.get("decisions") or []}
    shipped = {x["key"] for x in report.get("shipped") or []}
    edited = {x["key"]: x for x in report.get("editor_rejected") or []}
    unreviewed = {x["key"]: x for x in report.get("editor_unreviewed") or []}
    rows: list[dict[str, Any]] = []
    keys = list(dict.fromkeys(list(decisions) + list(pool) + list(edited) + list(unreviewed) + list(shipped)))
    for k in keys:
        c = pool.get(k) or decisions.get(k) or {}
        d = decisions.get(k) or {}
        row = {"key": k, "title": c.get("title", ""), "start": c.get("start"), "end": c.get("end"),
               "score": (d.get("scores") or c.get("scores") or {}).get("final"), "stage": "", "category": "",
               "detail": ""}
        if k in shipped:
            row.update(stage="editor", category="shipped")
        elif k in unreviewed:
            row.update(stage="editor", category="model_failure", detail=str(unreviewed[k].get("reason", ""))[:200])
        elif k in edited:
            e = edited[k]
            cat = str(e.get("category") or "editor_rejected")
            repaired = cat.startswith("repair_failed:")
            base = EDITOR.get(cat.split(":")[-1], "weak_moment")
            votes = d.get("verdicts") or []
            if base == "weak_moment" and votes and sum(v == "ship" for v in votes) > len(votes) / 2:
                base = "editor_disagreement"
            row.update(stage="editor", category="repair_failed" if repaired and base not in (
                "weak_moment", "editor_disagreement", "model_failure") else base,
                detail=(f"after repair: {base}; " if repaired else "") + str(e.get("reason", ""))[:240],
                failed_checks=e.get("failed_checks") or [])
        else:
            cat, why = _ranking_category(d)
            if not cat:
                continue
            row.update(stage="ranking", category=cat, detail=why,
                       verdicts=d.get("verdicts") or [], no_share=(d.get("scores") or {}).get("no_share"))
        rows.append(row)
    by_cat = Counter(r["category"] for r in rows)
    by_bucket = Counter(BUCKET.get(r["category"], "other") for r in rows)
    gen = int(report.get("rejected_proposal_count") or 0)
    summary = _headline(by_bucket, len(rows))
    return {"candidates": len(rows), "by_category": dict(by_cat), "by_bucket": dict(by_bucket),
            "bad_candidate_generation": gen, "rows": rows, "headline": summary}


def _headline(b: Counter, n: int) -> str:
    if not n:
        return "no candidates were found"
    if b.get("shipped"):
        return f"{b['shipped']} of {n} candidates became Shorts"
    order = sorted(((v, k) for k, v in b.items()), reverse=True)
    top = ", ".join(f"{v} {k.replace('_', ' ')}" for v, k in order[:3])
    return f"0 of {n} candidates shipped – earliest failures: {top}"


__all__ = ["classify"]
