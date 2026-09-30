"""
בדיקה לכלי דוח הקבלה (scripts/acceptance_report.py): מריצה את הפייפליין
האמיתי על וידאו הבדיקה (תמלול fixture), בונה את הדוח ובודקת שיש בו את
מה שנדרש לשיפוט אנושי: קליפים, וו ופאנץ' עם נימוק, "כמעט", כפילויות,
ביטחון כתוביות וזמני עיבוד.

הרצה:  python3 tests/test_acceptance_report.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxacc_"))
VIDEO = Path("/home/claude/testdata/polixor_test_stream.mp4")
FIXTURE = VIDEO.with_suffix(".transcript.json")
os.environ["POLIXOR_FIXTURE_TRANSCRIPT"] = str(FIXTURE)

from polixor import i18n                                              # noqa: E402
from polixor.config import PATHS, AppSettings                         # noqa: E402
from polixor.db import init_db, session_scope                         # noqa: E402
from polixor.models import Job, JobStatus, ProjectPhase, RunScope, new_id  # noqa: E402
from polixor.pipeline import run_job                                  # noqa: E402
from polixor.project_config import default_config                     # noqa: E402

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "acceptance_report.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("acceptance_report", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    if not (VIDEO.exists() and FIXTURE.exists()):
        print("אין וידאו בדיקה – דילוג")
        return 0
    PATHS.ensure()
    init_db()
    base = AppSettings().to_dict()
    base.update({"transcript_provider": "fixture", "ai_mode": "heuristic", "video_quality": "low"})
    cfg = default_config("he", "he")
    cfg.update({"clip_min_seconds": 10, "clip_max_seconds": 45})
    jid = new_id()
    with session_scope() as s:
        s.add(Job(id=jid, title="בדיקת קבלה", input_url="", status=JobStatus.QUEUED,
                  settings_snapshot=base, artifacts={"source_path": str(VIDEO)},
                  completed_stages=[], run_scope=RunScope.ANALYZE.value,
                  phase=ProjectPhase.IMPORTING.value, ui_language="he",
                  content_language="he", project_config=cfg))
    run_job(jid, threading.Event())
    with session_scope() as s:
        job = s.get(Job, jid)
        job.run_scope, job.mode, job.status = RunScope.GENERATE.value, "short", JobStatus.QUEUED
    run_job(jid, threading.Event())

    tool = _load_tool()
    with i18n.use_lang("en"):
        rep = tool.build(jid)
    out = Path(tempfile.mkdtemp(prefix="pxaccout_"))
    tool.write_markdown(rep, out)
    tool.write_html(rep, out)
    (out / "report.json").write_text(json.dumps(rep, default=str, ensure_ascii=False), "utf-8")

    checks = []

    def check(cond: bool, label: str) -> None:
        checks.append(bool(cond))
        mark = "\033[92m✓\033[0m" if cond else "\033[91m✗\033[0m"
        print(f"  {mark} {label}")

    clips = rep["clips"]
    check(len(clips) >= 1, f"הדוח כולל {len(clips)} קליפים")
    check(all(c["selection"] and c["selection"]["hook"]["text"] and c["selection"]["payoff"]["text"]
              for c in clips), "לכל קליפ וו ופאנץ' מהדוח של המנוע")
    check(all(c["selection"]["hook"]["reasons"] and c["selection"]["payoff"]["reasons"] for c in clips),
          "לכל וו ולכל פאנץ' יש נימוק")
    check(all(c["subtitles"]["words"] > 0 and c["subtitles"]["low_confidence_ratio"] is not None
              for c in clips), "ביטחון הכתוביות מחושב לכל קליפ")
    check("near_misses" in rep and "duplicates" in rep, "הדוח כולל 'כמעט' וכפילויות")
    perf = rep["performance"]
    check(perf and perf["total_rtf"] and {"transcribe", "analyze", "select", "render_short"}
          <= {s["stage"] for s in perf["stages"]}, "זמני עיבוד לכל שלב עם RTF")
    md = (out / "report.md").read_text("utf-8")
    page = (out / "report.html").read_text("utf-8")
    check("Would you post it?" in md and "Would you post it?" in page, "שאלת 'האם היית מפרסם' לכל קליפ")
    check(page.count("<video") == len(clips) and "Download my ratings" in page,
          "עמוד הדוח: נגן לכל קליפ והורדת הדירוגים")
    ok = sum(checks)
    print(f"\n{ok}/{len(checks)} בדיקות דוח קבלה עברו")
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
