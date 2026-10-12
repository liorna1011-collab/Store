"""Inspect every delivered file of a project through the real HTTP API (what a customer downloads).

python polixor/scripts/inspect_outputs.py URL PASSWORD_FILE PROJECT_ID OUT_DIR

Per clip: Range request on the player URL (206 + Content-Range), full download, ffprobe
(codec / size / fps / duration), EBU R128 loudness + true peak, black frames, three frame grabs,
the SRT (cue order, overlap, inside the duration, Hebrew text). Then the Download-All ZIP
(integrity, entries, manifest). Writes OUT_DIR/inspect.json.
"""
import io
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import httpx

url, pwf, pid, out = sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4])
out.mkdir(parents=True, exist_ok=True)
c = httpx.Client(base_url=url, timeout=300, follow_redirects=True)
c.post("/login", data={"password": Path(pwf).read_text().strip(), "next": "/"})
res = c.get(f"/api/studio/projects/{pid}/results").json()
report = {"project": pid, "summary": res.get("summary"), "clips": [], "zip": {}}


def ffprobe(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(p)],
                       capture_output=True, text=True)
    j = json.loads(r.stdout or "{}")
    v = next((s for s in j.get("streams", []) if s["codec_type"] == "video"), {})
    a = next((s for s in j.get("streams", []) if s["codec_type"] == "audio"), {})
    return {"vcodec": v.get("codec_name"), "w": v.get("width"), "h": v.get("height"),
            "fps": v.get("avg_frame_rate"), "acodec": a.get("codec_name"), "sr": a.get("sample_rate"),
            "duration": round(float(j.get("format", {}).get("duration", 0)), 2)}


def loudness(p):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(p), "-filter_complex",
                        "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True)
    tail = r.stderr[r.stderr.rfind("Summary:"):]
    i = re.search(r"I:\s+(-?[\d.]+) LUFS", tail)
    tp = re.search(r"Peak:\s+(-?[\d.]+) dBFS", tail)
    return {"lufs": float(i.group(1)) if i else None, "true_peak": float(tp.group(1)) if tp else None}


def black(p):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(p), "-vf",
                        "blackdetect=d=0.5:pic_th=0.98", "-an", "-f", "null", "-"], capture_output=True, text=True)
    return [float(x) for x in re.findall(r"black_duration:([\d.]+)", r.stderr)]


def srt_cues(text):
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.strip().splitlines()
        if len(lines) < 2:
            continue
        m = re.match(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)", lines[1])
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        cues.append((g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000, g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000,
                     " ".join(lines[2:])))
    return cues


for clip in res.get("shorts", []) + res.get("long", []):
    row = {"id": clip["id"], "kind": clip["kind"], "group": clip["group"], "title": clip["title"],
           "reported_duration": clip["duration"], "publish_reasons": clip["publish"]["reasons"]}
    if clip["group"] in ("in_progress", "failed"):
        row["error"] = clip.get("error")
        report["clips"].append(row)
        continue
    r = c.get(clip["media"]["video"], headers={"Range": "bytes=0-1023"})
    row["range"] = {"status": r.status_code, "content_range": r.headers.get("content-range"), "bytes": len(r.content)}
    r = c.get(clip["media"]["download"])
    f = out / f"{clip['id']}.mp4"
    f.write_bytes(r.content)
    row["download"] = {"status": r.status_code, "bytes": len(r.content),
                       "disposition": r.headers.get("content-disposition", "")[:120]}
    row["probe"] = ffprobe(f)
    row["loudness"] = loudness(f)
    row["black_segments"] = black(f)
    d = row["probe"]["duration"]
    for k, t in enumerate((0.5, d / 2, max(0.5, d - 0.6))):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(f), "-frames:v", "1",
                        "-vf", "scale=270:-2", str(out / f"{clip['id']}_{k}.png")], check=False)
    s = c.get(clip["media"]["srt"])
    cues = srt_cues(s.text) if s.status_code == 200 else []
    overl = sum(1 for a, b in zip(cues, cues[1:]) if b[0] < a[1] - 0.01)
    outside = sum(1 for a, b, _ in cues if b > d + 0.05 or a < -0.01)
    heb = sum(1 for *_, t in cues if re.search(r"[\u0590-\u05FF]", t))
    gaps = [b[0] - a[1] for a, b in zip(cues, cues[1:])]
    row["srt"] = {"status": s.status_code, "cues": len(cues), "overlaps": overl, "outside_duration": outside,
                  "hebrew_cues": heb, "first_cue_at": cues[0][0] if cues else None,
                  "last_cue_end": cues[-1][1] if cues else None, "longest_gap": round(max(gaps, default=0), 2),
                  "sample": [t for *_, t in cues[:3]]}
    report["clips"].append(row)

r = c.get(f"/api/studio/projects/{pid}/download", params={"kind": "package"})
report["zip"]["status"] = r.status_code
report["zip"]["bytes"] = len(r.content)
if r.status_code == 200:
    z = zipfile.ZipFile(io.BytesIO(r.content))
    report["zip"]["testzip"] = z.testzip()                    # None = every CRC good
    report["zip"]["entries"] = z.namelist()
    man = next((n for n in z.namelist() if n.endswith(("package.json", "manifest.json"))), None)
    if man:
        m = json.loads(z.read(man))
        items = m if isinstance(m, list) else (m.get("clips") or m.get("items") or [])
        report["zip"]["manifest_items"] = len(items)
        report["zip"]["manifest_ready"] = [bool(x.get("publish_ready")) for x in items]
(out / "inspect.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
print(json.dumps(report, ensure_ascii=False, indent=1))
