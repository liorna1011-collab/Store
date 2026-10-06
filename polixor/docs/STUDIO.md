# Polixor Studio – using Polixor without scripts

Normal use is the website: upload → start → leave → come back → watch → rate →
download. The acceptance scripts (`scripts/acceptance_compare.py`,
`scripts/codespaces/semantic-blind-test.sh`) remain for developers only.

## Start

Codespaces (first time it installs; afterwards it starts in seconds, rebuilds the
interface when it changed, and keeps running jobs):

    bash polixor/scripts/codespaces/start.sh restart

Open port **8756** from the PORTS tab (password protected; the password is in
`POLIXOR-PASSWORD.txt`). Add your Anthropic key once in **Settings → AI** – without it
Polixor runs in a labelled no-AI mode and never marks a clip ready to post.

## The flow

1. **New project** – drop a video (or a supported link). Choose what to make:
   *Content package* (Shorts + long-form), *Shorts only*, *Long-form only*, or
   *configure after the analysis*. Advanced: content type (auto-detected:
   livestream, podcast/interview, news/political, solo, general), quality
   (Premium – the reference; Fast – lighter speech recognition, same editor and
   checks), how many Shorts **at most**, their length, and the on-screen hook text
   (off by default).
2. **Start** – the analysis runs, then generation starts by itself. Close the
   browser if you like: everything runs on the server, and every expensive step
   is checkpointed. If the server restarts (Codespace slept, `start.sh restart`),
   the job continues from its last checkpoint by itself.
3. **Results** – Shorts and long-form tabs. Up front: only what passed every final
   check (*Ready to post*). Below, collapsed: outputs that need attention or were
   not checked by the editor, failed outputs with their reason, and the candidates
   the editor turned down with its reason. Every video plays from the server.
   Download one clip, all Shorts, the long-form videos, or the whole package (with
   `package.json`: titles, captions, why each clip was chosen). *Re-edit* opens the
   clip editor (boundaries, subtitles, layout) without reprocessing the source.
4. **QA mode** (switch on the results page) – rate each output: would you post it
   (yes / small fix / no), hook, story/payoff, subtitles (good / wrong text /
   timing), edit (good / bad cut / pacing / framing / other), note. Ratings are
   saved to the project; *Export ratings* downloads them as JSON together with what
   the system knew about each clip. `GET /api/studio/metrics` aggregates approval
   rate of surfaced clips, small-fix, rejection, hook/story/subtitle failure rates.
   Nothing is learned automatically from ratings.

## Uploading large videos

Local files go up as byte ranges (`PUT /api/uploads/<id>/range?offset=N`), each with a SHA-256
computed in a Web Worker; the server records which byte ranges arrived complete, so the size of a
request can change at any moment without losing anything already sent.

* **Request size is deployment-aware** (`GET /api/uploads/transport`). Behind the GitHub Codespaces
  port forwarder (`*.app.github.dev`) large request bodies are refused with HTTP 413 before they
  reach Polixor: there an upload starts at 4 MiB, may grow only after a size succeeded six times in
  a row, and never past 8 MiB, with at most 4 requests in flight. Elsewhere: 8 MiB, up to 32 MiB.
  `POLIXOR_UPLOAD_MAX_REQUEST_BYTES` caps both (another proxy in front).
* **A 413 is adapted to, not retried:** the refused part is split in half and sent again, the user
  sees *"Adjusting upload for this connection…"*, the refused size is remembered for that address
  and never tried again; the size that worked is where the next upload starts.
* Other failures by kind: expired sign-in (Polixor's or the Codespaces port's) → paused, continues
  after signing in; network / timeout / 502-504 → retried with backoff and fewer parallel requests;
  429 → waits Retry-After; another 4xx → stops with the server's reason.
* Pause / Resume / Cancel / Retry on the file card; after a refresh, choosing the same file again
  continues from the confirmed bytes (kept 48 hours). A file that would leave less than 2 GB free is
  refused before anything is sent. Every failed request is recorded (size, status, request id) in
  the admin view.

## What "ready to post" means

A clip is *ready to post* only when all of these hold:

* the final editor shipped it: opening hooks, understandable alone, real payoff,
  clean ending, good pacing (one repair round at most, then it is rejected);
* the rendered file passed the render check (streams, duration, black/frozen
  frames, sampled frames);
* audio is sound;
* the subtitles have no timing defect left and no uncertain critical word
  (a name or number the audio could not confirm).

There is no quota: if only four moments are good, you get four.

## Speed and the editor's decisions

* The editor judges every candidate on the discovery transcript (the strong model in
  Premium). Only a clip it ships gets the expensive part: the two-model final
  transcript, verification of names/numbers, the title, the render.
* A strong moment that fails on construction (no payoff yet, missing context, weak
  opening, bad ending, pacing) gets one targeted repair before it can be rejected.
  A weak moment is rejected at once.
* An editor that cannot be reached is *not evaluated*, never a rejection; *Retry the
  editorial review* (Diagnostics) judges only those clips – everything else is cached.
* A Short is rendered the moment it ships, while the editor judges the rest; Shorts
  render strongest first; in a content package long-form is planned only after the
  Shorts are out, so it never delays them. Finished outputs appear in Studio as they
  are made.
* If nothing ships, the two closest calls are rendered under *Needs attention* with
  the exact failed check – never as *Ready to post*.
* **Diagnostics** (project page) shows the time per stage, the RTF, time to first
  Short / all Shorts / long-form, the top bottlenecks (model calls, tokens and cache hits are
  in the admin view only), and
  why each candidate did not ship – computed from what the run recorded, also for
  projects made before this version.

## Storage

`GET /api/studio/projects/<id>/storage` shows a project's bytes by class: source,
outputs, artifacts (checkpoints), caches (model answers), temp.
`POST /api/studio/projects/<id>/storage/cleanup` deletes **temp only** (render part
folders, `.tmp` files, failed renders, export files no clip points to); add
`?dry_run=true` to see what it would free. It never touches a finished clip, a
checkpoint, the source or a cache, and refuses while the project is processing.

## Plan and minutes (what the customer sees)

The customer sees **source video minutes** only – e.g. *Starter · ₪99/month · Used 126 of 300
minutes · 174 remaining* (home page and Settings). Never tokens, dollars, model calls or machine
time.

* **A billable minute** is the probed length of the source (ffprobe on the uploaded file, or the
  imported section / live capture). Re-renders, subtitle edits, downloads, re-opening a project
  and QA ratings are never counted.
* **Rounding:** exact milliseconds are stored and summed; display rounds the totals once, to whole
  minutes, half up (59 s → 1, 61 s → 1, 90 s → 2, 28:34 → 29, 59:59 → 60). Remaining = plan minus
  the shown used minutes, so the two always add up. A hundred 59-second videos are 98 minutes.
* **Lifecycle** (append-only ledger, `services/billing.py`): nothing while uploading → *reserved*
  after the probe, before anything runs (a video that does not fit is refused: *"You have 18
  minutes remaining. This video is 42 minutes."* – the New project page says it before Start) →
  *committed* when processing starts → *released* when processing never started, when our own
  failure came before the transcript, or on a cancel the policy refunds.
* **Idempotent:** every step has a unique key – double click, refresh, API retry, server restart,
  worker resume, Resume after "needs attention" never charge twice. Reservations are atomic
  across projects (one lock + one SQLite write transaction).
* **Cancel policy** (`billing_cancel_policy`): `refund_before_output` (default: refunded unless a
  clip already exists), `refund_before_commit`, `never`.
* **Billing period:** monthly from the account's start; rolls forward by itself.
* Plans: Starter 300 min / ₪99, Pro 900 / ₪249, Studio 2400 / ₪599 (`PLANS`).

## Reliability

* Every request ends: 30 s timeout (2 minutes for link checks and AI calls), GET retried twice on
  a network/proxy failure; a clear message otherwise – never a stack trace or a path.
* An expired sign-in shows *"Your session expired. Sign in again."*; signing in returns to the
  same screen; uploads and jobs are kept.
* Heavy work never runs on the web server's event loop: uploads hash and write in worker threads,
  re-exports render in a background pool (the page follows the clip; leaving it never cancels the
  render; a failed re-edit keeps the previous file), ZIP downloads are streamed.
* **No job stays "processing" forever:** a watchdog sees a job whose worker died (it continues from
  its checkpoints, up to 3 times) and a stage that reports nothing for too long (ASR 40 min, model
  stages 45 min, renders 30–45 min; `POLIXOR_STALL_MINUTES` scales them). Such a job shows **Needs
  attention** with the stage, and **Resume** continues from the last checkpoint – no new charge.
* Job status changes only along validated transitions (a closed job is never reopened by a late
  thread).

## Versions, sessions, failed actions

* Every response carries `X-Request-Id` and `X-Polixor-Build`. A tab running an older interface than
  the server serves shows *"A new version of Polixor is available. Reload."* and reloads by itself
  (once; it waits while an upload runs). The page itself is never cached; its scripts are.
* An expired sign-in – Polixor's, or the Codespaces port's (its forwarder redirects to GitHub) – opens
  *"Your session expired. Sign in again."*, also when it shows up as a screen that cannot load.
* A failed action shows friendly words and a reference ("Ref …"); the admin view (Failed actions)
  shows the route, status, kind, time, request id and project – no DevTools needed. Logged to
  `<data>/logs/events.jsonl`, without bodies, headers or secrets.
* `POLIXOR_PAID_AI=off` refuses every paid AI call (Anthropic, OpenAI) before it leaves the machine.

## Admin / developer view (internal)

`/admin` (not linked in the interface) – needs the admin token: `POLIXOR_ADMIN_TOKEN`, or the file
`admin.token` in the data folder (created at first start, mode 600; `start.sh` prints where).
It shows per project: source length, minutes charged, AI cost, infrastructure estimate, revenue,
gross margin, model calls/tokens/cache hit rate; the usage ledger (plan change, manual credit);
upload telemetry (size, average/peak Mbps, retries, failed chunks, resumes, chunk size,
concurrency, finalize time); job health; machine profile and tuning. The full diagnostics of a
project (with per-stage CPU / FFmpeg CPU / disk / waiting and queue time) are at
`/api/admin/projects/<id>/diagnostics`. Customer routes never carry any of it.

### Cost of the language model (estimate, internal)

Claude Opus 5.5 at $4 / $20 per million input / output tokens; thinking tokens are
output. Estimated from the stage sizes in the code (10-minute topic chunks,
12-minute candidate windows, a 3-round tournament in groups of 6, at most the
requested Shorts + 4 reserves through boundaries, titles and the editor), Hebrew
speech at ~35k transcript tokens per hour. The admin view shows the measured numbers per project.

| source | normal | high (many rejections, long thinking) |
|---|---|---|
| 12 min | ~$3–5 | ~$8 |
| 1 hour | ~$7–10 | ~$18 |
| 3 hours | ~$14–18 | ~$35 |

Re-running a project costs almost nothing: identical requests are answered from
the project's cache. Speech recognition runs locally (no API cost).
