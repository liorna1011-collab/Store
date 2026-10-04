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

Local files go up in 16 MB parts (`/api/uploads`): three at a time, each with a SHA-256
check, each retried with backoff (up to 8 attempts) when the connection or a proxy
fails. The server writes every part straight to its place in one preallocated file –
nothing is held in memory – and only after all parts arrived, the size matches and
ffprobe reads it as a video is it moved (atomically) into the sources folder. Start is
enabled only then. Pause / Resume / Cancel / Retry are on the file card; if the page is
refreshed or closed, choosing the same file again continues where it stopped (the
server keeps the parts for 48 hours). A file that would leave less than 2 GB free on
the server is refused before anything is sent.

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

## Storage

`GET /api/studio/projects/<id>/storage` shows a project's bytes by class: source,
outputs, artifacts (checkpoints), caches (model answers), temp.
`POST /api/studio/projects/<id>/storage/cleanup` deletes **temp only** (render part
folders, `.tmp` files, failed renders, export files no clip points to); add
`?dry_run=true` to see what it would free. It never touches a finished clip, a
checkpoint, the source or a cache, and refuses while the project is processing.

## Cost of the language model (estimate)

Claude Opus 5.5 at $4 / $20 per million input / output tokens; thinking tokens are
output. Estimated from the stage sizes in the code (10-minute topic chunks,
12-minute candidate windows, a 3-round tournament in groups of 6, at most the
requested Shorts + 4 reserves through boundaries, titles and the editor), Hebrew
speech at ~35k transcript tokens per hour. Not measured on a production run yet –
the project's report (`intel_report.json` → `usage`) records the real numbers.

| source | normal | high (many rejections, long thinking) |
|---|---|---|
| 12 min | ~$3–5 | ~$8 |
| 1 hour | ~$7–10 | ~$18 |
| 3 hours | ~$14–18 | ~$35 |

Re-running a project costs almost nothing: identical requests are answered from
the project's cache. Speech recognition runs locally (no API cost; CPU time,
roughly the length of the source in Premium on a 4-core Codespace). The optional
cloud re-hearing (`asr_cloud_fallback`, OpenAI) only touches uncertain words:
well under $1 per hour.
