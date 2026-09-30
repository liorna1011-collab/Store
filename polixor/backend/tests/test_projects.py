"""
בדיקות לזרימת הפרויקטים ב-API: Import → Analyze → Configure → Generate.

רצות מול השרת האמיתי (FastAPI TestClient עם מנהל המשימות), על וידאו
הבדיקה הסינתטי ותמלול fixture. כוללות:
  * מיגרציה של DB מהגרסה שלפני השדרוג (סכמה אמיתית, tests/fixtures).
  * זרימה מלאה, PATCH, רשימה וסינון לפי שלב.
  * יצירה מחדש בלי כפילויות, 409 בזמן יצירה, 400 כשהניתוח חסר.
  * ביטול, מחיקה עם קבצים, הגשה מחדש של משימה QUEUED אחרי הפעלה מחדש.
  * שפת הבקשה: Accept-Language, ‏X-Polixor-Lang, ‏?lang, ושגיאה שנשמרה
    ומוצגת בכל שפה.

הרצה:  python3 tests/test_projects.py [path/to/polixor_test_stream.mp4]
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxproj_"))
DATA = Path(os.environ["POLIXOR_DATA_DIR"])


def _video() -> Path:
    arg = next((a for a in sys.argv[1:] if a.endswith(".mp4")), None)
    for cand in ([Path(arg)] if arg else []) + [
            Path("/home/claude/testdata/polixor_test_stream.mp4"),
            DATA / "testdata" / "polixor_test_stream.mp4"]:
        if cand.exists():
            return cand
    out = DATA / "testdata"
    subprocess.run([sys.executable, str(HERE / "make_test_video.py"), str(out)],
                   check=True, capture_output=True)
    return out / "polixor_test_stream.mp4"


VIDEO = _video()
FIXTURE = VIDEO.with_suffix(".transcript.json")
if FIXTURE.exists():
    os.environ["POLIXOR_FIXTURE_TRANSCRIPT"] = str(FIXTURE)

# ---- מיגרציה: DB בסכמה הישנה, לפני שהאפליקציה נטענת ----
_OLD_DB = DATA / "polixor.db"
if not _OLD_DB.exists():
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(_OLD_DB)
    con.executescript((HERE / "fixtures" / "baseline_schema.sql").read_text("utf-8"))
    con.execute(
        "INSERT INTO jobs (id, title, input_url, status, stage, stage_progress, "
        "overall_progress, message, error, error_code, completed_stages, artifacts, "
        "settings_snapshot, is_live_mode, live_cycles, live_state, "
        "live_captured_seconds, live_reconnects, live_stop_requested, live_segments, "
        "live_error, created_at) VALUES ('oldjob000000000a', 'משימה ישנה', '', "
        "'COMPLETED', 'DONE', 1, 1, '', '', '', '[]', '{}', '{}', 0, 0, 'IDLE', 0, 0, 0, "
        "'[]', '', '2025-01-01 10:00:00')")
    con.commit()
    con.close()

from fastapi.testclient import TestClient                          # noqa: E402

from polixor.main import app                                        # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
results: list[tuple[bool, str]] = []


def check(cond: bool, label: str, detail: str = "") -> bool:
    results.append((bool(cond), label))
    print(f"  {PASS if cond else FAIL} {label}" + (f"  — {detail}" if detail and not cond else ""))
    return bool(cond)


def wait(c: TestClient, pid: str, phases: tuple[str, ...], timeout: float = 600) -> dict:
    t0 = time.time()
    p: dict = {}
    while time.time() - t0 < timeout:
        p = c.get(f"/api/projects/{pid}").json()
        if p["phase"] in phases or p["status"] in ("failed", "cancelled"):
            return p
        time.sleep(0.5)
    return p


def upload(c: TestClient) -> str:
    with VIDEO.open("rb") as f:
        r = c.post("/api/upload", files={"file": (VIDEO.name, f, "video/mp4")})
    assert r.status_code == 200, r.text
    return r.json()["upload_token"]


def create(c: TestClient, **kw) -> dict:
    body = {"source": {"type": "upload", "upload_token": upload(c)},
            "ui_language": "he", "content_language": "he", **kw}
    r = c.post("/api/projects", json=body)
    assert r.status_code == 200, r.text
    return r.json()


SHORT_CFG = {"mode": "short", "aspect_ratio": "9:16", "clip_count": 2,
             "clip_min_seconds": 15, "clip_max_seconds": 30, "layout": "center"}


def main() -> int:
    print(f"נתוני בדיקה: {DATA}\nוידאו: {VIDEO}")
    with TestClient(app) as c:
        c.put("/api/settings", json={"transcript_provider": "fixture" if FIXTURE.exists()
                                     else "none", "ai_mode": "heuristic",
                                     "video_quality": "low", "visual_sample_fps": 2.0})

        print("\n▶ מיגרציה")
        con = sqlite3.connect(DATA / "polixor.db")
        cols = {r[1] for r in con.execute("PRAGMA table_info(jobs)")}
        con.close()
        check({"phase", "run_scope", "mode", "ui_language", "content_language",
               "project_config", "analysis", "error_data"} <= cols,
              "עמודות הפרויקט נוספו לטבלה הישנה")
        old = c.get("/api/projects/oldjob000000000a")
        check(old.status_code == 200 and old.json()["legacy"] is True,
              "משימה מהגרסה הקודמת נקראת כפרויקט (legacy)", old.text[:200])
        check(c.get("/api/jobs").status_code == 200, "ה-API הישן של משימות עדיין עובד")

        print("\n▶ ברירות מחדל")
        d_he = c.get("/api/projects/defaults", headers={"Accept-Language": "he-IL"}).json()
        d_en = c.get("/api/projects/defaults?lang=en").json()
        check(d_he["config"]["subtitles"]["style"]["font"] != "" and
              "options" in d_en and "9:16" in d_en["options"]["aspect_ratios"],
              "ברירות מחדל ואפשרויות")

        print("\n▶ ניתוח")
        t0 = time.time()
        p = create(c, title="בדיקת פרויקט", vocabulary="ולורנט, אוהד\nאוהד")
        pid = p["id"]
        check(p["config"].get("vocabulary") == ["ולורנט", "אוהד"],
              "אוצר מילים של הפרויקט נשמר (בלי כפילויות)", str(p["config"].get("vocabulary")))
        check(p["phase"] in ("importing", "analyzing"), "יצירה מתחילה ניתוח מיד", p["phase"])
        r = c.post(f"/api/projects/{pid}/generate", json={"config": SHORT_CFG})
        check(r.status_code in (400, 409), "יצירה לפני סוף הניתוח נחסמת", str(r.status_code))
        p = wait(c, pid, ("configure",))
        check(p["phase"] == "configure", f"הניתוח הושלם ({time.time() - t0:.1f}s)",
              str(p.get("error")))
        an = p.get("analysis") or {}
        check(bool(an) and "speakers" in str(an), "סיכום ניתוח נשמר (כולל הסבר על דוברים)")
        check(c.get(f"/api/projects/{pid}/thumbnail").status_code == 200,
              "תמונה ממוזערת מהמקור המקומי")

        print("\n▶ PATCH ורשימה")
        r = c.patch(f"/api/projects/{pid}", json={"config": {**SHORT_CFG, "clip_count": 99,
                                                             "aspect_ratio": "7:3"}})
        cfg = r.json()["config"]
        check(r.status_code == 200 and cfg["clip_count"] == 20 and cfg["aspect_ratio"] == "9:16",
              "ערכים לא חוקיים מתוקנים בשרת", str(cfg)[:200])
        c.patch(f"/api/projects/{pid}", json={"config": SHORT_CFG})
        items = c.get("/api/projects?phase=configure").json()["items"]
        check(any(i["id"] == pid for i in items), "סינון רשימה לפי שלב")

        print("\n▶ יצירה")
        t0 = time.time()
        r = c.post(f"/api/projects/{pid}/generate", json={"config": SHORT_CFG})
        check(r.status_code == 200, "generate התקבל", r.text[:200])
        r = c.patch(f"/api/projects/{pid}", json={"config": {"clip_count": 3}})
        check(r.status_code == 409, "שינוי הגדרות בזמן יצירה → 409", str(r.status_code))
        p = wait(c, pid, ("done",))
        clips1 = c.get(f"/api/projects/{pid}/clips").json()
        check(p["phase"] == "done" and 1 <= len(clips1) <= 2,
              f"נוצרו {len(clips1)} קליפים ({time.time() - t0:.1f}s)", str(p.get("error")))
        check(all(cl["status"] in ("ready", "needs_review") for cl in clips1),
              "כל הקליפים הסתיימו", str([cl["status"] for cl in clips1]))
        check(all(Path(cl["file_path"]).exists() for cl in clips1 if cl.get("file_path")),
              "קובצי הקליפים קיימים")
        rev = c.get(f"/api/projects/{pid}/clip-review?lang=en").json()
        sel = rev.get("selected") or []
        check(rev.get("available") is True and len(sel) == len(clips1)
              and all(r["hook"]["text"] and r["payoff"]["text"] and r["components"]
                      and r["boundaries"]["start_reason"]["text"] for r in sel),
              "דוח בחירה: לכל קליפ וו, פאנץ', רכיבי ציון וסיבת גבולות",
              str({k: rev.get(k) for k in ("available", "stats")})[:200])
        check(all(any(abs(cl["source_start"] - r["start"]) < 0.01 for r in sel) for cl in clips1),
              "הקליפים שנוצרו הם בדיוק הטווחים שבדוח")
        cues = c.get(f"/api/clips/{clips1[0]['id']}/cues").json()
        pr = c.get(f"/api/clips/{clips1[0]['id']}/proofread")
        check(pr.status_code == 200 and {"items", "stats"} <= set(pr.json()),
              "תוצאות ההגהה לקליפ זמינות לעורך", pr.text[:160])
        check(not cues or all("p" in w for q in cues for w in q["words"]),
              "ביטחון הזיהוי של כל מילה נשמר בכתוביות (להדגשה בעורך)")
        if cues:
            edited = [{"id": q["id"], "start": q["start"], "end": q["end"], "text": q["text"]}
                      for q in cues]
            edited[0]["text"] = edited[0]["text"] + " (תוקן)"
            c.put(f"/api/clips/{clips1[0]['id']}/cues", json=edited)
            from polixor.config import PATHS as _P
            corr = json.loads((_P.job_work_dir(pid) / "transcript.corrections.json")
                              .read_text("utf-8"))
            ue = (corr.get("user_edits") or [{}])[-1]
            check(ue.get("after", "").endswith("(תוקן)") and ue.get("asr_text"),
                  "עריכה ידנית נרשמת ביומן התיקונים לצד מה שהמזהה שמע", str(ue)[:160])

        print("\n▶ יצירה מחדש")
        c.post(f"/api/projects/{pid}/generate", json={"config": SHORT_CFG})
        wait(c, pid, ("done",))
        clips2 = c.get(f"/api/projects/{pid}/clips").json()
        check(len(clips2) == len(clips1), "יצירה מחדש מחליפה ולא משכפלת",
              f"{len(clips1)} → {len(clips2)}")
        check(not ({x["id"] for x in clips1} & {x["id"] for x in clips2}),
              "הקליפים הקודמים הוחלפו")
        check(not any(Path(x["file_path"]).exists() for x in clips1 if x.get("file_path")
                      and x["file_path"] not in {y.get("file_path") for y in clips2}),
              "קבצי הקליפים הקודמים נמחקו")

        print("\n▶ ביטול ומחיקה")
        p2 = create(c)
        c.post(f"/api/projects/{p2['id']}/cancel")
        p2 = wait(c, p2["id"], ("configure", "failed"), timeout=60)
        check(p2["status"] in ("cancelled", "failed") or p2["phase"] == "configure",
              "ביטול ניתוח", f"{p2['status']}/{p2['phase']}")
        r = c.post(f"/api/projects/{p2['id']}/generate", json={"config": SHORT_CFG})
        check(r.status_code == 400 or p2["phase"] == "configure",
              "יצירה בלי ניתוח → 400 (analysis_incomplete)", str(r.status_code))
        files = [Path(x["file_path"]) for x in clips2 if x.get("file_path")]
        r = c.delete(f"/api/projects/{pid}?delete_files=true")
        check(r.status_code == 200 and c.get(f"/api/projects/{pid}").status_code == 404,
              "מחיקת פרויקט")
        check(not any(f.exists() for f in files), "קבצי הקליפים נמחקו עם הפרויקט")
        c.delete(f"/api/projects/{p2['id']}")

        print("\n▶ שפת הבקשה")
        r_en = c.get("/api/projects/nope", headers={"Accept-Language": "en-US,en;q=0.9"})
        r_he = c.get("/api/projects/nope", headers={"Accept-Language": "he-IL"})
        r_x = c.get("/api/projects/nope", headers={"Accept-Language": "he-IL",
                                                   "X-Polixor-Lang": "en"})
        r_q = c.get("/api/projects/nope?lang=en", headers={"X-Polixor-Lang": "he"})
        m_en, m_he = r_en.json()["detail"]["message"], r_he.json()["detail"]["message"]
        check(r_en.status_code == 404 and m_en.isascii() and not m_he.isascii(),
              "Accept-Language קובע את שפת השגיאה", f"{m_en} / {m_he}")
        check(r_x.json()["detail"]["message"] == m_en, "X-Polixor-Lang גובר על Accept-Language")
        check(r_q.json()["detail"]["message"] == m_en, "?lang גובר על הכותרות")
        check(c.get("/api/health").json().get("lang") == "he",
              "ברירת המחדל בלי כותרות היא עברית")

        print("\n▶ שגיאה שנשמרה – מוצגת בכל שפה")
        from polixor.db import session_scope
        from polixor.errors import NoMomentsFoundError
        from polixor.models import Job, JobStatus

        with session_scope() as s:
            j = s.get(Job, "oldjob000000000a")
            j.status = JobStatus.FAILED
            j.error_data = NoMomentsFoundError().to_record()
        e_en = c.get("/api/projects/oldjob000000000a?lang=en").json()["error"]["message"]
        e_he = c.get("/api/projects/oldjob000000000a?lang=he").json()["error"]["message"]
        check(e_en != e_he and e_en.isascii(), "שגיאה שמורה מתורגמת לפי שפת הבקשה",
              f"{e_en} / {e_he}")

        print("\n▶ הפעלה מחדש: משימה בתור מוגשת שוב")
        from polixor.models import JobStage, ProjectPhase, RunScope, new_id
        from polixor.pipeline import resume_interrupted_jobs

        token = upload(c)
        qid = new_id()
        with session_scope() as s:
            s.add(Job(id=qid, title="queued", input_url="", status=JobStatus.QUEUED,
                      stage=JobStage.PENDING, settings_snapshot={},
                      artifacts={"source_path": str(DATA / "sources" / token)},
                      completed_stages=[], run_scope=RunScope.ANALYZE.value,
                      phase=ProjectPhase.IMPORTING.value, ui_language="he",
                      content_language="he", project_config={}, error_data={}))
        resume_interrupted_jobs()
        q = wait(c, qid, ("configure",), timeout=180)
        check(q["phase"] == "configure", "משימה שהייתה בתור רצה אחרי resume",
              f"{q['status']}/{q['phase']}")

    ok = sum(1 for r in results if r[0])
    print(f"\n{ok}/{len(results)} בדיקות פרויקטים עברו")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
