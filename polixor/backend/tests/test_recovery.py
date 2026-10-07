"""
Core recovery pass: the evidence a real project carries about itself (no model call, nothing re-run).

  time breakdown  wall seconds by the categories a person thinks in, biggest first; per-Short editor
                  work that ran a few at a time is scaled to the stage's wall time (and marked);
                  early renders that the editor waited for count as render
  cost ledger     every model run of a project is kept – a re-edit does not erase the first run's cost
  replay estimate what "Re-edit with the improved editor" is expected to cost, from the project's own
                  tokens per call, before anyone presses it

Run:  python3 tests/test_recovery.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxrec_"))
os.environ["POLIXOR_PAID_AI"] = "off"

from polixor.config import PATHS                                   # noqa: E402
from polixor.db import init_db, session_scope                      # noqa: E402

PATHS.ensure()
init_db()


def test_time_breakdown_adds_up_and_scales_parallel_editor_work():
    from polixor.services.diagnostics import time_breakdown

    stages = {"probe": {"seconds": 2}, "audio": {"seconds": 8}, "transcribe": {"seconds": 240},
              "analyze": {"seconds": 30}, "select": {"seconds": 400}, "render_short": {"seconds": 90}}
    subs = {"analyze.audio": {"seconds": 5}, "analyze.visual": {"seconds": 20}, "select.semantic": {"seconds": 300},
            "select.render_wait": {"seconds": 60}, "render.qa": {"seconds": 10}}
    # topic map / candidates / ranking ran one after another (120 s); the per-Short steps summed to
    # 540 s with 3 at a time inside the remaining 180 s of the editor stage
    sem = {"topic_map": 40, "candidates": 50, "ranking": 30, "editor": 300, "boundaries": 60, "reconstruct": 90,
           "final_transcript": 60, "hooks": 30}
    rows = time_breakdown(stages, subs, sem, queue=4)
    by = {r["category"]: r for r in rows}
    assert rows[0]["category"] == "asr" and rows == sorted(rows, key=lambda r: -r["seconds"])
    assert by["editor"]["shared"] and abs(by["editor"]["seconds"] - 360 / 540 * 180) < 0.2
    assert abs(by["repairs"]["seconds"] - 90 / 540 * 180) < 0.2
    assert by["render"]["seconds"] == 80 + 60                       # render stage − QC + early-render wait
    total = sum(r["seconds"] for r in rows)
    assert abs(total - (2 + 8 + 240 + 30 + 400 + 90 + 4)) < 1.0, total   # every wall second once
    assert abs(by["other"]["seconds"] - (5 + 40)) < 0.2               # analysis and editor stage leftovers


def _job(intel: dict, arts: dict | None = None) -> str:
    from polixor.models import Job, JobStatus, new_id

    d = Path(tempfile.mkdtemp())
    p = d / "intel_report.json"
    p.write_text(json.dumps(intel), "utf-8")
    jid = new_id()
    with session_scope() as s:
        s.add(Job(id=jid, title="t", input_url="", status=JobStatus.COMPLETED, phase="done", run_scope="generate",
                  mode="short", project_config={"mode": "short", "clip_count": 5},
                  artifacts={"intel_report_path": str(p), "source_info": {"duration": 973.0}, **(arts or {})},
                  completed_stages=[]))
    return jid


def test_every_run_is_in_the_cost_ledger():
    from polixor.models import Job
    from polixor.services import costs

    jid = _job({"usage": {"calls": 40, "input_tokens": 400000, "output_tokens": 50000}},
               {"ai_runs": [costs.run_record({"calls": 40, "input_tokens": 400000, "output_tokens": 50000},
                                             kind="earlier"),
                            costs.run_record({"calls": 20, "input_tokens": 200000, "output_tokens": 30000},
                                             kind="generation")]})
    with session_scope() as s:
        e = costs.project(s.get(Job, jid), 973000, {"price": 99, "currency": "USD", "minutes": 600})
    first = 0.4 * 4 + 0.05 * 20
    second = 0.2 * 4 + 0.03 * 20
    assert abs(e["ai_cost_usd"] - (first + second)) < 0.01 and len(e["ai_runs"]) == 2


def test_replay_estimate_uses_the_projects_own_tokens_and_keeps_cached_steps_free():
    from polixor.models import Job
    from polixor.services import costs

    by_task = {"topic_map": {"calls": 3, "input_tokens": 90000, "output_tokens": 9000},
               "candidates": {"calls": 6, "input_tokens": 120000, "output_tokens": 30000},
               "rank": {"calls": 4, "input_tokens": 80000, "output_tokens": 8000},
               "boundaries": {"calls": 10, "input_tokens": 40000, "output_tokens": 5000},
               "editor": {"calls": 10, "input_tokens": 80000, "output_tokens": 20000}}
    jid = _job({"usage": {"by_task": by_task}, "pool": [{"key": f"C{i}"} for i in range(30)]})
    with session_scope() as s:
        est = costs.replay_estimate(s.get(Job, jid))
    assert est["available"] and est["candidates_judged"] >= 5
    assert set(est["cached_free"]) == {"topic_map", "candidates", "rank"}
    lo, hi = est["usd_range"]
    assert 0 < lo < est["usd"] < hi < 20, est


def test_upload_bench_is_admin_only_and_the_transport_starts_from_its_best_setting():
    from fastapi.testclient import TestClient

    from polixor.api.routes_admin import admin_token
    from polixor.main import app

    adm = {"x-polixor-admin": admin_token()}
    with TestClient(app) as c:
        assert c.put("/api/admin/upload-bench/sink", content=b"x" * 1000).status_code == 403
        r = c.put("/api/admin/upload-bench/sink", content=b"x" * 300000, headers=adm).json()
        assert r["bytes"] == 300000                                   # read and dropped
        before = c.get("/api/uploads/transport").json()
        best = {"request_bytes": 8 * 1024 * 1024, "concurrency": 3, "polixor_MBps": 12.5,
                "raw_MBps_same_setting": 13.0, "raw_MBps_ceiling": 14.0, "overhead_pct": 4}
        assert c.post("/api/admin/upload-bench/result", json={"rows": [], "best": best}).status_code == 403
        assert c.post("/api/admin/upload-bench/result", json={"rows": [], "best": best}, headers=adm).json()["saved"]
        after = c.get("/api/uploads/transport").json()
        assert c.get("/api/admin/upload-bench/result", headers=adm).json()["best"]["concurrency"] == 3
    assert after["profile"].endswith("+measured") and after["concurrency_start"] == min(3, after["concurrency_max"])
    assert after["start_bytes"] == min(after["max_bytes"], 8 * 1024 * 1024) and before["profile"] != after["profile"]


def _run_all() -> int:
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            t0 = time.time()
            fn()
            print(f"✓ {name} ({time.time() - t0:.1f}s)")
        except Exception as exc:                                    # noqa: BLE001
            import traceback

            failed += 1
            print(f"✗ {name}: {exc}")
            traceback.print_exc()
    print(f"{len(tests) - failed}/{len(tests)} recovery tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
