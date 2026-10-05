# Production hardening – why the site could freeze, and what changed

Found by reading every request handler, the job worker, the event bus, the websocket, the API
client and every poller and effect – then confirmed with tests (`tests/test_hardening.py`) and
a real-browser click-through (desktop + mobile, English + Hebrew).

## Root causes found

| # | Cause | Effect for the user | Fix |
|---|---|---|---|
| 1 | `/api/upload` and the image upload were `async` handlers doing synchronous disk writes, **ffprobe** and image decoding | every other request (all pages, progress) stalled while one file was probed | plain handlers (thread pool) – the event loop never waits |
| 2 | Chunk upload hashed each 16 MB chunk (SHA-256) **on the event loop**, and hopped to a thread once per ~64 KB | the site answered slowly during big uploads | hashing + writing in a worker thread, 1 MB per hop; measured: `/api/health` p95 < 0.5 s while four 32 MB chunks upload (test) |
| 3 | Clip re-export rendered with FFmpeg **inside the request** (minutes), no guard against double click | the button "hung"; leaving the page lost track; two clicks = two renders | background render pool sized to the machine; the page follows the clip; one render per clip; interrupted renders reported at restart; a failed re-edit keeps the previous file |
| 4 | ZIP downloads were built completely in a temp file before the first byte; two downloads of a project wrote the **same** temp file; the clip ZIP was fetched into page memory as a Blob | multi-GB package looked frozen; corrupt/missing archive when two downloads overlapped; browser tab could crash | streamed ZIP (ZIP64, stored), no temp file; native browser download |
| 5 | `fetch` had **no timeout** | a stalled proxy connection = a spinner forever | every request has a timeout (30 s; 2 min for link checks/AI), GET retried twice on transient failures, clear message otherwise |
| 6 | Every `job.progress` websocket event re-rendered the whole app (several per second per job); a reconnect never fetched what it missed | sluggish UI during processing; stale state after Wi-Fi blips | progress batched to ≤ 2 renders/s; refresh on reconnect |
| 7 | Project page and dashboard reloaded on every event – overlapping requests, an older answer could overwrite a newer one | request storms during generation; flicker / stale data | coalesced loader: one request at a time, one trailing reload |
| 8 | Results page polled with `setInterval` (overlapping, also in hidden tabs, toast on every failed poll) and every card loaded its MP4 (`preload="metadata"`) | heavy network on open; toast spam | one poll at a time, visible tab only, quiet errors; `preload="none"` + poster |
| 9 | Project list: ~4 queries per project + an aggregate over all stage timings per running project; no pagination; studio metrics: 2 queries per project; no index on `jobs.created_at` | dashboard slowed with project count | constant queries per page (tested 10 vs 100), pages of 30 with "Show more", indexes |
| 10 | `intel_report.json` (MBs on long sources) parsed on **every** results/diagnostics poll | CPU spikes on the server while polling | parsed once per file version (mtime cache) |
| 11 | A 401 redirected instantly to the login page | unsaved form/upload context lost | "Your session expired. Sign in again." dialog; sign-in returns to the same screen; uploads pause and continue |
| 12 | A job whose worker died, or a stage that hung (model call, ASR, FFmpeg), stayed "processing" forever; a late thread could overwrite a decided state | endless spinner | heartbeats + watchdog: orphan → resumed from checkpoints; stalled → **Needs attention** with the stage + **Resume**; validated status transitions |
| 13 | A "verifying" upload interrupted by a restart stayed busy forever | the file could never be finished | a stale verification may run again |
| 14 | Create project: idempotency scanned 1000 jobs and was racy (two simultaneous Starts → two projects) | duplicate projects (and, with billing, double charges) | unique idempotency key (index), browser sends one key per press |
| 15 | Code splitting (new) + a dropped connection → a page's code cannot load | blank screen | error boundary: clear message + Try again (found by the browser test) |
| 16 | Network error hint told customers to run `scripts/run_backend` | confusing developer text | customer wording in both languages |

## Speed

* Initial JS 668 KB → 407 KB (pages load when opened).
* Uploads: adaptive parallelism 2–6, chunk size from the measured link (8–32 MB), hashing off the
  main thread; telemetry for the admin view.
* Hardware-aware: CPU count from the container quota (not the host), RAM, GPU (CUDA via
  CTranslate2), disk, load → render workers, ASR threads, upload concurrency bound
  (`services/hardware.py`, overridable by env).
* Profiling per stage (admin): wall, this thread's CPU, FFmpeg/child CPU, disk read/write,
  waiting (model/network/disk), queue time before a worker picked the job up, model calls and
  cache hits by task.
* Pipeline speed work from the previous pass stays: Shorts render as they ship, strongest first;
  long-form planned after the Shorts; every expensive step cached/checkpointed. Quality gates
  (story checks, editor gate, subtitle verification, final QC) are unchanged.

## Not done / limits

* Production speed targets (30 min source → first Short ≤ 5–10 min) were **not measured** here:
  this container has no GPU and no paid model calls were allowed. The small-source benchmark and
  the profiling tools are in place; measure on the target machine with the admin diagnostics.
* Billing is single-account ("default"); the model and ledger carry `account_id` for later.
* The in-process lock makes reservations atomic for the single server process `start.sh` runs;
  SQLite's write lock (`BEGIN IMMEDIATE`) covers a second process.
