"""
מייצר את כל הנכסים לאתר ההדגמה: קליפים אמיתיים בארבעה סגנונות עריכה,
ארבע פריסות אנכיות, נתוני ציר הזמן, הכתוביות ותכניות העריכה.

הכול מופק על-ידי הפייפליין האמיתי של Polixor – לא מוקאפ.

הרצה:  python3 tests/make_demo_assets.py <video.mp4> <out_dir>
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polixor.config import AppSettings                                # noqa: E402
from polixor.services import editing, render, scoring, subtitles      # noqa: E402
from polixor.services.audio import analyze_audio                      # noqa: E402
from polixor.services.reframe import plan_reframe                     # noqa: E402
from polixor.services.transcribe import transcribe_audio              # noqa: E402
from polixor.services.visual import analyze_video, estimate_camera_region  # noqa: E402
from polixor.util.ffmpeg import (                                     # noqa: E402
    extract_audio_wav, extract_thumbnail, probe, validate_playable,
)

# חלון שמכיל: לידאין מת, שתיקה דרמטית של 5 שניות, סיפור שקט, ופרץ ניצחון.
# נבחר בכוונה כדי שההבדל בין הסגנונות יהיה מורגש ולא תיאורטי.
WINDOW = (33.0, 57.5)
LAYOUT_WINDOW = (22.0, 30.0)

# אזור מצלמת הסטרימר בווידאו הבדיקה, כפי שהוא מוגדר ידנית במסך העריכה.
# בווידאו סינתטי מסווג הפנים לא מזהה אותו לבד, ולכן זה המסלול הידני.
MANUAL_CAMERA = {"x": 0.748, "y": 0.661, "w": 0.228, "h": 0.297}

LAYOUTS = [
    ("center", "חיתוך מרכזי",
     "חותך את מרכז הפריים. תמיד עובד, ללא תלות בזיהוי."),
    ("auto_face", "מעקב אחרי פנים",
     "עוקב אחרי הפנים הדומיננטיות עם אזור מת והגבלת מהירות, כדי שהמסגרת "
     "לא תרעד. אם לא זוהו פנים – חוזר לחיתוך מרכזי ומדווח."),
    ("split", "מסך מפוצל",
     "מצלמת הסטרימר למעלה (38%), גיימפליי למטה. אזור המצלמה מזוהה "
     "אוטומטית או מוגדר ידנית ונשמר."),
    ("blur_pad", "מסגרת מלאה על רקע מטושטש",
     "הפריים המלא במרכז על רקע מטושטש של עצמו. שום דבר לא נחתך, "
     "ואין הגדלה – ולכן התמונה נשארת חדה."),
]


def main() -> None:
    video = Path(sys.argv[1] if len(sys.argv) > 1
                 else "/home/claude/testdata/polixor_test_stream.mp4")
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "/home/claude/demo_assets")
    if out.exists():
        shutil.rmtree(out)
    (out / "video").mkdir(parents=True)
    (out / "img").mkdir(parents=True)
    work = out / "_work"
    work.mkdir()

    fixture = video.with_suffix(".transcript.json")
    if fixture.exists():
        os.environ.setdefault("POLIXOR_FIXTURE_TRANSCRIPT", str(fixture))

    settings = AppSettings.from_dict({
        "transcript_provider": "fixture" if fixture.exists() else "none",
        "visual_sample_fps": 2.0,
        "video_quality": "medium",
        "subtitles_enabled": True,
        "subtitle_word_level": True,
        "short_resolution": "720x1280",
        "long_resolution": "1280x720",
    })

    info = probe(video)
    print(f"מקור: {info.width}x{info.height} · {info.duration:.1f}s\n")

    # ================= ניתוח =================
    print("[1/5] ניתוח…")
    wav = work / "audio.wav"
    extract_audio_wav(video, wav, total_seconds=info.duration)
    audio = analyze_audio(wav)
    visual = analyze_video(video, sample_fps=2.0, duration=info.duration,
                           detect_faces=True)
    transcript = transcribe_audio(wav, settings=settings,
                                  media_duration=info.duration)
    timeline = scoring.build_timeline(audio=audio, visual=visual,
                                      transcript=transcript,
                                      duration=info.duration, settings=settings)

    src_info = {"width": info.width, "height": info.height,
                "has_audio": info.has_audio, "duration": info.duration}

    manifest: dict = {
        "source": {
            "width": info.width, "height": info.height,
            "duration": round(info.duration, 2),
            "size_bytes": info.size_bytes,
            "note": "וידאו בדיקה סינתטי שנוצר מאפס – ללא חומר מוגן.",
        },
        "window": {"start": WINDOW[0], "end": WINDOW[1]},
        "styles": [], "layouts": [], "timeline": {}, "cues": [],
        "transcript": [],
    }

    # ================= ציר הזמן =================
    print("[2/5] ציר הזמן…")
    import numpy as np
    n = timeline.n
    keep = min(900, n)
    idx = np.linspace(0, n - 1, keep).astype(int)
    manifest["timeline"] = {
        "duration": round(timeline.duration, 2),
        "times": [round(float(timeline.times[i]), 2) for i in idx],
        "score": [round(float(timeline.score[i]), 4) for i in idx],
        "vocal": [round(float(timeline.vocal[i]), 3) for i in idx],
        "speech": [round(float(timeline.speech[i]), 3) for i in idx],
        "visual": [round(float(timeline.visual[i]), 3) for i in idx],
        "pause": [round(float(timeline.pause[i]), 3) for i in idx],
    }
    manifest["transcript"] = [
        {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text}
        for s in transcript.segments
    ]

    # ================= סגנונות עריכה =================
    print("[3/5] ארבעת סגנונות העריכה…")
    peak = float(timeline.times[
        int(timeline.score[timeline.idx(WINDOW[0]):timeline.idx(WINDOW[1])].argmax())
        + timeline.idx(WINDOW[0])])
    reframe_plan = plan_reframe(visual, clip_start=WINDOW[0], clip_end=WINDOW[1],
                                layout="center")

    base_cues = subtitles.build_cues(transcript, clip_start=WINDOW[0],
                                     clip_end=WINDOW[1], max_chars=20)

    for style_name in editing.EDIT_STYLES:
        style = editing.get_style(style_name)
        t0 = time.time()
        plan = editing.build_edit_plan(
            clip_start=WINDOW[0], clip_end=WINDOW[1], peak_time=peak,
            style=style, audio=audio, timeline=timeline, transcript=transcript)

        cues = subtitles.remap_cues(list(base_cues), plan)
        sub_style = subtitles.style_for_clip(settings, vertical=True, language="he")
        sub_style.animation = style.caption_animation
        ass = work / f"{style_name}.ass"
        if cues:
            subtitles.write_ass(cues, ass, width=720, height=1280, style=sub_style)

        dst = out / "video" / f"style_{style_name}.mp4"
        req = render.build_request(
            source=video, output=dst, segments=[WINDOW], vertical=True,
            settings=settings, reframe=reframe_plan,
            subtitle_path=ass if cues else None, source_info=src_info,
            transitions=False, work_dir=work,
            edit_style=style_name, edit_plans=[plan])
        res = render.render_clip(req)
        ok, msg = validate_playable(res.path)
        _shrink(dst)

        extract_thumbnail(dst, out / "img" / f"style_{style_name}.jpg",
                          at_seconds=min(2.0, res.duration / 3), width=420)

        stats = plan.stats()
        manifest["styles"].append({
            "name": style_name, "label": style.label,
            "description": style.description,
            "file": f"video/style_{style_name}.mp4",
            "thumb": f"img/style_{style_name}.jpg",
            "duration": round(res.duration, 2),
            "size_bytes": dst.stat().st_size,
            "valid": ok, "validation": msg,
            "render_seconds": round(time.time() - t0, 1),
            "stats": stats,
            "summary": editing.describe_plan(plan, style),
            "beats": [
                {"start": round(b.src_start, 3), "end": round(b.src_end, 3),
                 "zoom": round(b.zoom, 3), "speed": round(b.speed, 3),
                 "reason": b.reason}
                for b in plan.beats
            ],
            "caption_animation": style.caption_animation,
            "cue_count": len(cues),
        })
        print(f"   {style.label:8s} {stats['raw_duration']:5.1f}s → "
              f"{res.duration:5.1f}s  {dst.stat().st_size / 1024:6.0f} KB  "
              f"{'תקין' if ok else 'שגיאה: ' + msg}")

        if style_name == "dynamic":
            manifest["cues"] = [c.to_dict() for c in cues]

    # ================= פריסות אנכיות =================
    print("[4/5] ארבע הפריסות האנכיות…")
    camera = estimate_camera_region(visual)
    clean = editing.get_style("clean")
    for name, label, desc in LAYOUTS:
        lp = plan_reframe(visual, clip_start=LAYOUT_WINDOW[0],
                          clip_end=LAYOUT_WINDOW[1], layout=name,
                          manual_camera=(camera or MANUAL_CAMERA)
                          if name == "split" else None)
        plan = editing.build_edit_plan(
            clip_start=LAYOUT_WINDOW[0], clip_end=LAYOUT_WINDOW[1],
            peak_time=peak, style=clean, audio=audio, timeline=timeline,
            transcript=transcript)
        dst = out / "video" / f"layout_{name}.mp4"
        req = render.build_request(
            source=video, output=dst, segments=[LAYOUT_WINDOW], vertical=True,
            settings=settings, reframe=lp, subtitle_path=None,
            source_info=src_info, transitions=False, work_dir=work,
            edit_style="clean", edit_plans=[plan])
        res = render.render_clip(req)
        ok, _ = validate_playable(res.path)
        _shrink(dst)
        extract_thumbnail(dst, out / "img" / f"layout_{name}.jpg",
                          at_seconds=res.duration / 2, width=360)
        manifest["layouts"].append({
            "name": name, "label": label, "description": desc,
            "file": f"video/layout_{name}.mp4",
            "thumb": f"img/layout_{name}.jpg",
            "applied": lp.layout, "note": lp.note,
            "tracked_ratio": round(lp.tracked_ratio, 3),
            "size_bytes": dst.stat().st_size, "valid": ok,
        })
        if name == "split" and not camera:
            manifest["layouts"][-1]["note"] += (
                " אזור המצלמה כאן הוגדר ידנית: בווידאו סינתטי מסווג הפנים "
                "אינו מזהה אותו לבד.")
            manifest["layouts"][-1]["camera_source"] = "manual"
        fallback = " (נפל לחיתוך מרכזי)" if lp.layout != name else ""
        print(f"   {label:28s} {dst.stat().st_size / 1024:6.0f} KB{fallback}")

    # ================= קטע מהמקור, להשוואה =================
    print("[5/5] קטע מקור להשוואה…")
    src_clip = out / "video" / "source_excerpt.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", str(WINDOW[0]), "-i", str(video), "-t", str(WINDOW[1] - WINDOW[0]),
        "-vf", "scale=854:-2", "-c:v", "libx264", "-preset", "medium",
        "-crf", "26", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart", str(src_clip),
    ], check=True, timeout=600)
    extract_thumbnail(src_clip, out / "img" / "source.jpg",
                      at_seconds=8.0, width=640)
    manifest["source"]["excerpt"] = "video/source_excerpt.mp4"
    manifest["source"]["thumb"] = "img/source.jpg"
    manifest["source"]["excerpt_size"] = src_clip.stat().st_size

    shutil.rmtree(work, ignore_errors=True)
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"\n✓ {out}  ({total / 1024 / 1024:.1f} MB)")
    for f in sorted(out.rglob("*")):
        if f.is_file():
            print(f"   {f.relative_to(out).as_posix():40s} {f.stat().st_size / 1024:7.0f} KB")


def _shrink(path: Path, crf: int = 26) -> None:
    """דוחס מחדש לקובץ קטן יותר לאתר, בלי לשנות מידות או תוכן."""
    tmp = path.with_suffix(".tmp.mp4")
    r = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart", str(tmp),
    ], capture_output=True, text=True, timeout=900)
    if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 1024:
        tmp.replace(path)
    else:
        tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
