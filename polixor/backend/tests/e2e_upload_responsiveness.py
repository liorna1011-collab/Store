"""
The app stays clickable while a big upload runs over a slow line.

Chrome opens at most 6 connections per host over HTTP/1.1. If the upload's parallel chunk requests
took all of them, every click would wait behind multi-second chunk requests (measured before the
fix: API p95 1.7 s, a route change 2.5 s). The upload now keeps two connections free.

    python tests/e2e_upload_responsiveness.py URL PASSWORD_FILE BIG_VIDEO MB_PER_S
Against a running server (CODESPACES=true for the Codespaces transport profile). Fails when an
app API call during the upload takes over 1 s at p95.
"""
import sys, time, json, statistics
from playwright.sync_api import sync_playwright
url, pwf, path, mbps = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
res = {}
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(); page = ctx.new_page()
    cdp = ctx.new_cdp_session(page); cdp.send("Network.enable")
    page.goto(url + "/?lang=en"); page.fill("input[name=password]", open(pwf).read().strip()); page.keyboard.press("Enter")
    page.wait_for_url(lambda u: "/login" not in u)
    res["protocol"] = page.evaluate("performance.getEntriesByType('navigation')[0]?.nextHopProtocol")
    cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 40, "downloadThroughput": -1,
                                                   "uploadThroughput": int(mbps * 1024 * 1024)})
    page.goto(url + "/new?lang=en"); page.set_input_files("input[type=file]", path)
    page.wait_for_selector("[data-testid=upload-phase][data-phase=uploading]", timeout=60000)
    page.wait_for_timeout(8000)                                  # let the parallel requests ramp up
    lat = []
    routes = ["/", "/clips", "/settings", "/new", "/"] * 3
    for r in routes:
        t0 = time.time()
        page.evaluate(f"window.history.pushState({{}}, '', '{r}'); dispatchEvent(new PopStateEvent('popstate'))")
        # the page is usable when its data arrived: wait for the network to be idle except the upload
        page.wait_for_function("() => !document.querySelector('.animate-pulse, [aria-busy=true]')", timeout=120000)
        lat.append(round((time.time() - t0) * 1000))
    api = page.evaluate("""() => performance.getEntriesByType('resource')
        .filter(e => e.name.includes('/api/') && !e.name.includes('/chunk') && !e.name.includes('/uploads/'))
        .map(e => Math.round(e.duration))""")
    res["route_ms"] = {"p50": statistics.median(lat), "max": max(lat), "all": lat}
    api.sort()
    res["api_ms"] = {"n": len(api), "p50": api[len(api)//2] if api else None, "p95": api[int(len(api)*.95)-1] if api else None, "max": api[-1] if api else None}
    res["upload_parallel"] = page.evaluate("document.querySelector('[data-testid=upload-speed]')?.innerText || ''")
    b.close()
print(json.dumps(res))
ok = res['api_ms']['p95'] is not None and res['api_ms']['p95'] < 1000
print('PASS' if ok else 'FAIL: app requests waited behind the upload')
sys.exit(0 if ok else 1)
