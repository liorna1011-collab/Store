"""
בדיקות לשער הסיסמה (polixor/access.py) – על האפליקציה האמיתית.

הרצה:  python3 tests/test_access.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxaccess_"))
PASSWORD = "correct horse battery"
os.environ["POLIXOR_ACCESS_PASSWORD"] = PASSWORD

from fastapi.testclient import TestClient                  # noqa: E402
from starlette.websockets import WebSocketDisconnect       # noqa: E402

from polixor import access                                  # noqa: E402
from polixor.main import app                                # noqa: E402

HTML = {"accept": "text/html,application/xhtml+xml"}


def client() -> TestClient:
    return TestClient(app, follow_redirects=False)


def login(c: TestClient, password: str = PASSWORD, nxt: str = "/", **headers):
    return c.post("/login", data={"password": password, "next": nxt}, headers=headers)


# --------------------------------------------------------------------------
def test_everything_is_locked_without_session():
    with client() as c:
        r = c.get("/api/projects")
        assert r.status_code == 401, r.status_code
        assert r.json()["code"] == "auth_required"
        for path in ("/api/settings", "/api/clips", "/api/system", "/docs",
                     "/openapi.json", "/api/clips/x/file", "/assets/index.js"):
            assert c.get(path).status_code in (401, 303), path
        r = c.post("/api/projects", json={})
        assert r.status_code == 401


def test_pages_redirect_to_login_with_next():
    with client() as c:
        r = c.get("/projects/abc?x=1", headers=HTML)
        assert r.status_code == 303
        assert r.headers["location"] == "/login?next=%2Fprojects%2Fabc%3Fx%3D1"


def test_health_and_login_page_are_public():
    with client() as c:
        h = c.get("/api/health").json()
        assert h["ok"] is True and h["access_protected"] is True
        r = c.get("/login")
        assert r.status_code == 200 and 'type="password"' in r.text
        assert r.headers["x-frame-options"] == "DENY"


def test_wrong_password_is_rejected():
    with client() as c:
        r = login(c, "nope", **{"x-forwarded-for": "10.0.0.1"})
        assert r.status_code == 401 and "set-cookie" not in r.headers
        assert c.get("/api/projects").status_code == 401


def test_right_password_opens_everything():
    with client() as c:
        r = login(c, nxt="/projects/abc")
        assert r.status_code == 303 and r.headers["location"] == "/projects/abc"
        cookie = r.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=Lax" in cookie
        assert "Secure" not in cookie                 # http מקומי
        assert c.get("/api/projects").status_code == 200
        assert c.get("/api/settings").status_code == 200
        with c.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "hello"


def test_websocket_rejected_without_session():
    with client() as c:
        try:
            with c.websocket_connect("/ws"):
                pass
        except WebSocketDisconnect as exc:
            assert exc.code == 4401
        else:
            raise AssertionError("websocket accepted without a session")


def test_tampered_or_foreign_cookie_is_rejected():
    with client() as c:
        good = login(c).cookies[access.COOKIE]
    version, issued, sig = good.split(".")
    for bad in (f"{version}.{issued}.{'0' * len(sig)}",
                f"{version}.{int(issued) - 1}.{sig}", "garbage", ""):
        with client() as c:
            c.cookies.set(access.COOKIE, bad)
            assert c.get("/api/projects").status_code == 401, bad


def test_changing_the_password_logs_everyone_out():
    data = Path(os.environ["POLIXOR_DATA_DIR"])
    a = access.AccessGate(None, password="one", data_dir=data)
    b = access.AccessGate(None, password="two", data_dir=data)
    token = a._sign(int(__import__("time").time()))
    assert a._valid(token) and not b._valid(token)


def test_expired_session_is_rejected():
    gate = access.AccessGate(None, password=PASSWORD,
                             data_dir=Path(os.environ["POLIXOR_DATA_DIR"]))
    old = int(__import__("time").time()) - access.SESSION_DAYS * 86400 - 10
    assert not gate._valid(gate._sign(old))


def test_no_open_redirect():
    for evil in ("https://evil.example", "//evil.example", "/\\evil", "javascript:x", "/logout"):
        assert access._safe_next(evil) == "/", evil
    assert access._safe_next("/clips/1/edit?x=2") == "/clips/1/edit?x=2"
    with client() as c:
        r = login(c, nxt="https://evil.example")
        assert r.headers["location"] == "/"


def test_lockout_after_repeated_failures():
    with client() as c:
        ip = {"x-forwarded-for": "203.0.113.7"}
        for _ in range(access.FREE_ATTEMPTS):
            assert login(c, "bad", **ip).status_code == 401
        r = login(c, PASSWORD, **ip)                  # גם הסיסמה הנכונה חסומה בזמן הנעילה
        assert r.status_code == 429 and "set-cookie" not in r.headers
        assert login(c, PASSWORD, **{"x-forwarded-for": "203.0.113.8"}).status_code == 303


def test_secure_cookie_behind_https_proxy():
    with client() as c:
        r = login(c, **{"x-forwarded-proto": "https"})
        assert "Secure" in r.headers["set-cookie"]


def test_logout_clears_the_session():
    with client() as c:
        login(c)
        assert c.get("/api/projects").status_code == 200
        r = c.post("/logout")
        assert r.status_code == 303 and "Max-Age=0" in r.headers["set-cookie"]
        c.cookies.clear()
        assert c.get("/api/projects").status_code == 401


def test_login_page_language():
    with client() as c:
        assert "Sign in to Polixor" in c.get("/login?lang=en").text
        he = c.get("/login", headers={"accept-language": "he-IL"}).text
        assert 'dir="rtl"' in he and "כניסה ל-Polixor" in he


def test_gate_is_off_without_password():
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["path"])

    gate = access.AccessGate(inner, password="")
    asyncio.run(gate({"type": "http", "path": "/api/projects", "headers": []}, None, None))
    assert seen == ["/api/projects"]


# --------------------------------------------------------------------------
def _run_all() -> int:
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    passed, failed = 0, []
    for name, fn in fns:
        try:
            fn()
            print(f"  \033[92m✓\033[0m {name}")
            passed += 1
        except AssertionError as exc:
            print(f"  \033[91m✗\033[0m {name}: {exc}")
            failed.append(name)
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"  \033[91m✗\033[0m {name}: {type(exc).__name__}: {exc}")
            failed.append(name)
    print(f"\n{passed}/{len(fns)} בדיקות גישה עברו")
    if failed:
        print("נכשלו: " + ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
