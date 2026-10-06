"""
Browser stress test WHILE the backend is busy: does the page stay responsive?

    python tests/ui_stress.py --url http://127.0.0.1:PORT --password-file F --project PID [--seconds 240]

Against a running server with a project that is processing (see --start to create one from a
file). With the project page open it repeatedly, for --seconds:

  * clicks every main navigation item and returns to the project (in-page latency: click →
    new route rendered, measured with performance.now() in the page itself)
  * opens / closes panels, scrolls, switches the interface language, plays an existing video

and records, through the Chrome DevTools Protocol:

  * main-thread long tasks (PerformanceObserver 'longtask'), total blocking time, the longest
  * WebSocket frames received (count, bytes, per second) and every HTTP response's size
  * JS heap size over time; console errors; page errors

FAILS when: any long task > 1000 ms, any navigation > 2500 ms, a page error, or the heap grows
by more than 150 MB. Prints a JSON report (and --out FILE).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

NAV = [("/", "dashboard"), ("/new", "newProject"), ("/clips", "clips"), ("/settings", "settings")]

PROBE = """() => {
  if (window.__px) return;
  window.__px = {lt: [], nav: []};
  try {
    new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__px.lt.push([e.startTime, e.duration]); })
      .observe({type: 'longtask', buffered: true});
  } catch (e) {}
}"""

# click a nav link and resolve when the new route has rendered (2 frames after the URL and a heading)
NAVIGATE = """async ([href]) => {
  const t0 = performance.now();
  const a = [...document.querySelectorAll('nav a')].find((x) => x.getAttribute('href') === href);
  if (!a) return -1;
  a.click();
  const deadline = t0 + 15000;
  while (performance.now() < deadline) {
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    const main = document.querySelector('main');
    if (location.pathname === href && main && main.querySelector('h1, h2, [data-testid]')) break;
  }
  return performance.now() - t0;
}"""


def pct(v: list[float], p: float) -> float:
    v = sorted(v)
    return round(v[min(len(v) - 1, int(len(v) * p))], 1) if v else 0.0


def main() -> int:
    from playwright.sync_api import sync_playwright

    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--password-file", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--seconds", type=float, default=240)
    ap.add_argument("--chromium", default="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    ap.add_argument("--out", default="")
    ap.add_argument("--max-longtask", type=float, default=1000)
    ap.add_argument("--max-nav", type=float, default=2500)
    ap.add_argument("--idle", type=float, default=60, help="seconds on the project page without interaction first")
    ap.add_argument("--cpu-throttle", type=float, default=1.0, help="Chrome CPU slowdown (4 ≈ an ordinary laptop)")
    a = ap.parse_args()
    base, pw = a.url.rstrip("/"), Path(a.password_file).read_text().strip()
    ws_frames: list[tuple[float, int]] = []
    sizes: dict[str, list[int]] = defaultdict(list)
    urls: dict[str, str] = {}
    errors: list[str] = []
    heap: list[tuple[float, float]] = []
    nav_ms: dict[str, list[float]] = defaultdict(list)
    wall_ms: list[float] = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=a.chromium)
        ctx = b.new_context(viewport={"width": 1366, "height": 860})
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text[:200]}") if m.type == "error" else None)
        cdp = ctx.new_cdp_session(page)
        cdp.send("Network.enable")
        cdp.send("Performance.enable")
        if a.cpu_throttle > 1:
            cdp.send("Emulation.setCPUThrottlingRate", {"rate": a.cpu_throttle})
        lt_all: list[float] = []

        def harvest() -> None:
            try:
                lt_all.extend(d for _s, d in page.evaluate("() => { const x = (window.__px && window.__px.lt) || [];"
                                                           " if (window.__px) window.__px.lt = []; return x }"))
            except Exception:                                    # noqa: BLE001
                pass
        cdp.on("Network.webSocketFrameReceived",
               lambda e: ws_frames.append((time.time(), len(e["response"].get("payloadData") or ""))))
        cdp.on("Network.responseReceived", lambda e: urls.__setitem__(e["requestId"], e["response"]["url"]))

        def finished(e):
            u = urls.get(e["requestId"], "")
            if "/api/" in u:
                path = u.split(base, 1)[-1].split("?")[0]
                for seg in path.split("/"):
                    if len(seg) == 16 and seg.isalnum():
                        path = path.replace(seg, "{id}")
                sizes[path].append(int(e.get("encodedDataLength") or 0))
        cdp.on("Network.loadingFinished", finished)

        page.goto(base + "/?lang=en")
        page.wait_for_selector("input[name=password]")
        page.fill("input[name=password]", pw)
        page.keyboard.press("Enter")
        page.wait_for_url(lambda u: "/login" not in u, timeout=30_000)
        page.goto(f"{base}/projects/{a.project}?lang=en")
        page.wait_for_selector("main h1, main h2", timeout=30_000)
        page.evaluate(PROBE)
        # 1) sit on the busy project page without touching anything: only the live updates run
        harvest()
        idle_t0 = time.time()
        page.wait_for_timeout(int(a.idle * 1000))
        idle_lt: list[float] = []
        lt_before = len(lt_all)
        harvest()
        idle_lt = lt_all[lt_before:]
        idle_secs = time.time() - idle_t0
        t_end = time.time() + a.seconds
        rounds = 0
        while time.time() < t_end:
            rounds += 1
            for href, _key in NAV:
                t0 = time.time()
                ms = page.evaluate(NAVIGATE, [href])
                wall_ms.append((time.time() - t0) * 1000)
                nav_ms[href].append(ms)
                page.mouse.wheel(0, 600)
                page.mouse.wheel(0, -600)
            # back to the project through the dashboard list or directly
            harvest()
            t0 = time.time()
            page.goto(f"{base}/projects/{a.project}")
            page.wait_for_selector("main h1, main h2", timeout=30_000)
            nav_ms["project(full load)"].append((time.time() - t0) * 1000)
            page.evaluate(PROBE)
            # panels: open/close every <details> on the page
            page.evaluate("() => document.querySelectorAll('main details').forEach((d) => { d.open = !d.open })")
            page.wait_for_timeout(300)
            page.evaluate("() => document.querySelectorAll('main details').forEach((d) => { d.open = false })")
            # play a finished video if one exists
            page.evaluate("() => { const v = document.querySelector('main video'); if (v) { v.muted = true; "
                          "v.play().catch(() => {}); setTimeout(() => v.pause(), 1500) } }")
            if rounds % 3 == 0:
                # language switch (hidden ?lang=) and back
                harvest()
                page.goto(f"{base}/projects/{a.project}?lang=he")
                page.wait_for_selector("main h1, main h2", timeout=30_000)
                page.evaluate(PROBE)
                harvest()
                page.goto(f"{base}/projects/{a.project}?lang=en")
                page.wait_for_selector("main h1, main h2", timeout=30_000)
                page.evaluate(PROBE)
            m = cdp.send("Performance.getMetrics")
            hv = next((x["value"] for x in m["metrics"] if x["name"] == "JSHeapUsedSize"), 0)
            heap.append((time.time(), hv / 1e6))
            page.wait_for_timeout(500)
        harvest()
        b.close()
    dur = max(1.0, a.seconds)
    lts = lt_all
    allnav = [x for k, v in nav_ms.items() if not k.startswith("project") for x in v if x >= 0]
    report = {
        "seconds": a.seconds, "rounds": rounds, "cpu_throttle": a.cpu_throttle,
        "idle_on_project_page": {"seconds": round(idle_secs, 1), "longtasks": len(idle_lt),
                                 "blocking_ms_per_minute": round(sum(max(0, d - 50) for d in idle_lt) / max(1, idle_secs) * 60, 1),
                                 "max_ms": round(max(idle_lt, default=0), 1)},
        "longtasks": {"count": len(lts), "max_ms": round(max(lts, default=0), 1),
                      "total_blocking_ms": round(sum(max(0, d - 50) for d in lts), 1),
                      "over_200ms": sum(1 for d in lts if d > 200)},
        "navigation_ms": {k: {"n": len(v), "p50": pct(v, 0.5), "p95": pct(v, 0.95), "max": round(max(v), 1)}
                          for k, v in nav_ms.items() if v},
        "navigation_wall_ms": {"p50": pct(wall_ms, 0.5), "p95": pct(wall_ms, 0.95), "max": round(max(wall_ms, default=0), 1)},
        "websocket": {"frames": len(ws_frames), "per_second": round(len(ws_frames) / dur, 2),
                      "kb_total": round(sum(n for _t, n in ws_frames) / 1024, 1),
                      "largest_bytes": max((n for _t, n in ws_frames), default=0)},
        "http": sorted(({"path": k, "n": len(v), "kb_each_max": round(max(v) / 1024, 1),
                         "kb_total": round(sum(v) / 1024, 1)} for k, v in sizes.items()),
                       key=lambda x: -x["kb_total"])[:12],
        "heap_mb": {"start": round(heap[0][1], 1) if heap else None, "max": round(max((h for _t, h in heap), default=0), 1),
                    "end": round(heap[-1][1], 1) if heap else None},
        "errors": errors[:20],
    }
    growth = (report["heap_mb"]["end"] or 0) - (report["heap_mb"]["start"] or 0)
    fails = []
    if report["longtasks"]["max_ms"] > a.max_longtask:
        fails.append(f"long task {report['longtasks']['max_ms']} ms")
    if allnav and max(allnav) > a.max_nav:
        fails.append(f"navigation {max(allnav):.0f} ms")
    if any(e.startswith("pageerror") for e in errors):
        fails.append("page error")
    if growth > 150:
        fails.append(f"heap grew {growth:.0f} MB")
    report["fail"] = fails
    print(json.dumps(report, indent=1, ensure_ascii=False))
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=1, ensure_ascii=False))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
