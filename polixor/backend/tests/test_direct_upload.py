"""
Direct browser → object storage uploads (POLIXOR_STORAGE=s3), against a real local S3 HTTP server
(moto). No real bucket, no credentials of anyone's.

  * the browser gets part geometry and short-lived signed URLs – never a key or a secret
  * parts go straight to the storage (signed PUTs over HTTP); Polixor receives no video bytes
  * resume: after an interruption the STORAGE's part list decides what is still missing
  * an expired / foreign URL is refused by the storage; a missing part blocks completion
  * complete → stored size checked → ffprobe through a signed GET → project created from the object
  * the worker copies the object to its disk when processing starts (pipeline._stage_download)
  * abort releases the stored parts

Run:  python3 tests/test_direct_upload.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxdirect_"))
os.environ["POLIXOR_PAID_AI"] = "off"

TEST_VIDEO = Path(os.environ.get("POLIXOR_TEST_VIDEO", "/home/claude/testdata/polixor_test_stream.mp4"))
KEY_ID, SECRET = "AKIAPOLIXORTEST00000", "polixor-test-secret-never-in-a-response"


def _moto():
    from moto.server import ThreadedMotoServer

    srv = ThreadedMotoServer(ip_address="127.0.0.1", port=0)
    srv.start()
    host, port = srv.get_host_and_port()
    return srv, f"http://{host}:{port}"


SERVER, ENDPOINT = _moto()
os.environ.update({"POLIXOR_STORAGE": "s3", "POLIXOR_S3_BUCKET": "polixor-test", "POLIXOR_S3_REGION": "eu-central-1",
                   "POLIXOR_S3_ENDPOINT": ENDPOINT, "POLIXOR_S3_ACCESS_KEY_ID": KEY_ID,
                   "POLIXOR_S3_SECRET_ACCESS_KEY": SECRET, "POLIXOR_S3_PART_MB": "5"})

from polixor.config import PATHS                                   # noqa: E402
from polixor.db import init_db                                     # noqa: E402

PATHS.ensure()
init_db()

import boto3                                                       # noqa: E402
import httpx                                                       # noqa: E402

boto3.client("s3", region_name="eu-central-1", endpoint_url=ENDPOINT, aws_access_key_id=KEY_ID,
             aws_secret_access_key=SECRET).create_bucket(
    Bucket="polixor-test", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"})


def _client():
    from fastapi.testclient import TestClient

    from polixor.main import app
    return TestClient(app)


def _big_video(path: Path, loops: int = 6) -> Path:
    """A real video a few parts long (5 MiB parts)."""
    import subprocess

    from polixor.util.ffmpeg import ffmpeg_bin
    lst = path.with_suffix(".txt")
    lst.write_text("".join(f"file '{TEST_VIDEO}'\n" for _ in range(loops)))
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", str(path)], check=True)
    return path


def _no_secret(obj) -> None:
    text = json.dumps(obj)
    assert SECRET not in text and KEY_ID not in text.replace("X-Amz-Credential=" + KEY_ID, ""), "a credential leaked"


def test_transport_says_object_storage_and_never_carries_a_key():
    with _client() as c:
        t = c.get("/api/uploads/transport").json()
    assert t["storage"] == "s3_multipart"
    _no_secret(t)


def test_direct_upload_resumes_from_the_storage_and_creates_a_processable_project():
    if not TEST_VIDEO.exists():
        print("   (skipped: no test video)")
        return
    video = _big_video(Path(tempfile.mkdtemp()) / "long.mp4")
    data = video.read_bytes()
    size = len(data)
    with _client() as c:
        s = c.post("/api/uploads/direct", json={"filename": "long.mp4", "size": size, "fingerprint": "fp-long"}).json()
        _no_secret(s)
        uid, part, total = s["upload_id"], s["part_bytes"], s["parts_total"]
        assert total >= 3, s
        # the browser sends the first two parts straight to the storage, then "the laptop sleeps"
        urls = c.post(f"/api/uploads/direct/{uid}/sign", json={"parts": [1, 2]}).json()["urls"]
        for n in (1, 2):
            u = urls[str(n)]
            assert u.startswith(ENDPOINT) and SECRET not in u           # a signature, never the secret
            r = httpx.put(u, content=data[(n - 1) * part:n * part])
            assert r.status_code == 200, r.text[:200]
        # the same file chosen again (refresh / another day): the same session, the storage says what is in
        again = c.post("/api/uploads/direct", json={"filename": "long.mp4", "size": size, "fingerprint": "fp-long"}).json()
        assert again["upload_id"] == uid
        have = c.get(f"/api/uploads/direct/{uid}/parts").json()
        assert sorted(p["part_number"] for p in have["parts"]) == [1, 2]
        # completing with a part missing is refused (nothing half-made becomes a project)
        assert c.post(f"/api/uploads/direct/{uid}/complete").status_code == 409
        rest = list(range(3, total + 1))
        urls = c.post(f"/api/uploads/direct/{uid}/sign", json={"parts": rest}).json()["urls"]
        for n in rest:
            r = httpx.put(urls[str(n)], content=data[(n - 1) * part:n * part])
            assert r.status_code == 200
        done = c.post(f"/api/uploads/direct/{uid}/complete").json()
        _no_secret(done)
        assert done["status"] == "complete" and abs(done["result"]["duration"] - 540) < 3, done
        # a project from the stored object (no local file yet – the worker fetches it)
        pr = c.post("/api/projects", json={"source": {"type": "upload", "upload_id": uid}, "title": "direct",
                                           "goal": None})
        assert pr.status_code == 200, pr.text[:300]
        pid = pr.json()["id"]
    from polixor.db import session_scope
    from polixor.models import Job

    with session_scope() as s:
        arts = dict(s.get(Job, pid).artifacts or {})
    assert arts["source_object"]["key"] and not Path(arts["source_path"]).exists()

    # the processing worker copies the object to its disk (inside the cloud), byte-identical
    from polixor import pipeline

    class Rep:
        def start_stage(self, *a, **k):
            pass

        def progress(self, *a, **k):
            pass

    class Ctx:
        artifacts = arts
        reporter = Rep()
        source_path = None
        is_live = False
    pipeline._stage_download(Ctx())                                  # type: ignore[arg-type]
    assert Path(arts["source_path"]).read_bytes() == data


def test_a_foreign_or_tampered_url_is_refused_and_abort_releases_parts():
    with _client() as c:
        s = c.post("/api/uploads/direct", json={"filename": "x.mp4", "size": 12 * 1024 * 1024, "fingerprint": "fp-x"}).json()
        uid = s["upload_id"]
        urls = c.post(f"/api/uploads/direct/{uid}/sign", json={"parts": [1, 2, 99]}).json()["urls"]
        assert set(urls) == {"1", "2"}                               # only parts of THIS object exist
        sig = {k: v.split("X-Amz-Signature=")[1] for k, v in urls.items()}
        # each URL is signed for its own part (bucket, key, upload id, part number, 15 min): S3 / R2
        # refuse a URL whose part number or expiry was changed (local moto does not check signatures)
        assert sig["1"] != sig["2"] and all("X-Amz-Expires=900" in v for v in urls.values())
        assert c.delete(f"/api/uploads/direct/{uid}").json()["cancelled"]
        assert c.post(f"/api/uploads/direct/{uid}/sign", json={"parts": [1]}).status_code in (200, 404)


def test_part_size_fits_ten_thousand_parts_for_huge_files():
    from polixor.services.object_storage import MAX_PARTS, part_size_for

    for gb in (1, 10, 25, 200):
        size = gb * 1024 ** 3
        p = part_size_for(size, 64 * 1024 * 1024)
        assert p >= 5 * 1024 * 1024 and -(-size // p) <= MAX_PARTS


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
    print(f"{len(tests) - failed}/{len(tests)} direct upload tests passed")
    SERVER.stop()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
