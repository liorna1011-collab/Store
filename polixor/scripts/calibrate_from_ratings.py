"""
Polixor – tune the clip quality bar from your own ratings.

After an acceptance review (scripts/acceptance_report.py) you download your
"Would you post it?" ratings as JSON. This tool matches each rating to the
clip's selection record and shows:

  * the score of every clip you rated yes / maybe / no
  * which score parts and penalties separate "yes" from "no"
  * a suggested quality bar (Settings -> Clips -> Quality bar)

    .venv/bin/python scripts/calibrate_from_ratings.py polixor-ratings-<id>.json
    (Windows: .venv\\Scripts\\python.exe scripts\\calibrate_from_ratings.py ...)

Several rating files (several videos) can be passed at once. Nothing is
changed automatically – the suggestion is printed for you to decide.
Messages are in English on purpose: the Windows console shows Hebrew reversed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def load_pairs(rating_file: Path) -> list[dict[str, Any]]:
    """(rating, selection record) for every rated clip in the file."""
    from polixor.db import session_scope
    from polixor.models import Clip, Job
    from polixor.services import analysis_store

    data = json.loads(rating_file.read_text("utf-8"))
    job_id = data.get("job_id", "")
    out: list[dict[str, Any]] = []
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            print(f"  {rating_file.name}: project {job_id} not found in this Polixor data folder")
            return out
        review = analysis_store.load_clip_review(
            Path(job.artifacts["clip_review_path"])) if (job.artifacts or {}).get("clip_review_path") else None
        clips = {c.id: (c.source_start, c.source_end)
                 for c in s.query(Clip).filter(Clip.job_id == job_id)}
    records = (review or {}).get("selected") or []
    for clip_id, r in (data.get("clips") or {}).items():
        if not r.get("rating") or clip_id not in clips:
            continue
        start = clips[clip_id][0]
        rec = next((x for x in records if abs(float(x["start"]) - float(start)) < 0.05), None)
        if rec is None:
            continue
        out.append({"rating": r["rating"], "note": r.get("note", ""), "record": rec})
    return out


def suggest_threshold(pairs: list[dict[str, Any]]) -> Optional[float]:
    """
    The bar that keeps the clips rated "yes" and drops the ones rated "no":
    midway between the weakest "yes" and the strongest "no" when they are
    separable, otherwise the value that misclassifies the fewest clips.
    """
    yes = sorted(p["record"]["final_score"] for p in pairs if p["rating"] == "yes")
    no = sorted(p["record"]["final_score"] for p in pairs if p["rating"] == "no")
    if not yes and not no:
        return None
    if yes and no and max(no) < min(yes):
        return round((max(no) + min(yes)) / 2.0, 3)
    candidates = sorted({round(x, 3) for x in yes + no} | {0.2, 0.9})
    best, best_err = None, 10 ** 9
    for t in candidates:
        err = sum(1 for v in yes if v < t) + sum(1 for v in no if v >= t)
        if err < best_err:
            best, best_err = t, err
    return best


def compare_parts(pairs: list[dict[str, Any]]) -> list[tuple[str, float, float]]:
    """Average of each score part / penalty among "yes" vs "no" clips."""
    rows: dict[str, dict[str, list[float]]] = {}
    for p in pairs:
        if p["rating"] not in ("yes", "no"):
            continue
        rec = p["record"]
        vals = {f"part:{c['key']}": c["value"] for c in rec.get("components") or []}
        vals.update({f"penalty:{c['key']}": c["value"] for c in rec.get("penalties") or []})
        for k, v in vals.items():
            rows.setdefault(k, {"yes": [], "no": []})[p["rating"]].append(float(v))
    out = []
    for k, v in rows.items():
        out.append((k, mean(v["yes"]) if v["yes"] else 0.0, mean(v["no"]) if v["no"] else 0.0))
    return sorted(out, key=lambda r: -abs(r[1] - r[2]))


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    from polixor.config import PATHS, SETTINGS
    from polixor.db import init_db

    PATHS.ensure()
    init_db()
    pairs: list[dict[str, Any]] = []
    for f in argv:
        pairs += load_pairs(Path(f))
    if not pairs:
        print("No rated clips could be matched to their selection records.")
        return 1
    print(f"\n{len(pairs)} rated clips\n")
    for p in sorted(pairs, key=lambda x: -x["record"]["final_score"]):
        r = p["record"]
        print(f"  {p['rating']:5s}  score {r['final_score']:.2f}  {r['hook']['text'][:60]!r}"
              + (f"  – {p['note']}" if p["note"] else ""))
    print("\nWhat separates 'yes' from 'no' (average yes / no):")
    for k, y, n in compare_parts(pairs)[:12]:
        print(f"  {k:32s} {y:5.2f} / {n:5.2f}")
    t = suggest_threshold(pairs)
    current = SETTINGS.get().clip_min_quality
    if t is not None:
        print(f"\nSuggested quality bar: {t:.2f}  (current: {current:.2f})")
        print("Change it in Settings -> Clips -> Quality bar, then press 'Generate again'.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
