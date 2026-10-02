"""
Polixor – measure speech-recognition variants against a gold reference.

Transcribes the whole video with each variant (checkpointed: an interrupted
run continues where it stopped) and scores every variant on the gold's
verified evidence: key phrases of the gold moments, verified names and
numbers, hallucination loops, and speed (real-time factor). Unresolved gold
words are not scored.

    python scripts/asr_variants.py --media /path/liberman_news.mp4 [--gold gold/news_liberman.gold.json]
           [--variants strong_beam5_vad,strong_greedy_vad,...] [--out asr_variants_<date>]

Variants (all on your CPU/GPU, nothing uploaded):
  strong_beam5_vad      the production discovery setting (strong Hebrew model, beam 5, VAD, batched)
  strong_beam5_vad_seq  the same without batching
  strong_greedy_vad     beam 1 (faster)
  strong_beam5_novad    no voice-activity filter
  strong_vocab          production + the gold's verified names as vocabulary hints
  general_turbo         the general large-v3-turbo model (the local second hypothesis)
  fast_small            the fast preview model (the old discovery transcript)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

VARIANTS: dict[str, dict[str, Any]] = {
    "strong_beam5_vad": {},
    "strong_beam5_vad_seq": {"asr_batched": False},
    "strong_greedy_vad": {"whisper_beam_size": 1},
    "strong_beam5_novad": {"_vad": False},
    "strong_vocab": {"_vocab": True},
    "general_turbo": {"whisper_model": "large-v3-turbo"},
    "fast_small": {"discovery_asr": "fast", "whisper_model": "auto"},
}


def extract_wav(media: Path, out: Path) -> Path:
    wav = out / "audio.wav"
    if not wav.exists():
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(media), "-ac", "1", "-ar", "16000",
                        "-c:a", "pcm_s16le", str(wav)], check=True)
    return wav


def run_variant(name: str, wav: Path, out: Path, language: str, vocab: list[str]) -> dict[str, Any]:
    from polixor.config import AppSettings
    from polixor.services import transcribe as T

    spec = dict(VARIANTS[name])
    vad = spec.pop("_vad", True)
    use_vocab = spec.pop("_vocab", False)
    s = AppSettings(transcribe_language=language, transcript_provider="faster-whisper", **spec)
    if use_vocab:
        s = replace(s, asr_vocabulary=vocab)
    s.clamp()
    cached = out / f"{name}.transcript.json"
    if cached.exists():
        from polixor.pipeline import _load_transcript

        tr = _load_transcript(cached)
        info = json.loads((out / f"{name}.info.json").read_text("utf-8"))
        return {"transcript": tr, **info}
    orig = T.FasterWhisperProvider._run_chunk
    if not vad:
        def no_vad(self, runner, batched, plan, language, audio_path, c0, c1, total, on_progress, cancel_event,
                   have_output):
            real = runner.transcribe

            def tr_no_vad(audio, **kw):
                kw["vad_filter"] = False
                kw.pop("vad_parameters", None)
                return real(audio, **kw)
            runner.transcribe = tr_no_vad
            try:
                return orig(self, runner, batched, plan, language, audio_path, c0, c1, total, on_progress,
                            cancel_event, have_output)
            finally:
                runner.transcribe = real
        T.FasterWhisperProvider._run_chunk = no_vad
    t0 = time.time()
    try:
        tr = T.FasterWhisperProvider().transcribe(wav, settings=s, checkpoint_dir=out / f"{name}_parts")
    finally:
        T.FasterWhisperProvider._run_chunk = orig
    secs = time.time() - t0
    from polixor.pipeline import _save_transcript

    _save_transcript(tr, cached)
    info = {"seconds": round(secs, 1), "model": tr.model, "loops": len((tr.meta or {}).get("loops") or []),
            "chunks_resumed": (tr.meta or {}).get("chunks_resumed")}
    (out / f"{name}.info.json").write_text(json.dumps(info), "utf-8")
    return {"transcript": tr, **info}


def score(gold, tr, duration: float) -> dict[str, Any]:
    from polixor.evaluation import gold as G
    from polixor.services import asr_loops

    ph_total = ph_ok = 0
    for m in gold.moments:
        for a in m.anchors:
            if a.phrase is None:
                continue
            ph_total += 1
            toks = G.normalize_tokens(tr.text_between(a.t - 1.5, a.t_end + 1.5))
            ph_ok += a.phrase.found_in(toks, gold.salt)
    terms: dict[str, list[int]] = {}
    for t in gold.terms:
        if t.status != "verified":
            continue
        for x in t.times:
            toks = G.normalize_tokens(tr.text_between(x - 2.0, x + 2.0))
            terms.setdefault(t.kind, [0, 0])
            terms[t.kind][0] += 1
            terms[t.kind][1] += t.found_in(toks, gold.salt)
    loops = asr_loops.find_loops(tr.segments)
    return {"phrases": f"{ph_ok}/{ph_total}", "phrase_accuracy": round(ph_ok / ph_total, 3) if ph_total else None,
            **{f"{k}s": f"{v[1]}/{v[0]}" for k, v in terms.items()},
            "loops_in_text": len(loops), "words": sum(len(s.words) for s in tr.segments)}


def main(argv: Optional[list[str]] = None) -> int:
    from polixor.evaluation import gold as G

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--media", type=Path, required=True)
    ap.add_argument("--gold", type=Path)
    ap.add_argument("--language", default="he")
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    media = args.media.resolve()
    out = (args.out or Path(f"asr_variants_{datetime.now():%Y%m%d_%H%M}")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    gold_path = args.gold
    if gold_path is None:
        sys.path.insert(0, str(ROOT / "scripts"))
        import gold_eval

        gold_path = G.find_gold(media, 0.0, gold_eval.GOLD_DIRS)
    if gold_path is None:
        raise SystemExit("No gold reference found for this video (use --gold).")
    gold = G.load_gold(gold_path)
    wav = extract_wav(media, out)
    from polixor.util.wav import wav_duration

    duration = wav_duration(wav) or 0.0
    vocab = []
    if (ROOT / "gold" / "local" / gold_path.name).exists():
        raw = json.loads((ROOT / "gold" / "local" / gold_path.name).read_text("utf-8"))
        vocab = [t["text"] for t in raw.get("terms") or [] if t.get("status") == "verified" and t.get("kind") == "name"]
    rows = []
    for name in [v.strip() for v in args.variants.split(",") if v.strip()]:
        if name not in VARIANTS:
            print(f"unknown variant {name}")
            continue
        print(f"[{datetime.now():%H:%M:%S}] {name} ...", flush=True)
        try:
            r = run_variant(name, wav, out, args.language, vocab)
        except Exception as exc:                        # noqa: BLE001
            print(f"  {name} failed: {exc}")
            rows.append({"variant": name, "error": str(exc)[:200]})
            continue
        sc = score(gold, r["transcript"], duration)
        rows.append({"variant": name, "model": r["model"], "seconds": r["seconds"],
                     "rtf": round(r["seconds"] / duration, 3) if duration else None,
                     "loops_repaired": r["loops"], **sc})
        print(f"  {rows[-1]}", flush=True)
    (out / "asr_variants.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), "utf-8")
    keys = ["variant", "model", "rtf", "phrases", "names", "numbers", "loops_repaired", "loops_in_text", "words"]
    lines = [f"# ASR variants – {media.name} vs {gold_path.name}", "",
             "| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(k, r.get("error", "–") if k == "model" else "–")) for k in keys) + " |")
    (out / "asr_variants.md").write_text("\n".join(lines) + "\n", "utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
