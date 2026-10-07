# Polixor processing engine

How a project is processed on the server, with no browser involved. Numbers come from
`docs/PERFORMANCE.md`, which records what was measured and where. Nothing in this file is
an estimate presented as a measurement.

## 1. Processes

```
browser ──HTTP/WebSocket──► web server (python -m polixor.main)
                              │  pages, uploads, API, WebSocket, admin
                              │  never runs heavy work
                              │
                              ├── tasks table (SQLite, durable)  ◄── enqueue / cancel
                              ├── event_relay table              ──► WebSocket clients
                              └── supervisor thread: starts and replaces worker processes
                                     │
           ┌─────────────────────────┼──────────────────────────┐
  worker (general) ×N        worker (general)           worker (interactive) ×1
  python -m polixor.workerd  …                          re-renders only
  claims tasks, lease renewed every second, writes events to the relay
```

* **Web server.** Holds no job threads in process mode (`POLIXOR_WORKER_MODE=process`, the
  default of the server entry point). `MANAGER.submit` writes a task; `cancel` sets a flag;
  `is_running` reads the leases.
* **Workers.** Separate OS processes started by the web server's supervisor. The number of
  general workers follows the "parallel projects" setting. One interactive worker is kept
  for re-renders, so a user waiting on an edit never queues behind a 3-hour analysis. Set
  the counts with `POLIXOR_WORKERS=general:2,interactive:1`.
* **Independence.** A closed tab, a phone that went to sleep, or a web restart (`start.sh
  restart`) does not touch a running job. A worker finishes the task it is on with the code
  it started with, then exits. A new worker started by the new web server takes over.
* **Inline mode.** Tests, `run_job` called directly and `POLIXOR_WORKER_MODE=inline` keep
  the old in-process thread pool. The pipeline code is the same in both modes.

## 2. Task queue (`services/taskq.py`)

| Property | How |
|---|---|
| Durable | a row in `tasks`; survives any restart |
| One claim | a single `UPDATE … WHERE id=(SELECT … LIMIT 1) AND status='queued' RETURNING` |
| Lease | 45 s, renewed every second by the worker's main thread |
| Lost worker | an expired lease is returned to the queue and the job resumes from its checkpoints |
| Crash loop | after 3 attempts the job is marked "needs attention" with a Resume button |
| Idempotency | a partial unique index allows one active task per key, so a double click or retry adds nothing |
| Cancel | queued: cancelled at once; running: flag read by the worker within 1 s, FFmpeg terminated |
| Priority | 0 interactive re-render · 10 first results · 20 more Shorts · 40 long-form |

**Long-form isolation.** In a worker, when more urgent work is waiting, a package run stops
after its Shorts and the long-form becomes its own task at priority 40. It resumes from the
selection checkpoint and the finished Shorts are kept. With nothing waiting, it continues
straight away, with no gap.

## 3. Pipeline stages and overlap

```
probe → audio (one decode) → transcription (discovery) ─┐
                     └── visual + layout scan (one decode, overlapped*) ┘
→ analysis → semantic editor (topic map → candidates → ranking → per-Short
   boundaries / final words / editor gate)
       └─ each Short the editor ships is rendered immediately
          (render pool, up to hardware.render_parallel())
→ remaining Shorts → long-form topic videos (own task if others wait)
```

\* The visual scan runs alongside transcription only when that does not slow ASR down:
the ASR runs on a GPU, the machine has 8 or more cores, or `POLIXOR_OVERLAP_VISUAL=1` is
set. On a 4-core CPU-only machine both would compete for the same cores.

**Two-tier ASR.** The discovery transcript covers the whole source. The finalist transcript
(strong model, ensemble, critical-word re-hearing) runs only on the candidates the ranking
kept, before the editor judges them. A subtitle style change re-renders from the stored
words: it never re-runs ASR or the editor.

## 3b. Streaming: Shorts while the source is still being transcribed (`streaming.py`)

The strong ASR transcribes in ~5-minute chunks and each chunk is final when it ends. As soon as
4 minutes are final, an **early window** runs the full semantic pipeline on that part (topic
map → candidates → ranking → editor gate with its one reconstruction → two-model final
transcript → title) on a background thread and renders each Short it ships immediately. The
ASR keeps going meanwhile.

```
ASR  ██████ chunk 1 ██████ chunk 2 ██████ chunk 3 ██████ … ──► full pass (global reconciliation)
                    └─ window 1: editor → Short 1 rendered        (later windows every 20 min of new
                                                                    transcript on long sources)
```

| rule | why |
|---|---|
| same editor, same checks, same final transcript | the Ready-to-Post standard does not change; a window can ship nothing |
| candidates must end 30 s before the last final second | a payoff not yet transcribed waits for the full pass |
| ≤ 1 Short per 10 window-minutes, all windows ≤ half the wanted Shorts | no quota, and room for the best moments of the whole source |
| full pass sees early Shorts as shipped | they count toward the limit; their moments are duplicates; the rest is picked across the whole source |
| only first processing, ≥ 10 min source, a usable model | a re-edit has its transcript already; `POLIXOR_STREAMING=off` disables |

Resume: windows checkpoint under `<work>/stream/w<i>/`, early Shorts in `<work>/stream.json`; a
restarted ASR replays finished chunks, windows answer from checkpoints, rendered Shorts are not
rendered again. Studio projects: the analysis job streams with the settings of the goal the
project was created with; the generation that follows keeps those clips (a later re-edit starts
clean). Cost: discovery (topic map / candidates / ranking) of the windowed text is paid twice;
the run report's `streaming.usage` has the windows' own use and is added to the run's AI cost.

Milestones (`job.milestone` events, `project.milestones`): "Found N promising moments in the
first M minutes", "Short N is ready". Metrics (`run_metrics`, diagnostics → milestones.from_start):
`transcribed_at`, `first_window_at`, `first_candidates_at`, `first_short_at` (**TTFTS**),
`all_shorts_at`, `first_ready_at` (first **Ready-to-Post** Short – the primary KPI).

## 3c. The automatic editor and model routing

Per candidate, inside the normal run (no re-edit button needed):

```
story construction (wide window: hook → context → development → payoff → clean ending)
→ final editor (ship / repair / reject + 5 story checks; a ship answer carries titles + caption)
→ good content, construction failed: ONE reconstruction by the senior editor, judged again by it
→ READY (ship)  |  NEEDS REVIEW (good content, ≤ 2 checks still failing – never "ready")  |  REJECT
```

Model tiers (`settings.ai_routing`, default `balanced`; `premium` = the strongest model for
everything, the old behaviour; `single` = `ai_model` only):

| tier | model (env override) | tasks |
|---|---|---|
| fast | claude-haiku-4-5 (`POLIXOR_MODEL_FAST`) | content profile, topic merge |
| editor | claude-sonnet-5-5 (`POLIXOR_MODEL_EDITOR`) | topic map, candidates, judges, story construction, final editor, long-form |
| premium | claude-opus-5-5 (`POLIXOR_MODEL_PREMIUM`) | escalation only: reconstructing a strong moment and judging the rebuilt cut |

The separate title/hook call is gone by default (the editor's shipping answer carries titles and
caption); it runs only when the optional on-screen hook text is switched on. Every token is priced
at its own model's rate (`costs.MODEL_PRICES`, `POLIXOR_PRICE_<MODEL>=in/out`); the run report
records calls, tokens and cost per model and the premium share (admin only).

Discovery ASR (the whole source) decodes greedily with a short fallback ladder
(`discovery_beam_size=1`); the subtitles of what ships come from the finalist ensemble at
`whisper_beam_size=5` – the expensive recognition runs only on published material.

## 4. Rendering

* **Encoder** (`services/encoders.py`). `hw_accel=auto` (the new default; settings v3
  migrates the old implicit "none") uses NVENC, QuickSync or VideoToolbox only when a real
  1-second 1080×1920 test encode succeeds on this machine. The result is cached in
  `data/encoders.json`.
  * A hardware failure during a real render redoes that render on the CPU and drops the
    hardware encoder for the rest of the process.
  * The quality mapping keeps the CPU path's quality bar: NVENC uses `p6`/`hq`, VBR `-cq crf+1`, and spatial and temporal AQ.
* **Audio mastering.** One FFmpeg pass extracts the audio and measures it (ebur128). The
  two-stage chain encodes once from the source, so there is one AAC generation, not two.
* **Render QC.** Black and freeze detection run in one decode.
* **Parallel renders.** `hardware.render_parallel()`. `POLIXOR_RENDER_PARALLEL` pins it.

## 5. Observability (admin only)

* `<work>/profile.json` for each project. It holds spans (wall, CPU, media seconds, RTF), every
  process started (by program), every full decode of the source, cache hits and misses,
  and model wait per task.
* Admin view sections:
  * the project diagnostics: ranked bottlenecks, processes, full decodes;
  * **Worker processes and queue**: mode, queue counts, live workers, recent tasks, the
    chosen encoder;
  * **Benchmarks**: saved `python -m polixor.bench` runs.
* The customer never sees internal timings beyond their own progress, and never sees costs.

## 6. Benchmark (no paid AI)

```
python -m polixor.bench --minutes 30            synthetic long source + fixture transcript
python -m polixor.bench --source F --fixture T  your own file
```

The benchmark forces `POLIXOR_PAID_AI=off` and uses the deterministic stand-in editor
(`POLIXOR_SEMANTIC_SCRIPTED=1`). It runs the real pipeline in a separate data folder and
reports stage times, RTF, time to first Short / all Shorts / long-form, processes, full
decodes, cache and API latency.
