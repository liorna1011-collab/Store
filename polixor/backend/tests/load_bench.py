"""
Is the website still fast while projects process? (web responsiveness + multi-project benchmark)

    python tests/load_bench.py --url http://127.0.0.1:8756 --password-file F --projects 3 [--video V]

Against a RUNNING server (worker processes on). It uploads the test video through the real
resumable-upload API N times, starts N projects (package goal), and while they process it
requests – every 0.25 s each – /api/health, /api/projects, a project page's data and the index
page. Reports latency p50 / p95 / max per endpoint, errors, and per project: time to first
Short, all Shorts, finished. No paid model call: run the server with POLIXOR_PAID_AI=off (and
POLIXOR_SEMANTIC_SCRIPTED=1 to exercise the whole editorial path with the stand-in editor).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx

ENDPOINTS = ("/api/health", "/api/projects", "/", "PROJECT")


def login(c: httpx.Client, password: str) -> None:
    r = c.post("/login", data={"password": password, "next": "/"}, follow_redirects=False)
    if r.status_code not in (200, 302, 303):
        raise SystemExit(f"login failed: {r.status_code}")


def upload(c: httpx.Client, video: Path) -> str:
    size = video.stat().st_size
    data = video.read_bytes()
    fp = hashlib.sha256(data[:1 << 20]).hexdigest()[:32] + f"-{time.time_ns()}"
    s = c.post("/api/uploads", json={"filename": video.name, "size": size, "fingerprint": fp}).json()
    uid, chunk = s["upload_id"], int(s.get("chunk_size") or 8 << 20)
    off = 0
    while off < size:
        part = data[off:off + chunk]
        r = c.put(f"/api/uploads/{uid}/range", params={"offset": off}, content=part,
                  headers={"x-upload-length": str(len(part)), "x-chunk-sha256": hashlib.sha256(part).hexdigest()})
        r.raise_for_status()
        off += len(part)
    c.post(f"/api/uploads/{uid}/complete").raise_for_status()
    for _ in range(120):
        st = c.get(f"/api/uploads/{uid}").json()
        if st.get("status") == "complete":
            return uid
        time.sleep(0.5)
    raise SystemExit("upload did not complete")


def probe(base: str, cookies: Any, path: str, stop: threading.Event, out: dict[str, list[float]],
          errors: dict[str, int]) -> None:
    with httpx.Client(base_url=base, cookies=cookies, timeout=30) as c:
        while not stop.is_set():
            t0 = time.perf_counter()
            try:
                r = c.get(path)
                ok = r.status_code < 500
            except Exception:                                       # noqa: BLE001
                ok = False
            dt = time.perf_counter() - t0
            out.setdefault(path, []).append(dt)
            if not ok:
                errors[path] = errors.get(path, 0) + 1
            stop.wait(0.25)


def pct(v: list[float], p: float) -> float:
    v = sorted(v)
    return round(v[min(len(v) - 1, int(len(v) * p))] * 1000, 1) if v else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8756")
    ap.add_argument("--password-file", default="")
    ap.add_argument("--projects", type=int, default=3)
    ap.add_argument("--video", default="/home/claude/testdata/polixor_test_stream.mp4")
    ap.add_argument("--timeout", type=float, default=1800)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    c = httpx.Client(base_url=a.url, timeout=60)
    if a.password_file:
        login(c, Path(a.password_file).read_text().strip())
    # the bench uses the fixture transcript the server was started with (no speech model download)
    c.put("/api/settings", json={"transcript_provider": "fixture"})
    idle: dict[str, list[float]] = {}
    errs: dict[str, int] = {}
    stop = threading.Event()
    th = threading.Thread(target=probe, args=(a.url, c.cookies, "/api/projects", stop, idle, errs))
    th.start()
    time.sleep(5)
    stop.set()
    th.join()

    t_start = time.time()
    pids = []
    for i in range(a.projects):
        uid = upload(c, Path(a.video))
        r = c.post("/api/projects", json={"source": {"type": "upload", "upload_id": uid}, "title": f"load {i + 1}",
                                          "goal": "package", "clip_count": 3})
        r.raise_for_status()
        pids.append(r.json()["id"])
    lat: dict[str, list[float]] = {}
    stop = threading.Event()
    threads = [threading.Thread(target=probe, args=(a.url, c.cookies, p if p != "PROJECT" else f"/api/projects/{pids[0]}",
                                                    stop, lat, errs)) for p in ENDPOINTS]
    for t in threads:
        t.start()
    done: dict[str, dict[str, Any]] = {}
    first: dict[str, float] = {}
    while time.time() - t_start < a.timeout and len(done) < len(pids):
        for pid in pids:
            if pid in done:
                continue
            p = c.get(f"/api/projects/{pid}").json()
            if pid not in first:
                try:
                    res = c.get(f"/api/studio/projects/{pid}/results").json()
                    if any(x.get("group") not in ("in_progress", "failed") for x in res.get("shorts", [])):
                        first[pid] = time.time() - t_start
                except Exception:                                   # noqa: BLE001
                    pass
            if p.get("phase") in ("done", "failed") and p.get("status") not in ("queued", "running"):
                done[pid] = {"phase": p.get("phase"), "status": p.get("status"), "at": round(time.time() - t_start, 1)}
        time.sleep(2)
    stop.set()
    for t in threads:
        t.join()
    report = {
        "projects": len(pids),
        "idle_projects_list_ms": {"p50": pct(idle.get("/api/projects", []), 0.5),
                                  "p95": pct(idle.get("/api/projects", []), 0.95)},
        "under_load_ms": {k: {"n": len(v), "p50": pct(v, 0.5), "p95": pct(v, 0.95), "max": pct(v, 1.0)}
                          for k, v in lat.items()},
        "errors": errs,
        "projects_result": {pid: {**done.get(pid, {"phase": "unfinished"}),
                                  "first_short_s": round(first[pid], 1) if pid in first else None} for pid in pids},
        "wall_s": round(time.time() - t_start, 1),
    }
    print(json.dumps(report, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=1))
    ok = len(done) == len(pids) and all(d["phase"] == "done" for d in done.values()) and not errs
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
