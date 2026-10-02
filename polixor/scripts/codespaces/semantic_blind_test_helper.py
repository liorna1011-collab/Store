"""Helpers of semantic-blind-test.sh: check that a key is saved (without printing it), pack the results."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def check_key() -> None:
    sys.path.insert(0, str(ROOT / "backend"))
    from polixor.config import SECRETS

    have = [n.split("_")[0].title() for n in ("anthropic_api_key", "openai_api_key") if SECRETS.get(n)]
    print("Language-model key saved in Polixor: " + (", ".join(have) if have else
          "NONE – AFTER will run in the labelled degraded mode. Add your Anthropic key in Settings → AI first."))


def pack(out: Path, dst: Path, gold: Path) -> None:
    names = ["compare.md", "compare.json", "gold_eval.md", "gold_eval.json", "before_diagnostics.json",
             "after_diagnostics.json", "run.log"]
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for n in names:
            if (out / n).exists():
                z.write(out / n, n)
        for pat in ("intel_report.json", "transcript.final.json", "clip_review.json"):
            for p in (out / "after_data").rglob(pat):
                z.write(p, str(p.relative_to(out)))
        for p in sorted(gold.glob("*_draft.gold.json")) + sorted(gold.glob("*_draft.gold.md")):
            z.write(p, "draft_gold/" + p.name)
    print(f"packed {dst}")


if __name__ == "__main__":
    if sys.argv[1] == "check-key":
        check_key()
    elif sys.argv[1] == "pack":
        pack(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
