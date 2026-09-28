"""
משווה את אותו רגע בארבעת סגנונות העריכה.

מרנדר את אותו חלון מהשידור ארבע פעמים – גולמי, נקי, דינמי, אנרגטי –
ומדפיס מה כל סגנון עשה: כמה נחתך, כמה זוויות, כמה זמן חסך.

הרצה:  python3 tests/compare_edit_styles.py <video.mp4> [out_dir]
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polixor.config import AppSettings                                # noqa: E402
from polixor.services import editing, render, scoring, subtitles      # noqa: E402
from polixor.services.audio import analyze_audio                      # noqa: E402
from polixor.services.reframe import plan_reframe                     # noqa: E402
from polixor.services.transcribe import transcribe_audio              # noqa: E402
from polixor.services.visual import analyze_video                     # noqa: E402
from polixor.util.ffmpeg import extract_audio_wav, probe, validate_playable  # noqa: E402
from polixor.util.fs import human_size                                # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"


def main() -> None:
    video = Path(sys.argv[1] if len(sys.argv) > 1
                 else "/home/claude/testdata/polixor_test_stream.mp4")
    out_dir = Path(sys.argv[2] if len(sys.argv) > 2 else "/home/claude/edit_compare")
    out_dir.mkdir(parents=True, exist_ok=True)

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
    })

    print(f"מקור: {video}")
    info = probe(video)
    print(f"      {info.width}x{info.height} · {info.duration:.1f} שניות\n")

    # ---- ניתוח פעם אחת, משותף לכל הסגנונות ----
    work = out_dir / "_work"
    work.mkdir(exist_ok=True)
    wav = work / "audio.wav"
    if not wav.exists():
        extract_audio_wav(video, wav, total_seconds=info.duration)

    print("מנתח…")
    audio = analyze_audio(wav)
    visual = analyze_video(video, sample_fps=2.0, duration=info.duration,
                           detect_faces=True)
    transcript = transcribe_audio(wav, settings=settings,
                                  media_duration=info.duration)
    timeline = scoring.build_timeline(audio=audio, visual=visual,
                                      transcript=transcript,
                                      duration=info.duration, settings=settings)

    # ---- בוחרים חלון שמכיל דיבור, שתיקה ופרץ עוצמה ----
    clip_start, clip_end = 14.0, 50.0
    peak = float(timeline.times[int(timeline.score[
        timeline.idx(clip_start):timeline.idx(clip_end)].argmax())
        + timeline.idx(clip_start)])
    print(f"חלון הבדיקה: {clip_start:.0f}–{clip_end:.0f} שניות "
          f"(שיא ב-{peak:.1f})\n")

    reframe_plan = plan_reframe(visual, clip_start=clip_start, clip_end=clip_end,
                                layout="auto_face")

    rows: list[dict] = []
    for style_name in editing.EDIT_STYLES:
        style = editing.get_style(style_name)
        t0 = time.time()

        plan = editing.build_edit_plan(
            clip_start=clip_start, clip_end=clip_end, peak_time=peak,
            style=style, audio=audio, timeline=timeline, transcript=transcript,
        )

        cues = subtitles.build_cues(transcript, clip_start=clip_start,
                                    clip_end=clip_end, max_chars=22)
        cues = subtitles.remap_cues(cues, plan)

        sub_style = subtitles.style_for_clip(settings, vertical=True, language="he")
        sub_style.animation = style.caption_animation
        ass_path = work / f"{style_name}.ass"
        if cues:
            subtitles.write_ass(cues, ass_path, width=720, height=1280,
                                style=sub_style)

        out = out_dir / f"{style_name}.mp4"
        req = render.build_request(
            source=video, output=out, segments=[(clip_start, clip_end)],
            vertical=True, settings=settings, reframe=reframe_plan,
            subtitle_path=ass_path if cues else None,
            source_info={"width": info.width, "height": info.height,
                         "has_audio": info.has_audio, "duration": info.duration},
            transitions=False, work_dir=work,
            edit_style=style_name, edit_plans=[plan],
        )
        result = render.render_clip(req)
        elapsed = time.time() - t0

        ok, msg = validate_playable(result.path)
        stats = plan.stats()
        rows.append({
            "style": style_name, "label": style.label,
            "raw": stats["raw_duration"], "out": round(result.duration, 2),
            "removed": stats["removed_seconds"],
            "removed_pct": stats["removed_percent"],
            "cuts": stats["cuts"], "zooms": stats["zoom_changes"],
            "dramatic": stats["dramatic_pauses_kept"],
            "cues": len(cues), "size": result.size_bytes,
            "valid": ok, "msg": msg, "render_s": round(elapsed, 1),
            "summary": editing.describe_plan(plan, style),
            "notes": plan.notes,
        })

        mark = PASS if ok else FAIL
        print(f"{mark} {style.label:8s} {stats['raw_duration']:5.1f}s → "
              f"{result.duration:5.1f}s  "
              f"({stats['cuts']:2d} חיתוכים, {stats['zoom_changes']:2d} זוויות, "
              f"{stats['removed_seconds']:4.1f}s הוסרו)  "
              f"{human_size(result.size_bytes):>9s}  [{elapsed:.1f}s]")
        for n in plan.notes:
            print(f"      · {n}")

    # ---- אימות ----
    print("\n" + "=" * 72)
    failures = []
    raw_row = next(r for r in rows if r["style"] == "raw")

    for r in rows:
        if not r["valid"]:
            failures.append(f"{r['style']}: קובץ לא תקין — {r['msg']}")

    if abs(raw_row["out"] - raw_row["raw"]) > 0.6:
        failures.append(f"raw שינה את האורך ({raw_row['raw']} → {raw_row['out']})")
    if raw_row["cuts"] != 0:
        failures.append("raw ביצע חיתוכים")

    for r in rows:
        if r["style"] == "raw":
            continue
        if r["out"] >= raw_row["out"] - 0.2:
            failures.append(f"{r['style']}: לא קיצר את הקליפ")
        expected = r["raw"] - r["removed"]
        if abs(r["out"] - expected) > 1.2:
            failures.append(
                f"{r['style']}: אורך הפלט {r['out']} לא תואם לתכנית {expected:.1f}")

    dyn = next(r for r in rows if r["style"] == "dynamic")
    hype = next(r for r in rows if r["style"] == "hype")
    if dyn["zooms"] == 0:
        failures.append("dynamic: לא בוצעו שינויי זווית")
    if hype["removed"] <= dyn["removed"]:
        failures.append("hype: לא אגרסיבי יותר מ-dynamic בהסרת שקט")

    if failures:
        print(f"{FAIL} {len(failures)} בעיות:")
        for f in failures:
            print(f"   · {f}")
    else:
        print(f"{PASS} כל הסגנונות הפיקו קובץ תקין, והאורכים תואמים לתכניות.")

    (out_dir / "comparison.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nפלט: {out_dir}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
