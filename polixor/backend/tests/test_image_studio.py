"""
בדיקות לסטודיו התמונות (שלב 11). OpenAI מדומה ב-httpx.MockTransport – שום
בקשה לא יוצאת מהמחשב, וכל בקשה נבדקת (נתיב, מודל, מידות, תמונות קלט).

  * שכבת היכולות: ברירת המחדל היא המודל החזק ביותר; מידות חוקיות; איכות
    מותאמת למודל; מודלים ישנים לא מוצגים; dall-e-3 בלי עריכה.
  * שיחה: יצירה → עריכה חוזרת של התמונה האחרונה (/images/edits) עם ההקשר;
    "תמונה חדשה" → /images/generations; היסטוריה נשמרת ומסודרת.
  * ייחוסים: העלאה נבדקת ומקודדת מחדש; כמה תמונות קלט בבקשה אחת; מגבלת
    המודל נאכפת; קבצים לא תקינים נדחים.
  * כשל ודחייה → הודעה ברורה + ניסיון חוזר; המפתח לא יוצא בשום תשובה.
  * הוספה לסרטון: שיבוץ (B-roll וכו') ותמונת שער לקליפ (JPEG ≤2MB, ביחס הקליפ).
  * API: CSRF; הודעות בעברית ובאנגלית; עובד גם בלי מפתח (כרטיס מקומי, לא AI).

הרצה:  python3 tests/test_image_studio.py
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
DATA = tempfile.mkdtemp(prefix="pxstudio_")
os.environ["POLIXOR_DATA_DIR"] = DATA
os.environ["POLIXOR_SCHEDULER"] = "0"

import httpx                                                         # noqa: E402

from polixor import i18n                                             # noqa: E402
from polixor.config import PATHS, SECRETS, SETTINGS                  # noqa: E402
from polixor.db import init_db, session_scope                        # noqa: E402
from polixor.models import (Clip, ClipKind, ClipStatus, GeneratedImage, ImagePlacement,  # noqa: E402
                            ImageStatus, Job, JobStatus, new_id)
from polixor.services import image_models, image_studio, images      # noqa: E402

PATHS.ensure()
init_db()
H = {"X-Polixor-Request": "1"}
FAKE_KEY = "sk-test-not-a-real-key-0123456789"


def png(w=64, h=64, color=(200, 40, 40), fmt="PNG") -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, fmt)
    return buf.getvalue()


def noisy(w: int, h: int) -> bytes:
    """תמונה שלא נדחסת לכלום (הספק דוחה תשובה קטנה מ-512 בתים כ"ריקה")."""
    from PIL import Image

    buf = io.BytesIO()
    Image.frombytes("RGB", (w, h), os.urandom(w * h * 3)).save(buf, "PNG")
    return buf.getvalue()


class FakeOpenAI:
    """מדמה את Images API; רושם כל בקשה (נתיב, שדות, מספר תמונות קלט)."""

    def __init__(self):
        self.calls: list[dict] = []
        self.fail_next: list[tuple[int, dict]] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        assert req.url.host == "api.openai.com", req.url
        assert req.headers["authorization"] == f"Bearer {FAKE_KEY}"
        call = {"path": req.url.path}
        ctype = req.headers.get("content-type", "")
        if ctype.startswith("application/json"):
            call["body"] = json.loads(req.content)
        else:
            body = req.content.decode("latin-1")
            call["images"] = body.count('name="image[]"')
            fields = {}
            for part in body.split("--")[1:]:
                if 'name="' in part and "filename=" not in part:
                    name = part.split('name="', 1)[1].split('"', 1)[0]
                    fields[name] = part.split("\r\n\r\n", 1)[-1].rsplit("\r\n", 1)[0]
            call["body"] = fields
        self.calls.append(call)
        if self.fail_next:
            code, payload = self.fail_next.pop(0)
            return httpx.Response(code, json=payload)
        size = (call["body"].get("size") or "1024x1024").split("x")
        img = noisy(int(size[0]) // 16, int(size[1]) // 16)
        return httpx.Response(200, json={"created": 1, "data": [{"b64_json": base64.b64encode(img).decode()}],
                                         "usage": {"total_tokens": 10}})


FAKE = FakeOpenAI()
images.OpenAIImageProvider.transport = httpx.MockTransport(FAKE.handler)


def _openai(**kw):
    SECRETS.set("openai_api_key", FAKE_KEY)
    SETTINGS.update({"image_provider": "openai", "image_model": image_models.DEFAULT_MODEL,
                     "image_quality": "high", "image_retries": 0, **kw})


def _client():
    from fastapi.testclient import TestClient

    from polixor.main import app
    return TestClient(app, base_url="http://testserver")


def _wait(image_id: str, timeout=20.0) -> GeneratedImage:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with session_scope() as s:
            row = s.get(GeneratedImage, image_id)
            if row.status not in (ImageStatus.QUEUED, ImageStatus.GENERATING):
                s.expunge(row)
                return row
        time.sleep(0.05)
    raise AssertionError("image did not finish")


def _clip(w=1080, h=1920) -> str:
    jid, cid = new_id(), new_id()
    media = Path(DATA) / f"{cid}.mp4"
    media.write_bytes(b"\x00" * 1024)
    with session_scope() as s:
        s.add(Job(id=jid, title="Stream", status=JobStatus.COMPLETED, artifacts={}, completed_stages=[]))
        s.flush()
        s.add(Clip(id=cid, job_id=jid, kind=ClipKind.SHORT, status=ClipStatus.READY, title="c",
                   file_path=str(media), file_size=1024, duration=20.0, width=w, height=h))
    return cid


# --------------------------------------------------------------------------
def test_capability_layer():
    m = image_models.get("")
    assert m.id == "gpt-image-2.5-sunburst" and image_models.DEFAULT_MODEL == m.id
    ids = [x.id for x in image_models.selectable("openai")]
    assert ids[:2] == ["gpt-image-2.5-sunburst", "gpt-image-2.5-flare"]
    assert "dall-e-3" not in ids and "dall-e-2" not in ids
    for mid in ("gpt-image-2.5-sunburst", "gpt-image-2.5-flare", "gpt-image-2"):
        mm = image_models.get(mid)
        assert mm.edit and mm.max_refs == 16
        for aspect in ("1:1", "16:9", "9:16"):
            w, h = (int(x) for x in image_models.size_for(mm, aspect).split("x"))
            assert w % 16 == 0 and h % 16 == 0 and max(w, h) <= 3840
            assert 1 / 3 <= w / h <= 3 and 655_360 <= w * h <= 8_294_400
            assert min(w, h) >= 1080                    # באיכות וידאו
    assert image_models.quality_for(image_models.get("gpt-image-2"), "max") == "high"
    assert image_models.quality_for(image_models.get("gpt-image-2.5-flare"), "xhigh") == "xhigh"
    assert image_models.quality_for(image_models.get("gpt-image-1"), "xhigh") == "high"
    assert image_models.quality_for(image_models.get("dall-e-3"), "high") == "hd"
    assert not image_models.get("dall-e-3").edit
    assert image_models.get("gpt-image-2").transparent_preview


def test_generation_request_uses_model_capabilities():
    _openai(image_quality="max")
    FAKE.calls.clear()
    data = images.generate_image("a red fox in the snow", aspect="9:16", settings=SETTINGS.get(),
                                 background="transparent")
    call = FAKE.calls[-1]
    assert call["path"] == "/v1/images/generations"
    b = call["body"]
    assert b["model"] == "gpt-image-2.5-sunburst" and b["size"] == "1152x2048"
    assert b["quality"] == "max" and b["output_format"] == "png" and b["background"] == "transparent"
    assert "response_format" not in b and data.model == "gpt-image-2.5-sunburst" and data.is_ai
    # מודל ישן יותר: האיכות יורדת לנתמכת, בלי פרמטרים שהוא לא מכיר
    SETTINGS.update({"image_model": "gpt-image-2"})
    images.generate_image("a red fox", aspect="16:9", settings=SETTINGS.get())
    b = FAKE.calls[-1]["body"]
    assert b["model"] == "gpt-image-2" and b["quality"] == "high" and b["size"] == "2048x1152"
    assert "background" not in b
    _openai()


def test_conversation_generate_then_iterative_edit():
    _openai()
    FAKE.calls.clear()
    th = image_studio.create_thread()
    first = image_studio.send(th["id"], "A cozy cabin in a forest", aspect="16:9")
    img1 = _wait(first["assistant"]["image_id"])
    assert img1.status == ImageStatus.READY and img1.width and Path(img1.file_path).exists()
    assert FAKE.calls[-1]["path"] == "/v1/images/generations"
    assert FAKE.calls[-1]["body"]["prompt"] == "A cozy cabin in a forest"

    second = image_studio.send(th["id"], "Make it night with snow")
    img2 = _wait(second["assistant"]["image_id"])
    call = FAKE.calls[-1]
    assert call["path"] == "/v1/images/edits" and call["images"] == 1
    prompt = call["body"]["prompt"]
    assert "Make it night with snow" in prompt and "A cozy cabin in a forest" in prompt
    assert call["body"]["model"] == "gpt-image-2.5-sunburst" and call["body"]["size"] == "2048x1152"
    assert img2.parent_id == img1.id and img2.prompt == "Make it night with snow"
    assert second["assistant"]["mode"] == "edit" and img2.aspect == "16:9"   # יחס נשמר בעריכה

    third = image_studio.send(th["id"], "Add a moose", mode="edit")
    _wait(third["assistant"]["image_id"])
    p3 = FAKE.calls[-1]["body"]["prompt"]
    assert "Make it night with snow" in p3                    # כל ההוראות הקודמות בהקשר

    fresh = image_studio.send(th["id"], "A beach at sunset", mode="new", aspect="1:1")
    _wait(fresh["assistant"]["image_id"])
    assert FAKE.calls[-1]["path"] == "/v1/images/generations"
    assert FAKE.calls[-1]["body"]["size"] == "1536x1536"

    t = image_studio.get_thread(th["id"])
    assert [m["role"] for m in t["messages"]] == ["user", "assistant"] * 4
    assert t["title"] == "A cozy cabin in a forest"
    assert t["cover_image_id"] == fresh["assistant"]["image_id"]


def test_attachments_and_reference_limits():
    _openai()
    FAKE.calls.clear()
    a = image_studio.upload(png(300, 200), "logo.png")
    b = image_studio.upload(png(100, 100, fmt="JPEG"), "face.jpg")
    with session_scope() as s:
        row = s.get(GeneratedImage, a)
        assert row.provider == "upload" and not row.is_ai and row.status == ImageStatus.READY
        assert Path(row.file_path).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"   # קודד מחדש ל-PNG
    th = image_studio.create_thread()
    first = image_studio.send(th["id"], "Poster with this logo", attachments=[a])
    _wait(first["assistant"]["image_id"])
    assert FAKE.calls[-1]["path"] == "/v1/images/edits" and FAKE.calls[-1]["images"] == 1
    assert "references" in FAKE.calls[-1]["body"]["prompt"]
    second = image_studio.send(th["id"], "Put this person next to the logo", attachments=[b, a])
    _wait(second["assistant"]["image_id"])
    assert FAKE.calls[-1]["images"] == 3                       # התמונה הנוכחית + שני ייחוסים
    # מגבלת המודל (הכרטיס המקומי: עד 4)
    SETTINGS.update({"image_provider": "placeholder"})
    many = [image_studio.upload(png(), f"r{i}.png") for i in range(4)]
    try:
        image_studio.send(th["id"], "too many", attachments=many)
        raise AssertionError("expected too_many_refs")
    except image_studio.StudioError as exc:
        assert exc.message_key == "image_studio.error.too_many_refs"
    _openai()


def test_upload_validation():
    for raw, key in ((b"", "upload_empty"), (b"not an image at all" * 10, "upload_invalid"),
                     (png(fmt="GIF"), "upload_type")):
        try:
            image_studio.upload(raw, "x")
            raise AssertionError(key)
        except image_studio.StudioError as exc:
            assert exc.message_key == f"image_studio.error.{key}", exc.message_key
    saved = image_studio.MAX_UPLOAD_BYTES
    image_studio.MAX_UPLOAD_BYTES = 100
    try:
        image_studio.upload(png(), "big.png")
        raise AssertionError("expected too big")
    except image_studio.StudioError as exc:
        assert exc.message_key == "image_studio.error.upload_too_big"
    finally:
        image_studio.MAX_UPLOAD_BYTES = saved
    big = image_studio.upload(png(5000, 100), "wide.png")        # מוקטן לצלע של 4096
    with session_scope() as s:
        assert s.get(GeneratedImage, big).width == 4096


def test_unsupported_options_are_refused():
    _openai(image_model="dall-e-3")
    th = image_studio.create_thread()
    ref = image_studio.upload(png(), "r.png")
    for kw, key in (({"attachments": [ref]}, "no_edit"), ({"background": "transparent"}, "no_transparent"),
                    ({"mode": "edit"}, "nothing_to_edit"), ({"mode": "zoom"}, "bad_mode")):
        try:
            image_studio.send(th["id"], "a lighthouse", **kw)
            raise AssertionError(key)
        except image_studio.StudioError as exc:
            assert exc.message_key == f"image_studio.error.{key}", (key, exc.message_key)
    caps = image_studio.capabilities()
    assert "transparent" not in caps["backgrounds"] and caps["model"]["max_refs"] == 0
    _openai()
    assert "transparent" in image_studio.capabilities()["backgrounds"]


def test_failure_then_retry():
    _openai()
    th = image_studio.create_thread()
    FAKE.fail_next.append((400, {"error": {"code": "content_policy_violation", "message": "rejected"}}))
    out = image_studio.send(th["id"], "something the provider rejects")
    row = _wait(out["assistant"]["image_id"])
    assert row.status == ImageStatus.FAILED and row.error
    image_studio.retry(out["assistant"]["id"])
    row = _wait(out["assistant"]["image_id"])
    assert row.status == ImageStatus.READY


def test_api_flow_csrf_and_no_key_leak():
    _openai()
    with _client() as c:
        assert c.post("/api/image-studio/threads", json={}).status_code == 403
        caps = c.get("/api/image-studio/capabilities").json()
        assert caps["model"]["id"] == "gpt-image-2.5-sunburst" and caps["ready"] is True
        th = c.post("/api/image-studio/threads", json={}, headers=H).json()
        up = c.post("/api/image-studio/uploads", files={"file": ("a.png", png(), "image/png")}, headers=H)
        assert up.status_code == 201 and up.json()["is_ai"] is False
        r = c.post(f"/api/image-studio/threads/{th['id']}/messages",
                   json={"text": "A neon city skyline", "attachments": [up.json()["id"]], "aspect": "9:16"},
                   headers=H)
        assert r.status_code == 202, r.text
        _wait(r.json()["assistant"]["image_id"])
        full = c.get(f"/api/image-studio/threads/{th['id']}").json()
        assert len(full["messages"]) == 2 and full["images"][r.json()["assistant"]["image_id"]]["status"] == "ready"
        listing = c.get("/api/image-studio/threads").json()["threads"]
        assert listing[0]["id"] == th["id"] and listing[0]["cover"]["id"]
        gallery = [i["id"] for i in c.get("/api/images").json()]
        assert up.json()["id"] not in gallery                  # העלאות לא בגלריה
        for body in (caps, full, listing, gallery):
            assert FAKE_KEY not in json.dumps(body)
        bad = c.post(f"/api/image-studio/threads/{th['id']}/messages", json={"text": "x y z", "mode": "zoom"},
                     headers={**H, "X-Polixor-Lang": "he"})
        assert bad.status_code == 400 and "מצב לא מוכר" in bad.json()["detail"]["message"]
        bad = c.post(f"/api/image-studio/threads/{th['id']}/messages", json={"text": "x y z", "mode": "zoom"},
                     headers={**H, "X-Polixor-Lang": "en"})
        assert "Unknown mode" in bad.json()["detail"]["message"]
        assert c.get("/api/image-studio/threads/nope").status_code == 404
        assert c.patch(f"/api/image-studio/threads/{th['id']}", json={"title": "Skyline"}, headers=H).json()["title"] == "Skyline"
        assert c.delete(f"/api/image-studio/threads/{th['id']}", headers=H).json()["deleted"]
        with session_scope() as s:                             # התמונות נשארות אחרי מחיקת השיחה
            assert s.get(GeneratedImage, r.json()["assistant"]["image_id"]) is not None


def test_add_to_video_and_thumbnail():
    _openai()
    th = image_studio.create_thread()
    out = image_studio.send(th["id"], "Bold intro card", aspect="9:16")
    iid = _wait(out["assistant"]["image_id"]).id
    cid = _clip()
    with _client() as c:
        for role in ("intro", "outro", "broll", "overlay", "background"):
            r = c.post(f"/api/clips/{cid}/images", json={"image_id": iid, "role": role, "at_time": 2,
                                                         "duration": 3}, headers=H)
            assert r.status_code == 201, (role, r.text)
        assert c.post("/api/image-studio/thumbnail", json={"clip_id": cid, "image_id": iid}).status_code == 403
        r = c.post("/api/image-studio/thumbnail", json={"clip_id": cid, "image_id": iid}, headers=H)
        assert r.status_code == 200, r.text
        r = c.post("/api/image-studio/thumbnail", json={"clip_id": cid, "image_id": iid}, headers=H)
        assert c.get(f"/api/clips/{cid}/thumbnail").status_code == 200
    from PIL import Image

    with session_scope() as s:
        path = Path(s.get(Clip, cid).thumbnail_path)
        thumbs = s.query(ImagePlacement).filter(ImagePlacement.clip_id == cid,
                                                ImagePlacement.role == "thumbnail").count()
    assert thumbs == 1                                         # תמונת שער אחת לקליפ
    with Image.open(path) as im:
        assert im.format == "JPEG" and im.size == (720, 1280)
    assert path.stat().st_size <= 2 * 1024 * 1024


def test_custom_thumbnail_survives_re_export():
    from polixor.api.routes_clips import keep_custom_thumbnail

    (PATHS.exports / "thumbnails").mkdir(parents=True, exist_ok=True)
    custom = PATHS.exports / "thumbnails" / "clipthumb_x_y.jpg"
    custom.write_bytes(b"\xff\xd8" + b"0" * 100)
    auto = Path(DATA) / "auto.jpg"
    auto.write_bytes(b"1")
    assert keep_custom_thumbnail(str(custom), str(auto)) == str(custom)
    assert keep_custom_thumbnail(str(auto), str(auto)) == str(auto)
    custom.unlink()
    assert keep_custom_thumbnail(str(custom), str(auto)) == str(auto)


def test_works_without_key_as_local_cards():
    SECRETS.delete("openai_api_key")
    SETTINGS.update({"image_provider": "placeholder"})
    caps = image_studio.capabilities()
    assert caps["ready"] and caps["is_ai"] is False and caps["model"]["id"] == "placeholder"
    th = image_studio.create_thread()
    one = image_studio.send(th["id"], "Intro card for my stream")
    row = _wait(one["assistant"]["image_id"])
    assert row.status == ImageStatus.READY and not row.is_ai
    ref = image_studio.upload(png(), "r.png")
    two = image_studio.send(th["id"], "Add this", attachments=[ref])
    row = _wait(two["assistant"]["image_id"])
    assert row.status == ImageStatus.READY and not row.is_ai and row.note
    SETTINGS.update({"image_provider": "openai"})
    assert image_studio.capabilities()["ready"] is False       # אין מפתח → הסטודיו חסום עם סיבה
    assert image_studio.capabilities()["reason"]


def test_locale_parity():
    import re

    from polixor.locales import image_studio as L
    for key, v in L.MESSAGES.items():
        assert v.get("he") and v.get("en"), key
        assert set(re.findall(r"{(\w+)}", v["he"])) == set(re.findall(r"{(\w+)}", v["en"])), key
    assert i18n.tr("image_studio.error.too_many_refs", "he", max=16).find("16") >= 0


# --------------------------------------------------------------------------
def _run_all() -> int:
    fns = [(n, globals()[n]) for n in list(globals()) if n.startswith("test_")]
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
    print(f"\n{passed}/{len(fns)} בדיקות סטודיו התמונות עברו")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
