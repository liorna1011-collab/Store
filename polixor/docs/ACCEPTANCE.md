# Final acceptance: BEFORE vs AFTER on your Hebrew livestream

The upgrade is **not complete** until this test has been run on the same
real Hebrew livestream you used before (about 1 hour, 3.6 GB). That file
is on your computer; it was never available in the build environment, so
this last step has to run on your machine.

## What it compares

`scripts/acceptance_compare.py` runs your video through two versions of
Polixor, the same way the app does (through each version's own API):

- **BEFORE**: the version you tested before the upgrade (git `0cc671b`).
- **AFTER**: the current version.

Each version gets its own data folder, so your normal Polixor projects
aren't touched. The video is linked, not copied.

| You asked for | How it's measured |
|---|---|
| Clip quality, hook quality, context/payoff completeness | **Your blind review.** Clips from both versions are mixed at random, and you answer *Would you post it? / Hook in the first seconds? / Understandable, with a payoff?* Plus an automatic proxy: every clip from **both** versions is scored by the current engine on the **same** transcript. |
| Weak/random clip rejection | Clips that fail the quality bar; clips that start or end mid-sentence. |
| Duplicate suppression | Pairs of clips that tell the same story or overlap in time. |
| Subtitle text accuracy | Word and character error rate against a few minutes you correct by hand (`--reference`), plus a blind side-by-side check of transcript samples in the review page. |
| Subtitle timing / word-highlight timing | Words that start or end in silence, overlap, or are too short or too long. The karaoke highlight follows these same word times. |
| Processing time | Analysis, generation, per stage, and real-time factor (time ÷ video length). |
| Re-export speed | Editing one subtitle line, then re-exporting that clip. |
| Genuinely usable clips | The number you marked *Yes* (and *With small fixes*) per version, after pressing **Reveal**. |

## How to run it (Windows)

1. Open a terminal in the Polixor folder (the git clone).
2. Optional but recommended: make the reference for subtitle accuracy.
   - Pick 3–5 minutes of the livestream with normal talking.
   - Write down exactly what is said, as an SRT whose times are
     **positions in the source video**. A free subtitle editor such as
     Subtitle Edit can do this, with the video open.
   - Save it, for example as `C:\Videos\stream_reference.srt`.
3. Run:

   ```
   .venv\Scripts\python.exe scripts\acceptance_compare.py --media "C:\Videos\stream.mp4" --language he --settings-from "%LOCALAPPDATA%\Polixor\settings.json" --reference "C:\Videos\stream_reference.srt"
   ```

   - `--settings-from` makes both versions use your own settings: Whisper
     model, device and so on. On Windows, Polixor's settings file is
     `%LOCALAPPDATA%\Polixor\settings.json`. If you set
     `POLIXOR_DATA_DIR`, it's in that folder instead. Leave the option out
     to use the defaults.
   - The BEFORE run takes as long as it did last time (hours). To rerun
     only the new version later, add `--only after --out <the same
     folder>`. The BEFORE results are kept.
4. When it finishes, open `compare.html` from the output folder in your
   browser:
   - Watch and rate every clip. You don't see which version made it.
   - Answer the subtitle comparisons.
   - Press **Reveal** to see usable clips per version and the automatic
     measurements.
   - Press **Download ratings** and send me `polixor_blind_ratings.json`
     together with `compare.md` and `compare.json`.

## Second comparison: after the clip-quality pass

The first real test showed that clips were chosen for acoustic peaks
(shouting, a vocal reaction) rather than for what was said. The quality
pass changes how clips are chosen, cut, subtitled and titled. To measure
it on the same livestream, compare the version you already tested
(`1104379`) with the current one.

Use a **new** output folder so the first test's folder (`acceptance_real`)
stays as it is:

```
.venv/bin/python scripts/acceptance_compare.py --media <the same video> --language he --settings-from <the same settings.json> --before-ref 1104379 --out acceptance_quality
```

Both versions now also write `<version>_diagnostics.json`, and
`compare.md` gets a "Diagnostics" section. They record:

- **Speech recognition:** the model that actually transcribed, and the
  profile.
- **Strong-model use:**
  - whether the stronger model ran;
  - how many seconds of audio it heard, and its processing time;
  - how many clip openings it re-checked;
  - the proofreading counts.
- **Selection:** the notes, the topics, each chosen clip's hook type and
  where it came from, and the near misses with the reason each one was
  rejected.
- **Timings:**
  - sub-stage timings;
  - for every render and for the re-export, what the encoder spent its time
    on: size, fps, layout, edit beats, encoder and preset, and CPU cores.

The older version records less, so some fields are empty for BEFORE.

### What changed in the quality pass

- **Hooks:**
  - A question, a shout or a reaction is no longer a hook by itself.
  - A hook needs a reason in what is said: an opinion, a disagreement, a
    real question, a claim, a comparison, a story or emotion.
  - Routine questions ("what day is it?", "are you home yet?") and tag
    questions ("you know?") score as weak openings.
- **Payoffs:** a payoff needs content, such as a verdict, an opinion, a
  disagreement, a comparison or words of reaction. A vocal reaction only
  strengthens one. A clip whose peak is only noise is rejected.
- **Boundaries:**
  - A clip that ends on a setup ("tell them why…") or a real question is
    extended to the answer, or rejected.
  - A bottom line that comes straight after the payoff ("in the end…") is
    included.
  - Trailing tags ("you know?") are cut off the end.
- **Rejected:**
  - private or off-mic talk (calling someone's name, "are you home?");
  - garbled openings;
  - clips that cross a strong topic boundary;
  - hooks that come too late.
- **Recall:** sections where people argue (several opinion, disagreement or
  comparison lines close together) are proposed even when nobody laughs or
  shouts. Topic detection proposes the strongest line of each argued topic.
- **AI editor (optional):**
  - It uses the language model you already configured.
  - Every judgement must quote the transcript exactly, otherwise it is
    discarded.
  - It can suggest new boundaries, but only as sentence numbers, and the
    rules re-check them before they are used.
  - Without a model, everything works the same, minus this step.
  - Optional local examples: `<data folder>/clip_judge_examples.json`, a list
    of `{"text", "label", "why"}`. It's never part of the repository.
- **Hebrew accuracy:** the stronger model re-checks the first 8 seconds of
  every chosen clip, even when the fast pass was confident. A word changes
  only when the stronger model is clearly better. The time this takes is
  recorded. If the stronger model is unavailable, a note says so and the
  clips are made as before.
- **Subtitles:**
  - Hesitations ("אה", "אממ") and stutters ("אני אני") are removed.
    Deliberate repetition ("לא לא לא") and personality words ("אחי") stay.
  - Each kept word keeps its exact time, so the word highlight stays in
    sync.
  - A full subtitle breaks at the last comma or full stop.
  - A word at the very end of a clip is no longer lost.
- **Titles:** the strongest line of the clip, in the creator's own words,
  without empty openers or trailing tags, cut at a phrase boundary.

The regression fixtures for these patterns are synthetic (in
`backend/tests/test_clip_quality.py`). They reproduce each failure pattern
without storing any of the livestream's words. Keep any real excerpts you
use for checking in `backend/tests/fixtures/local/`. Git ignores that
folder.

## If the BEFORE run was interrupted

A long BEFORE run can be cut off, for example when the Codespace stops.
Don't delete the output folder, and don't start BEFORE again from zero.

The BEFORE version keeps its own checkpoint, and the kit can resume from it:

```
.venv/bin/python scripts/acceptance_compare.py --media <the video the BEFORE run used> --out <the same output folder> --inspect-before
.venv/bin/python scripts/acceptance_compare.py --media <same video> --out <same folder> --settings-from <folder>/before_data/settings.json --resume-before
```

### `--inspect-before`: read-only report

This step changes nothing. It works on a copy of the database, opened
together with its `-wal`/`-shm` files. It reports:

- which stages completed;
- where the run stopped;
- whether the project is resumable;
- whether `transcript.json` and `audio16k.wav` are complete;
- what will run again.

A file is reused only when all of the following can be proven:

- **The stage completed** in the old version's own checkpoint (`completed_stages`).
- **The audio is whole.** `audio16k.wav` has a valid header, and all of
  its data is on disk. That data matches the video's length.
- **The transcript is complete and real.** `transcript.json` parses and
  was made by the configured speech recogniser, with no fallback note. It
  has the same number of segments as the database and the video's length.
- **The files belong to this run.** They are inside this output folder's
  `before_data` and belong to that folder's only project.
- **It is the same video.** The project's video is the file given with
  `--media`.
- **The code is the BEFORE commit.** The BEFORE code is exactly `0cc671b`,
  with no local changes.

If any check fails, nothing is resumed or changed.

### `--resume-before`

It first backs up the database and logs to
`before_data/_backup_before_resume_<time>/`. It then starts the BEFORE
version on the same data folder and calls the old version's own retry,
which continues from the last completed stage. The retry is
`POST /api/jobs/<id>/retry?from_start=false`.

The old `POST /api/projects/<id>/analyze` is never used for this: it
deletes the transcript and resets the checkpoint.

What happens on resume:

- The probe runs again, which takes seconds.
- Audio extraction and transcription are skipped.
- The interrupted analysis runs again **from the start of that stage**. The
  old version saves analysis only when it finishes, so the missing part is
  really computed, not filled in.
- Clip generation and the re-export test then run normally.

**BEFORE's analysis time** is the sum of the stage times the old version
recorded, each stage counted once. The interrupted partial attempt is not
counted, and nothing is estimated.

For the old version, the recorded stage times match wall-clock time within
about 3%: in an uninterrupted check they came to 134.6 s against 138.5 s.
AFTER's analysis time stays wall-clock. The newer version also does some
work outside its recorded stages, so a "recorded stage times" figure would
understate it and isn't used for the comparison. Per-stage times for both
versions are in `compare.json`.

`before.json` also records a SHA-256 for each reused file and confirms they
were unchanged after the run.

Only an interrupted **analysis** can be resumed this way. If the run stopped
during clip generation, the tool refuses rather than combining partial
outputs.

### Checked here, on a simulated cut

1. A BEFORE run on the 30-minute test file was hard-killed at 80% overall,
   inside the analyze stage (video frames).
2. It was inspected.
3. It was resumed with these commands.
4. The resumed run produced **exactly the same clips and subtitles** as an
   uninterrupted BEFORE run on the same file.
5. The reused audio and transcript were byte-identical afterwards.
6. A truncated WAV, a cut-off transcript, files from another run, and the
   wrong video were each refused, with nothing changed.

## What was verified here, and what wasn't

- The kit was run end to end in the build environment on both real
  versions (`0cc671b` and the current code), using the 90-second Hebrew
  test video with its prepared transcript. Whisper can't be downloaded
  there. Both versions started, analysed, generated clips and
  re-exported, and the report and blind review page were produced (desktop
  and 390 px, no errors).
- A 30-minute synthetic long file was also run as a long-source proxy.
  Its numbers are in the table below.
- **Neither is your livestream.** The 90-second clip has a prepared
  transcript and no real speech recognition, so its subtitle-accuracy and
  speed numbers don't predict the real result. That's why the real test
  is still required.

## Proxy results from the build environment (not your livestream)

These runs used both real versions end to end: the same file, the same
prepared Hebrew transcript, and the same machine, with nothing else
running.

The 30-minute proxy is the 90-second Hebrew test video repeated. Because
every region tells the same story, duplicate counts are inflated for any
version that doesn't suppress duplicates. Treat these numbers as a check
that the kit works and as rough speed ratios, not as your result.

| Metric (30-min proxy) | BEFORE `0cc671b` | AFTER |
|---|---|---|
| Clips produced | 5 | 2 |
| Pass the quality bar (one yardstick) | 4 | 1 |
| Mean clip score / hook / payoff | 0.53 / 0.29 / 1.00 | 0.68 / 0.68 / 0.80 |
| Start mid-sentence / end mid-sentence | 4 / 5 | 0 / 1 |
| Duplicate pairs | 10 | 0 |
| Subtitle words timed into silence or overlap | 9.4% | 0% |
| Analysis / generation / total | 138 s / 151 s / 289 s | 39 s / 27 s / 66 s |
| Real-time factor | 0.161 | 0.037 (4.4× faster) |
| Re-export after a subtitle edit | 22.9 s | 6.9 s (3.3× faster) |

On the 90-second file: BEFORE made 2 clips, and neither passed the quality
bar; AFTER made 1 clip, which passed. Total time was 66 s vs 18 s, and
re-export 19.5 s vs 9.3 s. These timings were taken while other tests
were running.

**What these numbers don't show:**

- Real Whisper speed and accuracy. The transcript was prepared in advance,
  so transcription took no time in either version.
- Subtitle word error rate: there was no real speech recognition to
  measure.
- Whether a person would post the clips.

Those three answers are what the run on your livestream is for.

## Known issues and open items

1. **The real livestream test hasn't been done.** Until it has, the
   upgrade is not complete.
2. **Real speech recognition wasn't measured here.** Hugging Face is
   blocked in the build environment, so Whisper couldn't be downloaded.
   Hebrew word accuracy and transcription speed come only from your run.
3. **Publishing to real accounts wasn't tested.** YouTube, Instagram,
   Facebook and TikTok were tested against mocked APIs and the sandbox.
   Nothing was ever posted. Two further limits:
   - Until Google and TikTok audit your apps, uploads are private or
     "Only me".
   - Scheduling on platforms without native scheduling needs the Polixor
     server running at the scheduled time.
4. **OpenAI image models weren't called for real.** No real key was used.
   The model parameters come from the official docs, read through search
   results because developers.openai.com is blocked here. Check once with
   your key. Transparent backgrounds on `gpt-image-2` are a preview
   feature at OpenAI.
5. **No mask drawing in the Studio.** The API supports masked edits
   (inpainting), but the Studio has no tool to draw a mask yet. Edits are
   described in words.
6. **AI social metadata was tested only with a mocked language model.**
   Without a connected model, suggestions come from the transcript and
   are labelled as such.
7. **The yardstick is an approximation.** The automatic clip score in the
   comparison rebuilds each clip's story from its start and end. It can
   disagree with the engine's own decision for a clip. In the 30-minute
   proxy, one AFTER clip the engine kept scored just under the bar. The
   blind human review decides.
8. **Carried over from before:**
   - The site walkthrough still reports that the director decision
     controls in clip editing (§32) exist in the API but have no screen.
   - Explanations written during processing stay in the project's
     language at that time.
   - Image placements appear in the video on the next export. A clip
     thumbnail applies at once.

## Cleaning up afterwards

The BEFORE version is checked out as a git worktree inside the output
folder. When you're done:

```
git worktree remove --force <output folder>/_before_src
```

Then delete the output folder.
