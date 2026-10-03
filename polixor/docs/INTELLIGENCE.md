# Polixor intelligence pipeline

How Polixor decides what becomes a Short and a long-form topic video. The
language model is the main decision layer. Phrase lists and audio peaks only
appear in the labelled degraded fallback.

```
MEDIA
 → full-source strong ASR (checkpointed every ~5 min, loops re-heard)      services/transcribe.py, asr_loops.py
 → sentences with IDs, times and turns                                     services/semantic/sentences.py
 → chunk topic maps (overlapping context) → global topic map               services/semantic/topics.py
 → typed candidate proposals per topic (sentence IDs + exact quotes)       services/semantic/candidates.py
 → deterministic validation of every model answer                          services/semantic/validate.py
 → global ranking: rubric + shuffled listwise tournament, dedupe, topics   services/semantic/ranking.py
 → boundary optimisation over sentence combinations, internal cuts         services/semantic/boundaries.py
 → final ASR ensemble per Short (second hypothesis, re-hearing, judge)     services/asr_ensemble.py, asr_engines.py
 → editorial hooks and titles from the final words                         services/semantic/hooks.py
 → final editor: ship / repair / reject (reject only after repair)         services/semantic/editor.py
 → Shorts + long-form topic videos from the same map                       services/semantic/longform_plan.py
 → render (unchanged renderers, overlay, subtitles)                        clip_factory.py, longform_render.py
```

The orchestration is in `services/semantic/run.py`; the job pipeline calls it
from `pipeline._select_semantic`.

## The semantic model

- **Anthropic (primary):**
  - official SDK (`anthropic` in `requirements.txt`);
  - default model `claude-opus-5-5` with adaptive thinking at effort `high` (Settings → AI; `semantic_effort`);
  - structured JSON output (`output_config.format`);
  - the system prompt is cached.
- **Server-side fallback.** Every Anthropic request sets the server-side
  `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`). If the
  model declines a request, the API re-runs it on Anthropic's recommended
  fallback model inside the same call. If a model or platform rejects any
  optional feature, it is dropped one at a time; the content is never changed.
- **OpenAI adapter** (Chat Completions, json_schema) and **Ollama** (local) use
  the same interface.
- **Keys** live only in the server-side secret store (or `POLIXOR_*_API_KEY`
  environment variables). They never reach the browser, the logs, the cache
  or the reports.
- **Response cache.** Every answer is cached on disk by a hash of the request
  (`<job work dir>/intel/llm/`). A resumed or regenerated job never pays for
  the same question twice.
- **Mode `auto` (default):** uses the cloud model when a key is saved,
  otherwise runs degraded mode.

## Grounding rules (code, not prompts)

Every model answer cites sentence IDs and exact quotes. The validator rejects,
with a recorded reason:

- unknown IDs;
- ranges that run backwards;
- quotes not in the cited sentence (one Hebrew prefix letter is tolerated);
- evidence out of role order;
- times that do not match the cited sentences;
- a moment without its closing evidence;
- any claim of who said something (there is no speaker diarization yet).

The ASR judge may only choose between word variants that an ASR hypothesis
actually heard. A hook may not use a number, or a word the final transcript
left unresolved.

## Final transcript of each Short

| Hypothesis | Source |
|---|---|
| A | the strong model on the whole clip |
| B | an independent model: the cloud recogniser if configured, otherwise local `large-v3-turbo` |
| C | the discovery transcript |
| D | a re-hearing of each disagreement window, with context and hints |

How the words are decided:

- **Votes count model families, not passes.** A, C and D are the same strong model.
- **Two families agree:** that variant wins.
- **Otherwise:** the language model chooses between the heard variants, or the word stays **unresolved**.
- **Critical words** (numbers, negations, project names, mixed script) that stay unresolved inside the evidence are re-heard by the editor. If they are still unresolved, the Short is rejected.

## Degraded mode

When no model is usable (no key, the model is switched off, or the model failed):

- the old engine chooses, with its phrase lists, audio peaks, minimum hook/payoff and TextTiling;
- every clip, the selection report (a banner in the UI), the run notes and `intel_report.json` say so, with the reason;
- if the strong ASR model cannot be loaded, the fast model transcribes and the same label applies.

## Checkpoints

Everything resumes:

- **ASR:** chunks of ~5 minutes (`asr_parts/`).
- **Sentences, each topic chunk, the merge, each candidate window, the ranking, every finished Short and every topic video:** `intel/*.json`, each keyed by a hash of its inputs.
- **Final transcripts:** `transcript.final.json`.
- **Model answers:** `intel/llm/`.

A crash during the eighth Short resumes at the eighth. A regeneration with
the same settings makes no model calls. `intel_report.json` records cache
hits and resumed stages.

## What is measured per run (`intel_report.json`, acceptance diagnostics)

- mode and reason;
- model;
- seconds heard by the strong model and seconds read by the semantic layer;
- topics and junk ranges;
- the whole candidate pool with scores;
- rejected proposals with reasons;
- selection decisions;
- editor history per Short;
- final-transcript statistics;
- model use (calls, tokens, cache reads, seconds, failures) per task;
- checkpoint hits;
- time per stage.

## Model calls and cost

The model calls were counted in a scale test: a 4-hour transcript, ten
Shorts, every stage of the pipeline.

| Stage | Calls (4 h) | Prompt size |
|---|---|---|
| Topic map (10-min chunks) | 24 | ~17k chars |
| Topic merge | 1 | ~4k chars |
| Candidates (topic windows) | 72 | ~8k chars |
| Ranking (3 shuffled rounds + final; at most 150 candidates) | 81 | ~4.5k chars |
| Boundaries, hooks, editor (per Short) | 3 × ~11 | 2–4k chars each |
| Long-form topic plans (at most `topic_videos_max`, default 8) | 8 | ~6k chars |

That is about 1.5 M characters of prompt (~0.5–0.75 M tokens of Hebrew
input). The output (JSON plus adaptive thinking at effort `high`) is the
larger part of the bill.

Estimated cost, ±50%:

| Source | Opus 5.5 | Sonnet 5.5 |
|---|---|---|
| 12 min | ~$2–4 | ~$1–2 |
| 1 h | ~$5–9 | ~$2.5–4.5 |
| 4 h | ~$12–20 | ~$6–10 |

On short sources the per-Short calls dominate. The real token counts of
every run are in `intel_report.json` → `usage`, per task.

Ways to lower the cost:

- choose `claude-sonnet-5-5` in Settings → AI;
- set `semantic_effort` to `medium`;
- regenerations are free: they reuse the cache.
