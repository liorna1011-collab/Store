"""
Polixor – score runs against a human gold reference (docs/GOLD_SCHEMA.md).

    # score the BEFORE/AFTER runs of an acceptance folder
    python scripts/gold_eval.py score --acceptance acceptance_news_<date> [--gold gold/news_liberman.gold.json]

    # write the committed (redacted) copy of a local gold file
    python scripts/gold_eval.py redact gold/local/news_liberman.gold.json gold/news_liberman.gold.json

    # check a gold file
    python scripts/gold_eval.py check gold/local/news_liberman.gold.json

    # a DRAFT full-source map from the AFTER run (topics, every candidate, junk) for you to review
    python scripts/gold_eval.py draft --acceptance acceptance_livestream_<date>

Without --gold the gold file is found by the video's file name and length in
gold/local/ (your own, readable) and then gold/ (committed, redacted).

The metrics never use the clip engine: precision, recall of the ship moments
(shipped and in the candidate pool), boundary error, key phrases / names /
numbers in the final subtitles, hook grounding, duplicates, how much of the
source the strong model heard and the semantic layer read, and whether the
run said which intelligence mode produced it. Unresolved words are not scored.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from polixor.evaluation import gold as G          # noqa: E402

GOLD_DIRS = (ROOT / "gold" / "local", ROOT / "gold")


def _read(path: Any) -> Any:
    try:
        return json.loads(Path(path).read_text("utf-8")) if path else None
    except (OSError, ValueError):
        return None


def _strong_seconds(arts: dict[str, Any], diag: dict[str, Any], media_s: float) -> Optional[float]:
    intel = diag.get("intelligence") or {}
    if intel.get("strong_asr_seconds") is not None:
        return float(intel["strong_asr_seconds"])
    # older versions: the windows re-heard by the strong model (or nothing)
    if not arts.get("strong_windows_path"):
        return 0.0
    sw = _read(arts.get("strong_windows_path"))
    if sw is None:
        secs = (diag.get("strong_windows") or {}).get("audio_seconds")
        return float(secs) if secs is not None else None
    secs = sum(max(0.0, float(w.get("end", 0)) - float(w.get("start", 0)))
               for w in sw.get("windows") or [] if w.get("accepted"))
    return round(min(media_s or secs, secs), 1)


def run_from_kit(result: dict[str, Any]) -> G.Run:
    """A version's run as recorded by scripts/acceptance_compare.py (<label>.json)."""
    diag = result.get("diagnostics") or {}
    arts = result.get("artifacts") or {}
    hooks = {h.get("clip_id"): h for h in diag.get("editorial_hooks") or [] if h.get("rendered")}
    clips, longs = [], []
    for c in result.get("clips") or []:
        cl = G.Clip(start=float(c["start"]), end=float(c["end"]),
                    text=" ".join(str(q.get("text") or "") for q in c.get("cues") or []),
                    hook=str((hooks.get(c.get("id")) or {}).get("text") or ""),
                    title=str(c.get("title") or ""), kind=str(c.get("kind") or "short"),
                    segments=[(float(a), float(b)) for a, b in c.get("segments") or []])
        (longs if cl.kind in ("long", "longform", "highlights") else clips).append(cl)
    for c in result.get("long_clips") or []:
        longs.append(G.Clip(start=float(c["start"]), end=float(c["end"]), kind="long",
                            segments=[(float(a), float(b)) for a, b in c.get("segments") or []]))
    intel = diag.get("intelligence") or {}
    report = _read(arts.get("intel_report_path")) or {}
    pool = [(float(p["start"]), float(p["end"])) for p in (report.get("pool") or intel.get("pool") or [])]
    pool_known = bool(report.get("pool") or intel.get("pool"))
    if not pool_known:
        review = _read(arts.get("clip_review_path")) or {}
        pool = [(float(r["start"]), float(r["end"])) for r in
                (review.get("selected") or []) + (review.get("near_misses") or [])]
    media_s = float(result.get("media_seconds") or 0.0)
    return G.Run(
        label=str(result.get("label") or ""), clips=clips, pool=pool, pool_known=pool_known,
        mode=intel.get("mode"), mode_reason=str(intel.get("reason") or ""),
        clips_labelled=bool(intel.get("clips_labelled")),
        strong_seconds=_strong_seconds(arts, diag, media_s),
        semantic_seconds=(float(intel["semantic_seconds"]) if intel.get("semantic_seconds") is not None
                          else None),
        source_seconds=media_s, longs=longs)


def score_acceptance(folder: Path, gold_path: Optional[Path] = None,
                     write: bool = True) -> Optional[dict[str, Any]]:
    runs = {lab: _read(folder / f"{lab}.json") for lab in ("before", "after")}
    runs = {k: v for k, v in runs.items() if v}
    if not runs:
        raise SystemExit(f"No before.json / after.json in {folder}")
    any_run = next(iter(runs.values()))
    if gold_path is None:
        gold_path = G.find_gold(Path(str(any_run.get("media") or "")), float(any_run.get("media_seconds") or 0),
                                GOLD_DIRS)
    if gold_path is None:
        return None
    gold = G.load_gold(gold_path)
    res = {lab: G.evaluate(gold, run_from_kit(r)) for lab, r in runs.items()}
    if write:
        (folder / "gold_eval.json").write_text(
            json.dumps({"gold_file": str(gold_path), "results": res}, ensure_ascii=False, indent=1), "utf-8")
        (folder / "gold_eval.md").write_text("\n".join(G.markdown(res)) + "\n", "utf-8")
    return {"gold_file": str(gold_path), "results": res}


def draft(folder: Path, out_dir: Optional[Path] = None) -> Path:
    """
    A draft full-source gold map from the AFTER run's semantic report: its topics,
    every candidate of the pool (label "hold" = not reviewed), its junk ranges as
    proposed negatives, and the blind ratings you already gave (as ratings).
    Status draft_unreviewed: never used for scoring until you review it and change
    the status. Written to gold/local/ (not committed) with a review sheet (.md).
    """
    after = _read(folder / "after.json") or {}
    arts = after.get("artifacts") or {}
    rep = _read(arts.get("intel_report_path")) or {}
    if not rep.get("pool"):
        raise SystemExit("The AFTER run has no semantic report (was it in degraded mode?).")
    media = Path(str(after.get("media") or "source"))
    gid = f"{media.stem}_draft"
    moments = [{"id": p["key"], "start": p["start"], "end": p["end"], "label": "hold", "kind": p.get("type", ""),
                "topic": p.get("topic", ""), "note": p.get("title", ""),
                "anchors": [{"role": "hook" if k == 0 else ("payoff" if k == len(p.get("evidence") or []) - 1
                                                            else "other"),
                             "t": e["t"], "t_end": e["t"], "text": e["quote"]}
                            for k, e in enumerate(p.get("evidence") or [])]}
               for p in rep["pool"]]
    data = {"schema": G.SCHEMA, "id": gid, "salt": gid, "status": "draft_unreviewed", "coverage": "partial",
            "status_note": "Machine draft from the semantic run. Review every moment: set label ship/maybe/no, fix "
                           "times, mark unresolved words; then set status to 'verified'.",
            "source": {"duration": after.get("media_seconds"), "language": "he",
                       "match": {"name_contains": [media.stem.lower()]}},
            "moments": moments,
            "negatives": [{"id": f"X{i + 1}", "start": j["start"], "end": j["end"], "reason": j.get("kind", "")}
                          for i, j in enumerate(rep.get("junk") or [])],
            "topics": [{"id": t["id"], "start": t["start"], "end": t["end"], "note": t.get("title", "")}
                       for t in rep.get("topics") or []],
            "terms": [], "unresolved": []}
    out_dir = out_dir or (ROOT / "gold" / "local")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{gid}.gold.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    lines = [f"# Draft gold – {media.name}", "", "Mark each moment ship / maybe / no (edit the JSON label).", ""]
    for t in data["topics"]:
        lines.append(f"## {t['id']} {t['start'] / 60:.0f}:{t['start'] % 60:02.0f}–{t['end'] / 60:.0f}:"
                     f"{t['end'] % 60:02.0f} {t['note']}")
        for m in moments:
            if m["topic"] == t["id"]:
                q = " → ".join(f"“{a['text']}”" for a in m["anchors"])
                lines.append(f"- [ ] {m['id']} {m['start']:.1f}–{m['end']:.1f} ({m['kind']}) {m['note']}: {q}")
        lines.append("")
    path.with_suffix(".md").write_text("\n".join(lines) + "\n", "utf-8")
    return path


def check(path: Path) -> int:
    g = G.load_gold(path)
    problems = []
    ids = [m.id for m in g.moments]
    if len(ids) != len(set(ids)):
        problems.append("duplicate moment ids")
    for m in g.moments:
        if not (0 <= m.start < m.end <= (g.duration or m.end) + 0.5):
            problems.append(f"{m.id}: impossible range {m.start}-{m.end}")
        for a in m.anchors:
            if not (m.start - 2 <= a.t <= a.t_end <= m.end + 2):
                problems.append(f"{m.id}: anchor {a.role} at {a.t} outside the moment")
        for a, b in m.cuts:
            if not (m.start < a < b < m.end):
                problems.append(f"{m.id}: cut {a}-{b} outside the moment")
    known = {str(u.get("id")) for u in g.unresolved}
    for m in g.moments:
        for u in m.unresolved:
            if u not in known:
                problems.append(f"{m.id}: unresolved {u} is not listed")
    print(f"{path.name}: {len(g.moments)} moments ({len(g.ship)} ship), {len(g.negatives)} negatives, "
          f"{len(g.topics)} topics, {len(g.terms)} terms, {len(g.unresolved)} unresolved; status {g.status}")
    for p in problems:
        print("  PROBLEM:", p)
    return 1 if problems else 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("score")
    sc.add_argument("--acceptance", type=Path, required=True)
    sc.add_argument("--gold", type=Path)
    rd = sub.add_parser("redact")
    rd.add_argument("src", type=Path)
    rd.add_argument("dst", type=Path)
    ck = sub.add_parser("check")
    ck.add_argument("path", type=Path)
    dr = sub.add_parser("draft")
    dr.add_argument("--acceptance", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.cmd == "draft":
        print(f"wrote {draft(args.acceptance.resolve())} (draft_unreviewed – review it before it counts)")
        return 0
    if args.cmd == "redact":
        data = json.loads(args.src.read_text("utf-8"))
        args.dst.write_text(json.dumps(G.redact(data), ensure_ascii=False, indent=1) + "\n", "utf-8")
        G.load_gold(args.dst)
        print(f"wrote {args.dst}")
        return 0
    if args.cmd == "check":
        return check(args.path)
    out = score_acceptance(args.acceptance.resolve(), args.gold)
    if out is None:
        print("No gold reference for this source (use --gold FILE).")
        return 2
    print("\n".join(G.markdown(out["results"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
