"""
Polixor – acceptance review report for YOUR real media.

The acceptance question is not "did the pipeline finish?" but "would a
creator actually post these clips?". This tool produces a review report you
can go through clip by clip:

  * selected clips (playable), with the hook, the payoff and why they were
    chosen, the score parts and penalties, and where each clip starts/ends
  * near misses (rejected), with the reason
  * duplicates that were removed
  * subtitle confidence and problem areas per clip (uncertain words,
    automatic corrections and what was originally heard)
  * processing time per stage and sub-stage, with real-time factors
  * a "Would you post it?" rating for each clip; the ratings download as a
    JSON file that can be used to tune the engine

Two ways to use it:

    # 1) you already processed the video in Polixor (project page):
    .venv/bin/python scripts/acceptance_report.py --project <project id>
    # 2) process a file now (real transcription, analysis and rendering):
    .venv/bin/python scripts/acceptance_report.py --media /path/to/stream.mp4 --language he

    (Windows: .venv\\Scripts\\python.exe scripts\\acceptance_report.py ...)

The project id is the last part of the project page address
(.../projects/<id>). Output: a folder with report.html, report.md and
report.json. Open report.html in a browser. Messages are in English on
purpose: the Windows console shows Hebrew reversed.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def _fmt_t(t: float) -> str:
    t = max(0.0, float(t))
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _process_media(media: Path, language: Optional[str], lang_ui: str) -> str:
    """Runs analysis + short-clip generation on the file (like the UI) and returns the job id."""
    from polixor.config import SETTINGS, AppSettings
    from polixor.db import session_scope
    from polixor.models import Job, JobStatus, ProjectPhase, RunScope, new_id
    from polixor.pipeline import run_job
    from polixor.project_config import default_config

    base = SETTINGS.get().to_dict()
    base["transcript_provider"] = "faster-whisper"
    if language:
        base["transcribe_language"] = language
    settings = AppSettings.from_dict(base)
    cfg = default_config(lang_ui, language or "auto")
    job_id = new_id()
    with session_scope() as s:
        s.add(Job(id=job_id, title=f"Acceptance: {media.name}", input_url="",
                  status=JobStatus.QUEUED, settings_snapshot=settings.to_dict(),
                  artifacts={"source_path": str(media)}, completed_stages=[],
                  run_scope=RunScope.ANALYZE.value, phase=ProjectPhase.IMPORTING.value,
                  ui_language=lang_ui, content_language=language or "auto",
                  project_config=cfg))
    print("Analysing (transcription + analysis) – this can take a long time for long videos...")
    t0 = time.time()
    run_job(job_id, threading.Event())
    print(f"  analysis done in {(time.time() - t0) / 60:.1f} min")
    with session_scope() as s:
        job = s.get(Job, job_id)
        job.run_scope = RunScope.GENERATE.value
        job.mode = "short"
        job.status = JobStatus.QUEUED
    print("Generating clips...")
    t0 = time.time()
    run_job(job_id, threading.Event())
    print(f"  generation done in {(time.time() - t0) / 60:.1f} min")
    return job_id


def build(job_id: str) -> dict[str, Any]:
    from polixor.api.serializers import performance_summary
    from polixor.db import session_scope
    from polixor.models import Clip, Job, StageTiming, SubtitleCue
    from polixor.services import analysis_store
    from polixor.services import transcript_correct as tc
    from polixor.services.clip_intel.review import localize

    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise SystemExit(f"No project/job with id {job_id}")
        arts = dict(job.artifacts or {})
        timings = s.query(StageTiming).filter(StageTiming.job_id == job_id) \
            .order_by(StageTiming.id).all()
        perf = performance_summary(job, timings) or {}
        clips = s.query(Clip).filter(Clip.job_id == job_id).order_by(Clip.source_start).all()
        clip_rows = []
        for c in clips:
            cues = s.query(SubtitleCue).filter(SubtitleCue.clip_id == c.id) \
                .order_by(SubtitleCue.idx).all()
            words = [w for q in cues for w in (q.words or []) if isinstance(w, dict)]
            low = [w for w in words if float(w.get("p", 1.0)) < 0.5]
            clip_rows.append({
                "id": c.id, "kind": c.kind.value, "status": c.status.value, "title": c.title,
                "start": c.source_start, "end": c.source_end, "duration": c.duration,
                "score": c.score, "reason": c.reason, "file": c.file_path,
                "subtitles": {
                    "cues": len(cues), "words": len(words),
                    "low_confidence_words": len(low),
                    "low_confidence_ratio": round(len(low) / len(words), 3) if words else None,
                    "uncertain": [w.get("text") for w in low][:40],
                    "corrected": [{"text": q.text, "heard_as": " ".join(
                        str(w.get("asr")) for w in (q.words or []) if isinstance(w, dict) and w.get("asr"))}
                        for q in cues if any(isinstance(w, dict) and w.get("flag") == "corrected"
                                             for w in (q.words or []))],
                    "edited": sum(1 for q in cues if q.edited),
                },
            })
        title = job.title

    review = analysis_store.load_clip_review(Path(arts["clip_review_path"])) \
        if arts.get("clip_review_path") else None
    if review:
        review = localize(review)
    corrections = tc.load(Path(arts["corrections_path"])) if arts.get("corrections_path") else None
    transcript_meta = {}
    if arts.get("transcript_path") and Path(arts["transcript_path"]).exists():
        data = json.loads(Path(arts["transcript_path"]).read_text("utf-8"))
        transcript_meta = {"provider": data.get("provider"), "model": data.get("model"),
                           "language": data.get("language"), "meta": data.get("meta") or {},
                           "segments": len(data.get("segments") or [])}

    # match each clip to its selection record
    for row in clip_rows:
        rec = None
        for r in (review or {}).get("selected") or []:
            if abs(float(r["start"]) - float(row["start"])) < 0.05:
                rec = r
                break
        row["selection"] = rec
    return {
        "job_id": job_id, "title": title,
        "created": datetime.now().isoformat(timespec="seconds"),
        "transcript": transcript_meta, "performance": perf,
        "selection": {k: (review or {}).get(k) for k in ("threshold", "stats", "regions", "settings")},
        "clips": clip_rows,
        "near_misses": (review or {}).get("near_misses") or [],
        "duplicates": (review or {}).get("duplicates") or [],
        "proofreading": {"stats": (corrections or {}).get("stats"),
                         "strong_model": (corrections or {}).get("strong_model"),
                         "retranscribed_seconds": (corrections or {}).get("retranscribed_seconds"),
                         "user_edits": len((corrections or {}).get("user_edits") or [])},
        "notes": arts.get("notes") or [],
    }


# --------------------------------------------------------------------------
def _reasons(items: list[dict[str, Any]]) -> str:
    return "; ".join(i.get("text", "") for i in items or [])


def write_markdown(rep: dict[str, Any], out: Path) -> None:
    L = [f"# Acceptance review – {rep['title']}", ""]
    perf = rep.get("performance") or {}
    tm = rep.get("transcript") or {}
    meta = tm.get("meta") or {}
    L += [f"- Source: {_fmt_t(perf.get('source_seconds', 0))} · total processing "
          f"{(perf.get('total_seconds') or 0) / 60:.1f} min · **total RTF {perf.get('total_rtf')}**",
          f"- Transcription: {tm.get('provider')} / {tm.get('model')} · language {tm.get('language')}"
          f" (p={meta.get('language_probability')}) · profile {meta.get('profile')} · "
          f"chunks {meta.get('chunks')} · vocabulary terms {meta.get('vocabulary_terms')}", ""]
    sel = rep.get("selection") or {}
    st = sel.get("stats") or {}
    L += [f"## Selection (quality bar {sel.get('threshold')})", "",
          f"{st.get('selected', 0)} selected out of {st.get('stories', 0)} possible stories · "
          f"{st.get('near_misses', 0)} near misses · {st.get('duplicates', 0)} duplicates removed · "
          f"{st.get('regions', 1)} analysis regions", ""]
    for i, c in enumerate(rep["clips"], 1):
        r = c.get("selection") or {}
        sub = c["subtitles"]
        L += [f"### {i}. {c['title']}  ({_fmt_t(c['start'])}–{_fmt_t(c['end'])}, {c['duration']:.0f} s, score {c['score']:.2f})",
              f"- File: `{c['file']}` · status {c['status']}"]
        if r:
            L += [f"- **Hook:** {r['hook']['text']}  \n  _why:_ {_reasons(r['hook']['reasons'])}"
                  + (f"  \n  _problems:_ {_reasons(r['hook']['problems'])}" if r['hook']['problems'] else ""),
                  f"- **Context:** {r['context']['sentences']} sentences, {r['context']['seconds']} s",
                  f"- **Payoff:** {r['payoff']['text']} {r['payoff'].get('tail') or ''}  \n  _why:_ {_reasons(r['payoff']['reasons'])}",
                  "- Score parts: " + ", ".join(f"{x['label']} {x['value']:.2f}" for x in r["components"]),
                  "- Penalties: " + (", ".join(f"{x['label']} −{x['value']:.2f}" for x in r["penalties"]) or "none"),
                  f"- Boundaries: starts at {r['boundaries']['start_reason']['text']}; ends at {r['boundaries']['end_reason']['text']}"]
        L += [f"- Subtitles: {sub['low_confidence_words']} of {sub['words']} words uncertain"
              + (f" ({sub['uncertain'][:10]})" if sub['uncertain'] else "")
              + (f" · {len(sub['corrected'])} lines auto-corrected" if sub['corrected'] else ""),
              "- Would you post it? ☐ yes ☐ maybe ☐ no — why:", ""]
    L += ["## Near misses (rejected)", ""]
    for r in rep["near_misses"]:
        L.append(f"- {_fmt_t(r['start'])}–{_fmt_t(r['end'])} score {r['final_score']:.2f} – "
                 f"**{(r.get('rejection') or {}).get('text', '')}** · hook: {r['hook']['text'][:80]} · "
                 f"payoff: {r['payoff']['text'][:80]}")
    L += ["", "## Duplicates removed", ""]
    for r in rep["duplicates"]:
        L.append(f"- {_fmt_t(r['start'])}–{_fmt_t(r['end'])} – {(r.get('rejection') or {}).get('text', '')} "
                 f"(kept: {r.get('duplicate_of')})")
    pr = rep.get("proofreading") or {}
    L += ["", "## Subtitle proofreading", "",
          f"{pr.get('stats')} · stronger model {pr.get('strong_model') or '–'} · "
          f"re-transcribed {pr.get('retranscribed_seconds') or 0} s · manual edits {pr.get('user_edits')}",
          "", "## Time per stage", "", "| Stage | Seconds | RTF |", "|---|---:|---:|"]
    for s in perf.get("stages") or []:
        L.append(f"| {s['stage']} | {s['seconds']:.1f} | {s['rtf']} |")
    for s in perf.get("substages") or []:
        L.append(f"| &nbsp;&nbsp;{s['name']} | {s['seconds']:.1f} | {s['rtf']} |")
    if rep.get("notes"):
        L += ["", "## Notes", ""] + [f"- {n}" for n in rep["notes"]]
    (out / "report.md").write_text("\n".join(L) + "\n", "utf-8")


def write_html(rep: dict[str, Any], out: Path) -> None:
    e = html.escape

    def rel(p: Optional[str]) -> str:
        if not p:
            return ""
        try:
            return Path(os.path.relpath(p, out)).as_posix()
        except ValueError:
            return Path(p).as_uri()

    cards = []
    for i, c in enumerate(rep["clips"], 1):
        r = c.get("selection") or {}
        sub = c["subtitles"]
        parts = "".join(f"<li>{e(x['label'])}: {x['value']:.2f}</li>" for x in r.get("components") or [])
        pens = "".join(f"<li class=bad>{e(x['label'])}: −{x['value']:.2f}</li>" for x in r.get("penalties") or [])
        corrected = "".join(f"<li><bdi>{e(x['text'])}</bdi> <small>(heard: <bdi>{e(x['heard_as'])}</bdi>)</small></li>"
                            for x in sub["corrected"])
        cards.append(f"""
<section class=card data-clip="{e(c['id'])}">
  <h2>{i}. <bdi>{e(c['title'])}</bdi></h2>
  <p class=meta>{_fmt_t(c['start'])}–{_fmt_t(c['end'])} · {c['duration']:.0f} s · score {c['score']:.2f} · {e(c['status'])}</p>
  <video controls preload=metadata src="{e(rel(c['file']))}"></video>
  <p><b>Hook</b>: <bdi>{e(r.get('hook', {}).get('text', ''))}</bdi><br><small>{e(_reasons(r.get('hook', {}).get('reasons', [])))}</small></p>
  <p><b>Payoff</b>: <bdi>{e(r.get('payoff', {}).get('text', ''))} {e(r.get('payoff', {}).get('tail') or '')}</bdi><br><small>{e(_reasons(r.get('payoff', {}).get('reasons', [])))}</small></p>
  <details><summary>Score parts, penalties, boundaries</summary><ul>{parts}{pens}</ul>
  <p><small>Starts at: {e((r.get('boundaries') or {}).get('start_reason', {}).get('text', ''))} · ends at: {e((r.get('boundaries') or {}).get('end_reason', {}).get('text', ''))}</small></p></details>
  <p><b>Subtitles</b>: {sub['low_confidence_words']} of {sub['words']} words uncertain
     {('· <bdi>' + e(', '.join(sub['uncertain'][:12])) + '</bdi>') if sub['uncertain'] else ''}</p>
  {('<p><b>Auto-corrected lines</b></p><ul>' + corrected + '</ul>') if corrected else ''}
  <fieldset><legend>Would you post it?</legend>
    <label><input type=radio name="r-{e(c['id'])}" value=yes> yes</label>
    <label><input type=radio name="r-{e(c['id'])}" value=maybe> maybe</label>
    <label><input type=radio name="r-{e(c['id'])}" value=no> no</label>
    <textarea placeholder="Why? (e.g. weak hook, cut too early, subtitle errors)" data-note="{e(c['id'])}"></textarea>
  </fieldset>
</section>""")
    near = "".join(
        f"<tr><td>{_fmt_t(r['start'])}–{_fmt_t(r['end'])}</td><td>{r['final_score']:.2f}</td>"
        f"<td>{e((r.get('rejection') or {}).get('text', ''))}</td><td><bdi>{e(r['hook']['text'][:90])}</bdi></td>"
        f"<td><bdi>{e(r['payoff']['text'][:90])}</bdi></td></tr>" for r in rep["near_misses"])
    dups = "".join(
        f"<tr><td>{_fmt_t(r['start'])}–{_fmt_t(r['end'])}</td><td>{e((r.get('rejection') or {}).get('text', ''))}</td>"
        f"<td>{e(str(r.get('duplicate_of')))}</td></tr>" for r in rep["duplicates"])
    perf = rep.get("performance") or {}
    rows = "".join(f"<tr><td>{e(s['stage'])}</td><td>{s['seconds']:.1f}</td><td>{s['rtf']}</td></tr>"
                   for s in perf.get("stages") or [])
    rows += "".join(f"<tr class=sub><td>{e(s['name'])}</td><td>{s['seconds']:.1f}</td><td>{s['rtf']}</td></tr>"
                    for s in perf.get("substages") or [])
    st = (rep.get("selection") or {}).get("stats") or {}
    doc = f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Acceptance review</title>
<style>
:root{{--bg:#fff;--fg:#1b1f29;--muted:#667;--card:#f6f7f9;--bad:#b42318}}
@media (prefers-color-scheme:dark){{:root{{--bg:#12151c;--fg:#e8eaf0;--muted:#99a;--card:#1b1f29;--bad:#f97066}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0 auto;max-width:980px;padding:16px}}
.card{{background:var(--card);border-radius:12px;padding:16px;margin:16px 0}}
video{{width:100%;max-height:70vh;background:#000;border-radius:8px}}
.meta,small{{color:var(--muted)}} .bad{{color:var(--bad)}}
table{{border-collapse:collapse;width:100%;font-size:13px}} td,th{{border-top:1px solid #8884;padding:4px 6px;text-align:start}}
tr.sub td{{color:var(--muted);padding-inline-start:18px}}
fieldset{{border:1px solid #8886;border-radius:8px}} textarea{{width:100%;min-height:48px;margin-top:6px}}
button{{font:inherit;padding:8px 14px;border-radius:8px;border:0;background:#2b59c3;color:#fff}}
.scroll{{overflow-x:auto}}
</style></head><body>
<h1>Acceptance review – <bdi>{e(rep['title'] or '')}</bdi></h1>
<p>{st.get('selected', 0)} clips selected out of {st.get('stories', 0)} possible stories (quality bar {(rep.get('selection') or {}).get('threshold')}) ·
{st.get('near_misses', 0)} near misses · {st.get('duplicates', 0)} duplicates removed ·
total processing {(perf.get('total_seconds') or 0) / 60:.1f} min, RTF {perf.get('total_rtf')}</p>
<p><button id=save>Download my ratings (JSON)</button></p>
{''.join(cards)}
<h2>Near misses (rejected)</h2><div class=scroll><table><tr><th>Time</th><th>Score</th><th>Why rejected</th><th>Hook</th><th>Payoff</th></tr>{near}</table></div>
<h2>Duplicates removed</h2><div class=scroll><table><tr><th>Time</th><th>Why</th><th>Kept</th></tr>{dups}</table></div>
<h2>Time per stage</h2><div class=scroll><table><tr><th>Stage</th><th>Seconds</th><th>RTF</th></tr>{rows}</table></div>
<script>
const KEY = 'polixor-acceptance-{e(rep['job_id'])}';
function collect() {{
  const out = {{job_id: '{e(rep['job_id'])}', rated_at: new Date().toISOString(), clips: {{}}}};
  document.querySelectorAll('section[data-clip]').forEach((s) => {{
    const id = s.dataset.clip;
    const r = s.querySelector('input[type=radio]:checked');
    out.clips[id] = {{rating: r ? r.value : null, note: s.querySelector('textarea').value}};
  }});
  return out;
}}
function persist() {{ try {{ localStorage.setItem(KEY, JSON.stringify(collect())); }} catch (e) {{}} }}
try {{
  const saved = JSON.parse(localStorage.getItem(KEY) || 'null');
  if (saved) for (const [id, v] of Object.entries(saved.clips || {{}})) {{
    const s = document.querySelector(`section[data-clip="${{id}}"]`); if (!s) continue;
    if (v.rating) {{ const r = s.querySelector(`input[value="${{v.rating}}"]`); if (r) r.checked = true; }}
    s.querySelector('textarea').value = v.note || '';
  }}
}} catch (e) {{}}
document.addEventListener('change', persist); document.addEventListener('input', persist);
document.getElementById('save').onclick = () => {{
  const blob = new Blob([JSON.stringify(collect(), null, 2)], {{type: 'application/json'}});
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
  a.download = 'polixor-ratings-{e(rep['job_id'])}.json'; a.click();
}};
</script></body></html>"""
    (out / "report.html").write_text(doc, "utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--project", help="project id (from the project page address)")
    g.add_argument("--media", type=Path, help="process this file now")
    ap.add_argument("--language", choices=("auto", "he", "en"), default=None)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    from polixor.config import PATHS
    from polixor.db import init_db
    from polixor import i18n

    PATHS.ensure()
    init_db()
    if args.media:
        media = args.media.expanduser().resolve()
        if not media.exists():
            print(f"File not found: {media}")
            return 2
        job_id = _process_media(media, args.language, "en")
    else:
        job_id = args.project.strip().rstrip("/").split("/")[-1]
    with i18n.use_lang("en"):
        rep = build(job_id)
    out = args.out or Path.cwd() / f"polixor-acceptance-{datetime.now():%Y%m%d-%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False, default=str), "utf-8")
    write_markdown(rep, out)
    write_html(rep, out)
    print(f"\n{len(rep['clips'])} clips · {len(rep['near_misses'])} near misses · "
          f"{len(rep['duplicates'])} duplicates")
    print(f"Report written to: {out}\nOpen {out / 'report.html'} in a browser.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
