"""
Browser-close E2E: the work happens on the server, the browser is only a remote control.

    python tests/e2e_background.py --url http://127.0.0.1:PORT --password-file F [--chromium PATH]

Against a RUNNING server in worker-process mode (the default of `python -m polixor.main`),
started with POLIXOR_PAID_AI=off (and POLIXOR_SEMANTIC_SCRIPTED=1 for the whole editorial
path without a paid model) and the fixture transcript. Steps:

  1. desktop browser: sign in, upload the test video through the real page, choose the
     package goal, press Start
  2. as soon as the project is processing: the browser is CLOSED (context and process)
  3. the WEB SERVER is restarted while the job runs (--restart-cmd, optional): the worker
     processes keep working
  4. nothing from the browser while the server works (checked through the API only)
  5. "phone": a NEW browser (mobile viewport, touch, no shared cookies) signs in, opens the
     project list, finds the project done, opens it, plays/downloads a finished Short
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("✓ " if ok else "✗ ") + name + (f" – {detail}" if detail and not ok else ""), flush=True)


def main() -> int:
    from playwright.sync_api import sync_playwright

    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--password-file", required=True)
    ap.add_argument("--video", default="/home/claude/testdata/polixor_test_stream.mp4")
    ap.add_argument("--chromium", default="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    ap.add_argument("--restart-cmd", default="", help="shell command that restarts the web server")
    ap.add_argument("--timeout", type=float, default=900)
    a = ap.parse_args()
    base, password = a.url.rstrip("/"), Path(a.password_file).read_text().strip()
    api = httpx.Client(base_url=base, timeout=30)
    api.post("/login", data={"password": password, "next": "/"})
    api.put("/api/settings", json={"transcript_provider": "fixture"})

    with sync_playwright() as p:
        # ---- 1. desktop: upload + Start
        b = p.chromium.launch(executable_path=a.chromium)
        ctx = b.new_context(viewport={"width": 1366, "height": 860})
        page = ctx.new_page()
        page.goto(base + "/?lang=en")
        page.wait_for_selector("input[name=password]")
        page.fill("input[name=password]", password)
        page.keyboard.press("Enter")
        page.wait_for_url(lambda u: "/login" not in u, timeout=30_000)
        page.goto(base + "/new?lang=en")
        page.set_input_files("input[type=file]", a.video)
        page.wait_for_selector("[data-testid=upload-phase][data-phase=complete]", timeout=180_000)
        page.select_option("[data-testid=goal]", "package")
        page.get_by_role("button", name="Start").click()
        page.wait_for_url("**/projects/**", timeout=60_000)
        pid = page.url.rstrip("/").split("/")[-1].split("?")[0]
        check("project started from the browser", bool(pid), page.url)
        # wait until the server is really processing it, then close everything
        t_start = time.time()
        while time.time() - t_start < 60:
            pj = api.get(f"/api/projects/{pid}").json()
            if pj.get("status") == "running":
                break
            time.sleep(0.5)
        check("the server is processing it", pj.get("status") == "running", json.dumps(pj.get("status")))
        ctx.close()
        b.close()
        check("the browser is closed while the project runs", True)

        # ---- 3. restart the web server mid-job
        if a.restart_cmd:
            before = api.get(f"/api/projects/{pid}").json()
            subprocess.run(a.restart_cmd, shell=True, check=False, timeout=180)
            for _ in range(120):
                try:
                    if httpx.get(base + "/api/health", timeout=3).status_code == 200:
                        break
                except Exception:                                   # noqa: BLE001
                    pass
                time.sleep(1)
            api = httpx.Client(base_url=base, timeout=30)
            api.post("/login", data={"password": password, "next": "/"})
            after = api.get(f"/api/projects/{pid}").json()
            check("web server restarted mid-job; the job was not interrupted",
                  after.get("status") in ("running", "queued", "completed") and after.get("error") in (None, {}, ""),
                  f"before={before.get('stage')} after={after.get('status')}/{after.get('stage')} err={after.get('error')}")

        # ---- 4. wait server-side only
        t0 = time.time()
        while time.time() - t0 < a.timeout:
            pj = api.get(f"/api/projects/{pid}").json()
            if pj.get("phase") in ("done", "failed") and pj.get("status") not in ("queued", "running"):
                break
            time.sleep(3)
        check("the project finished with no browser open", pj.get("phase") == "done",
              f"phase={pj.get('phase')} status={pj.get('status')} error={pj.get('error')}")
        res = api.get(f"/api/studio/projects/{pid}/results").json()
        made = [x for x in res.get("shorts", []) if x.get("group") not in ("in_progress", "failed")]
        check("Shorts were produced while nobody watched", len(made) >= 1, f"{len(res.get('shorts', []))} shorts")

        # ---- 5. phone return
        b2 = p.chromium.launch(executable_path=a.chromium)
        phone = b2.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                               device_scale_factor=3,
                               user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                                          "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
        m = phone.new_page()
        m.goto(base + "/?lang=en")
        m.wait_for_selector("input[name=password]")
        m.fill("input[name=password]", password)
        m.keyboard.press("Enter")
        m.wait_for_url(lambda u: "/login" not in u, timeout=30_000)
        m.goto(f"{base}/projects/{pid}?lang=en")
        m.wait_for_selector("[data-testid=output-card]", timeout=30_000)
        cards = m.locator("[data-testid=output-card]").count()
        check("phone: the finished project opens with its outputs", cards >= 1, f"{cards} cards")
        width = m.evaluate("document.documentElement.scrollWidth")
        check("phone: no horizontal scrolling", width <= 392, f"scrollWidth={width}")
        href = m.locator("[data-testid=output-card] a", has_text="Download").first.get_attribute("href")
        r = m.evaluate("async (u) => { const r = await fetch(u, {headers: {Range: 'bytes=0-1023'}}); "
                       "return [r.status, (await r.arrayBuffer()).byteLength] }", href)
        check("phone: a finished Short downloads", r[0] in (200, 206) and r[1] > 0, str(r))
        phone.close()
        b2.close()

    ok = all(x[1] for x in RESULTS)
    print(f"{sum(1 for x in RESULTS if x[1])}/{len(RESULTS)} browser-close checks passed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
