"""
Resumable chunked upload of source videos (services/uploads.py, api/routes_uploads.py).

Run:  python3 tests/test_uploads.py
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxup_"))
os.environ["POLIXOR_UPLOAD_CHUNK_BYTES"] = str(256 * 1024)        # small chunks: many of them, fast tests

from fastapi.testclient import TestClient                          # noqa: E402

from polixor.config import PATHS                                    # noqa: E402
from polixor.main import app                                        # noqa: E402
from polixor.services import uploads                                # noqa: E402
from polixor.util.ffmpeg import ffmpeg_bin                          # noqa: E402

CH = uploads.CHUNK_SIZE
_VIDEO: dict[str, bytes] = {}


def video() -> bytes:
    """A real ~1.5 MB MP4 (many chunks)."""
    if "v" not in _VIDEO:
        p = Path(tempfile.mkdtemp()) / "v.mp4"
        subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc2=s=640x360:d=6:r=25", "-f", "lavfi", "-i", "sine=f=300:d=6",
                        "-shortest", "-pix_fmt", "yuv420p", "-b:v", "2M", str(p)], check=True)
        _VIDEO["v"] = p.read_bytes()
    return _VIDEO["v"]


def chunks(data: bytes) -> list[bytes]:
    return [data[i:i + CH] for i in range(0, len(data), CH)]


def client() -> TestClient:
    return TestClient(app)


def start(c, data: bytes, name="stream.mp4", fp=""):
    r = c.post("/api/uploads", json={"filename": name, "size": len(data), "fingerprint": fp})
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------
def test_session_out_of_order_duplicates_and_finalize():
    data = video()
    with client() as c:
        s = start(c, data)
        parts = chunks(data)
        assert s["total_chunks"] == len(parts) > 3 and s["chunk_size"] == CH and "path" not in str(s)
        order = list(range(len(parts)))[::-1]                     # out of order
        for i in order:
            h = hashlib.sha256(parts[i]).hexdigest()
            assert c.put(f"/api/uploads/{s['upload_id']}/chunks/{i}", content=parts[i],
                         headers={"X-Chunk-Sha256": h}).status_code == 200
        # a retried (duplicate) chunk is harmless
        assert c.put(f"/api/uploads/{s['upload_id']}/chunks/0", content=parts[0]).status_code == 200
        st = c.get(f"/api/uploads/{s['upload_id']}").json()
        assert st["bytes_received"] == len(data) and not st["missing"]
        done = c.post(f"/api/uploads/{s['upload_id']}/complete").json()
        assert done["status"] == "complete" and done["result"]["duration"] > 5
        src = PATHS.sources / done["result"]["upload_token"]
        assert src.read_bytes() == data, "the source is byte-identical"
        assert not (uploads.root() / s["upload_id"] / "data.part").exists(), "moved, not copied"
        again = c.post(f"/api/uploads/{s['upload_id']}/complete").json()
        assert again["result"]["upload_token"] == done["result"]["upload_token"], "complete is idempotent"


def test_bad_chunks_are_never_marked_received():
    data = video()
    with client() as c:
        s = start(c, data)
        u = s["upload_id"]
        part = chunks(data)[1]
        # truncated (an interrupted request)
        r = c.put(f"/api/uploads/{u}/chunks/1", content=part[:1000])
        assert r.status_code == 400 and r.json()["detail"]["code"] == "upload_bad_chunk_size"
        # damaged
        r = c.put(f"/api/uploads/{u}/chunks/1", content=part, headers={"X-Chunk-Sha256": "0" * 64})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "upload_checksum"
        # out of range / too long
        assert c.put(f"/api/uploads/{u}/chunks/9999", content=b"x").status_code == 400
        assert c.put(f"/api/uploads/{u}/chunks/1", content=part + b"x").status_code == 400
        assert 1 not in c.get(f"/api/uploads/{u}").json()["received"]


def test_missing_chunks_are_detected_and_nothing_becomes_a_source():
    data = video()
    with client() as c:
        s = start(c, data)
        parts = chunks(data)
        for i, p in enumerate(parts):
            if i != 2:
                c.put(f"/api/uploads/{s['upload_id']}/chunks/{i}", content=p)
        before = set(PATHS.sources.glob("*")) if PATHS.sources.exists() else set()
        r = c.post(f"/api/uploads/{s['upload_id']}/complete")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "upload_incomplete"
        assert (set(PATHS.sources.glob("*")) if PATHS.sources.exists() else set()) == before
        # creating a project from it is refused too
        r = c.post("/api/projects", json={"source": {"type": "upload", "upload_id": s["upload_id"]},
                                         "ui_language": "en", "content_language": "auto"})
        assert r.status_code == 409


def test_resume_after_refresh_continues_the_same_session():
    data = video()
    fp = f"stream.mp4|{len(data)}|1712345678000"
    with client() as c:
        s = start(c, data, fp=fp)
        parts = chunks(data)
        for i in range(3):
            c.put(f"/api/uploads/{s['upload_id']}/chunks/{i}", content=parts[i])
        # the page was refreshed: the browser starts again with the same file
        again = start(c, data, fp=fp)
        assert again["upload_id"] == s["upload_id"] and again["received"] == [0, 1, 2]
        assert again["bytes_received"] == 3 * CH
        for i in again["missing"]:
            c.put(f"/api/uploads/{s['upload_id']}/chunks/{i}", content=parts[i])
        assert c.post(f"/api/uploads/{s['upload_id']}/complete").json()["status"] == "complete"
        # choosing the same file after it finished: the finished upload, not a new one
        assert start(c, data, fp=fp)["status"] == "complete"


def test_invalid_video_is_rejected_and_its_bytes_are_dropped():
    junk = os.urandom(3 * CH + 17)
    with client() as c:
        s = start(c, junk, name="fake.mp4")
        for i, p in enumerate(chunks(junk)):
            c.put(f"/api/uploads/{s['upload_id']}/chunks/{i}", content=p)
        r = c.post(f"/api/uploads/{s['upload_id']}/complete")
        assert r.status_code == 422 and r.json()["detail"]["code"] == "upload_invalid_video"
        assert c.get(f"/api/uploads/{s['upload_id']}").json()["status"] == "failed"
        assert not (uploads.root() / s["upload_id"] / "data.part").exists()
        # a wrong extension is refused before any byte is sent
        assert c.post("/api/uploads", json={"filename": "x.exe", "size": 10}).status_code == 400


def test_not_enough_disk_space_is_refused_before_uploading():
    import shutil

    real = shutil.disk_usage
    try:
        shutil.disk_usage = lambda p: real(p)._replace(free=3 * 1024 ** 3)   # 3 GB free, 2 GB margin
        with client() as c:
            r = c.post("/api/uploads", json={"filename": "big.mp4", "size": 4 * 1024 ** 3})
            assert r.status_code == 507 and r.json()["detail"]["code"] == "upload_no_space"
            assert "GB" in r.json()["detail"]["message"]
            assert c.post("/api/uploads", json={"filename": "ok.mp4", "size": 500 * 1024 ** 2}).status_code == 200
    finally:
        shutil.disk_usage = real


def test_cancel_and_cleanup_never_touch_finished_sources():
    data = video()
    with client() as c:
        s = start(c, data)
        c.put(f"/api/uploads/{s['upload_id']}/chunks/0", content=chunks(data)[0])
        assert c.delete(f"/api/uploads/{s['upload_id']}").status_code == 200
        assert not (uploads.root() / s["upload_id"]).exists()
        assert c.get(f"/api/uploads/{s['upload_id']}").status_code == 404
        # path traversal in the id is just "not found"
        assert c.get("/api/uploads/..%2F..%2Fetc").status_code == 404
        # an abandoned session expires after the TTL; a recent one and a finished source stay
        old = start(c, data)
        recent = start(c, data[:-1])
        done = start(c, data)
        for i, p in enumerate(chunks(data)):
            c.put(f"/api/uploads/{done['upload_id']}/chunks/{i}", content=p)
        token = c.post(f"/api/uploads/{done['upload_id']}/complete").json()["result"]["upload_token"]
        out = uploads.cleanup(now=time.time() + uploads.SESSION_TTL + 60)
        assert not (uploads.root() / old["upload_id"]).exists() and not (uploads.root() / recent["upload_id"]).exists()
        assert out["removed"] >= 2
        assert (PATHS.sources / token).exists(), "a finished source is never cleaned"
        assert (uploads.root() / done["upload_id"]).exists(), "finished session metadata kept for a week"


def test_one_upload_session_is_one_project():
    data = video()
    with client() as c:
        s = start(c, data)
        for i, p in enumerate(chunks(data)):
            c.put(f"/api/uploads/{s['upload_id']}/chunks/{i}", content=p)
        c.post(f"/api/uploads/{s['upload_id']}/complete")
        body = {"source": {"type": "upload", "upload_id": s["upload_id"]}, "ui_language": "en",
                "content_language": "auto"}
        a = c.post("/api/projects", json=body)
        b = c.post("/api/projects", json=body)                 # a retried Start
        assert a.status_code == 200 and b.status_code == 200 and a.json()["id"] == b.json()["id"]
        c.post(f"/api/projects/{a.json()['id']}/cancel")


def test_multi_gb_logical_upload_without_a_huge_fixture():
    """A 6 GB session: preallocated sparse (no disk used for bytes not sent), chunks land at their offsets."""
    import shutil

    size = 6 * 1024 ** 3 + 12345
    real = shutil.disk_usage
    try:
        shutil.disk_usage = lambda p: real(p)._replace(free=100 * 1024 ** 3)
        with client() as c:
            s = c.post("/api/uploads", json={"filename": "long_stream.mp4", "size": size}).json()
    finally:
        shutil.disk_usage = real
    n = s["total_chunks"]
    assert n == -(-size // CH)
    last = size - (n - 1) * CH
    with client() as c:
        u = s["upload_id"]
        assert c.put(f"/api/uploads/{u}/chunks/0", content=b"A" * CH).status_code == 200
        assert c.put(f"/api/uploads/{u}/chunks/{n - 1}", content=b"Z" * last).status_code == 200
        st = c.get(f"/api/uploads/{u}").json()
        assert st["bytes_received"] == CH + last and len(st["missing"]) == min(2000, n - 2)
        part = uploads.root() / u / "data.part"
        assert part.stat().st_size == size
        assert part.stat().st_blocks * 512 < 64 * 1024 ** 2, "sparse: only the sent bytes use disk"
        with part.open("rb") as f:
            f.seek(size - last)
            assert f.read(4) == b"ZZZZ"
        r = c.post(f"/api/uploads/{u}/complete")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "upload_incomplete"
        c.delete(f"/api/uploads/{u}")


def test_a_chunk_streams_to_disk_without_buffering_it_in_ram():
    """A real server process receives 64 MB chunks: its peak memory must not grow by a chunk."""
    import http.client
    import json as _json
    import socket

    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    data_dir = tempfile.mkdtemp(prefix="pxupram_")
    env = {**os.environ, "POLIXOR_DATA_DIR": data_dir, "POLIXOR_UPLOAD_CHUNK_BYTES": str(64 * 1024 ** 2),
           "POLIXOR_SCHEDULER": "0", "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    env.pop("POLIXOR_ACCESS_PASSWORD", None)
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "polixor.main:app", "--host", "127.0.0.1",
                             "--port", str(port), "--log-level", "warning"],
                            cwd=str(Path(__file__).resolve().parents[1]), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def hwm() -> int:
        for line in Path(f"/proc/{proc.pid}/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
        return 0
    try:
        for _ in range(80):
            try:
                http.client.HTTPConnection("127.0.0.1", port, timeout=1).request("GET", "/api/health")
                break
            except OSError:
                time.sleep(0.25)
        size = 3 * 64 * 1024 ** 2
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
        conn.request("POST", "/api/uploads", body=_json.dumps({"filename": "ram.mp4", "size": size}),
                     headers={"Content-Type": "application/json"})
        s = _json.loads(conn.getresponse().read())
        base = hwm()
        for i in range(3):
            def gen():
                block = bytes([65 + i]) * (1024 * 1024)
                for _ in range(64):
                    yield block
            conn.request("PUT", f"/api/uploads/{s['upload_id']}/chunks/{i}", body=gen(),
                         headers={"Content-Length": str(64 * 1024 ** 2), "Content-Type": "application/octet-stream"})
            r = conn.getresponse()
            assert r.status == 200, r.read()
            r.read()
        grew = hwm() - base
        assert grew < 40 * 1024 ** 2, f"server peak memory grew by {grew / 1e6:.0f} MB for 64 MB chunks"
        part = Path(data_dir) / "uploads" / s["upload_id"] / "data.part"
        with part.open("rb") as f:
            f.seek(2 * 64 * 1024 ** 2)
            assert f.read(3) == b"CCC"
    finally:
        proc.terminate()
        proc.wait(timeout=30)


# --------------------------------------------------------------------------
def _run_all() -> int:
    fns = [(n, f) for n, f in list(globals().items()) if n.startswith("test_") and callable(f)]
    passed, failed = 0, []
    for name, fn in fns:
        try:
            fn()
            print(f"  \033[92m✓\033[0m {name}")
            passed += 1
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"  \033[91m✗\033[0m {name}: {type(exc).__name__}: {exc}")
            failed.append(name)
    print(f"\n{passed}/{len(fns)} upload tests passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
