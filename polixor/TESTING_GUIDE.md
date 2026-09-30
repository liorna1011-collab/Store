# Polixor – Testing Guide

There are two ways to test Polixor:

- **A. Live in your browser – no installation (recommended).** Polixor runs
  on a cloud machine in your GitHub account and opens at an HTTPS address you
  can use on your Mac and your iPhone.
- **B. On your own computer** – sections 1–3 below.

---

## A. Live in your browser (GitHub Codespaces)

**You need:** a GitHub account (the one that owns the repository). Codespaces
is included in GitHub's free plan (120 core-hours a month – about 30 hours on
the recommended 4-core machine).

1. **Open this link** and click **Create codespace**:
   https://github.com/codespaces/new?hide_repo_select=true&ref=claude%2Fproject-build-requirements-nragi5&repo=1375783025&machine=standardLinux32gb
   - Optional, on the same page: under *Recommended secrets*, type your own
     `POLIXOR_ACCESS_PASSWORD`. If you leave it empty, a random password is
     created for you.
2. A code editor opens in the browser and sets everything up by itself
   (about **5–8 minutes** the first time: FFmpeg, fonts, Python packages,
   the interface and the transcription model). You don't need to type anything.
3. The file **POLIXOR-PASSWORD.txt** opens in the editor with your password.
4. When setup finishes, Polixor **opens in a new tab** at an address like
   `https://<name>-8756.app.github.dev`. If it doesn't open (pop-up blocker),
   click the **PORTS** tab at the bottom → the globe icon next to
   *Polixor (8756)*.
5. Enter the password. You're in.

**On your iPhone:** in the **PORTS** tab, right-click the address → *Copy
Local Address*, send it to yourself, open it in Safari, sign in to GitHub
when asked, then enter the Polixor password. Add it to the Home Screen to
open it like an app.

**Security:** the address works only for your GitHub account (keep the port
*Private*, which is the default). Polixor adds its own password on top. API
keys you enter are stored encrypted on the codespace and never reach the
browser. The password file is never committed to the repository.

**Password rejected?** In the codespace, open the terminal (menu → Terminal →
New Terminal) and run `bash polixor/scripts/codespaces/fix-login.sh`. It shows
whether a `POLIXOR_ACCESS_PASSWORD` Codespaces secret is in use (then the
secret's value is your password), re-syncs POLIXOR-PASSWORD.txt, restarts
Polixor and confirms that login works. Add `--new` for a brand-new password.

**Later:** the codespace stops after 30 minutes without activity (you can
raise this to 4 hours in GitHub → Settings → Codespaces → *Default idle
timeout*). To continue, open https://github.com/codespaces and click the
codespace. Polixor starts again by itself, and your projects and clips are
still there. When you're done testing, delete the codespace there too.

---

## B. On your own computer

Polixor runs on your computer: the server, the video processing and your
files all stay local. You open it in your browser, and optionally on your
phone over your home Wi-Fi. There is no account and no login.

---

## 1. Get the files

Use **polixor-ready.zip** (the interface is already built inside it):

1. Download `polixor-ready.zip`.
2. **Extract it** (Windows: right-click → *Extract All*). Don't run anything
   from inside the ZIP.
3. You now have a `polixor` folder with `Polixor.bat`, `Polixor-Phone.bat`,
   `scripts`, `sample`, …

> If you download the code from GitHub instead, the interface is not
> prebuilt. On Windows the installer builds it (it installs Node.js via
> winget). On Mac/Linux run `cd frontend && npm install && npm run build` once.

## 2. Start it

### Windows 10 / 11
Double-click **`Polixor.bat`**.

- **First run only:** it installs Python 3.12, FFmpeg and the Python packages
  (5–10 minutes). Allow the Windows permission prompts.
- The browser opens by itself at **http://127.0.0.1:8756**.
- To stop Polixor, close the black window.

### macOS
```bash
brew install python@3.12 ffmpeg      # once (needs Homebrew: https://brew.sh)
cd ~/Downloads/polixor               # wherever you extracted it
bash scripts/run.sh
```
Then open **http://127.0.0.1:8756**. The first run installs packages (a few
minutes). To stop, press `Ctrl+C`.

### Linux
```bash
sudo apt install python3 python3-venv ffmpeg
bash scripts/run.sh
```

## 3. Open it on your iPhone (optional)

The computer and the phone must be on the **same Wi-Fi**.

- **Windows:** double-click **`Polixor-Phone.bat`** instead of `Polixor.bat`.
  If Windows Firewall asks, choose **Allow on private networks**.
- **Mac/Linux:** `bash scripts/run.sh --phone`

The window prints a line such as:

```
Phone / other devices on this Wi-Fi:  http://192.168.1.23:8756
```

Type that address into Safari on the iPhone. Keep the computer on while you
test: the phone is only a screen, and the processing happens on the computer.

> Phone mode has no password, and anyone on the same network can open it.
> Use it only at home or on another private network.

## 4. First project: upload → analyze → generate → export

1. **Language:** choose English or עברית in the top bar. Settings → General
   has the same selector plus a light/dark theme.
2. **New project** → **File from computer** → drag in a video. On the iPhone,
   tap *Choose file* to pick from Photos or Files. Then press
   **Import and analyze**.
   - For a first quick run, use `sample/sample_stream.mp4` (1:30). Its audio is
     synthetic, so it has **no real speech** and gets no meaningful subtitles.
   - To test subtitles and titles, use a real video of someone talking.
3. **Analysis** runs by itself. You'll see progress, then a summary: duration,
   language, transcript, moments, audio, faces, facecam and speakers.
   - **The first real video takes longer:** the transcription model
     (~480 MB) is downloaded once and then works offline.
4. **Choose a mode:**
   - **Short clips:** vertical clips of the best moments (Shorts, TikTok,
     Reels).
   - **Long video:** one 16:9 video built from the whole source, with
     chapters.
5. **Settings.** Set the number of clips, their length, the aspect ratio
   (9:16 / 1:1 / 4:5 / 16:9) and the layout. *Auto* picks per section:
   reaction (content + camera), face tracking or center crop.
   - **Subtitles:** pick a preset and adjust font, size and position. The
     preview is rendered exactly like the export.
6. Press **Generate**. When it finishes you'll see the results.
7. **Results.** Polixor picks clips by story structure: a hook, the context
   it needs, and a payoff. The clip count is a maximum, not a target. If only
   two moments are strong enough, you get two clips; a video with no strong
   moment can return none. The **Selection report** under the clips shows why
   each clip was chosen (hook, payoff, score parts, penalties, where it starts
   and ends), plus the near misses and removed duplicates. Settings → Clips →
   *Quality bar* makes selection stricter or more lenient. Play each clip
   right in the page, then choose how to export:
   - **Download**: one clip as MP4
   - **SRT**: the subtitle file
   - **Download all (ZIP)**
8. **Edit a clip** (the *Edit* button) to try the editor:
   - change the start/end
   - correct subtitle text
   - change the subtitle design
   - switch the aspect ratio or layout
   - change the editing style

   Then press **Export again**. The editor also shows the AI editing plan,
   the quality check and what was done to the audio.
9. **Try the other mode:** *Change settings* → *Change mode* → *Long video* →
   **Generate again**. This reuses the analysis, so nothing is analyzed twice.
10. Your projects are saved. Close everything, start Polixor again, and they're
    still under **Projects**.

### Import from a link
**New project → Link** → paste a YouTube, Twitch, Kick, Google Drive or direct
video link → **Check**.

- **Long videos:** tick *Import only part of the video* and set a range.
- **Twitch/Kick live channels:** you get a *Recording length* option.

This needs internet access to those sites. Private, age-restricted or paid
content only works with your own cookies file (Settings → AI engine → Access
to restricted sources). Polixor never bypasses DRM.

### Notifications

The bell in the top bar counts what you haven't seen yet. The count turns red
when something needs attention. You get a notification when:

- an analysis finishes;
- clips are ready (or ready but some need review, or no clip passed the
  quality bar);
- a clip re-export finishes or fails;
- processing or a live recording fails;
- once publishing is added: something is published or scheduled, a post
  fails, or an account needs to be reconnected.

Notifications are grouped as *Needs attention*, *New* and *Earlier*. Clicking
one opens the project or clip and marks it read. On a phone, the list opens as
a full-width sheet.

### Publishing (foundation)

In **Settings → Publishing**, turn on *Sandbox publishing account*. It gives
you a test platform, so you can try the whole flow without posting anything
anywhere.

1. **Publishing → Accounts → Sandbox → Connect → Approve.** Real platforms
   work the same way: you sign in on the platform's own page, and Polixor
   never asks for your password.
2. **All clips → Publish** on any finished clip. Pick the accounts and check
   the title, then choose *Publish now* or *Schedule*.
3. **Publishing → Queue & history** shows each post: in progress, scheduled,
   published (with a link) or needs attention (with the reason and *Try
   again*).

Who publishes a scheduled post:

- If the platform supports scheduling, the platform publishes it at the
  chosen time, and Polixor doesn't need to be running.
- Otherwise Polixor publishes it at that time, so the Polixor server must be
  running then. That can be a VPS, a cloud server, or a computer that stays
  on. Codespaces isn't required.

If Polixor was off at the scheduled time, it still publishes when it comes
back, as long as it's within the late window (Settings → Publishing). After
that, the post is marked failed so you can decide what to do.

**YouTube** is available now. To connect it:

1. In Google Cloud, create a project, enable *YouTube Data API v3*, and
   create an OAuth client of type *Web application*.
2. Add the redirect URI shown in **Publishing → Accounts → Developer apps**
   to that client.
3. Paste the client ID and client secret into Polixor, then choose
   **Connect** next to YouTube and sign in with Google.

Until Google audits your API project, YouTube makes every upload private.
The Publish dialog warns you about this. After the audit is approved, turn on
*Settings → Publishing → "My YouTube API project passed Google's audit"*.

Scheduled YouTube posts are published by YouTube itself, so Polixor doesn't
need to be running. Details and the documentation check are in
`docs/providers/youtube.md`.

**Instagram and Facebook** connect through one Facebook sign-in. To set it up:

1. In Meta for Developers, create an app of type *Business* and add
   *Facebook Login for Business*.
2. Add the redirect URIs shown under **Developer apps → Meta** to that app.
3. Paste the App ID and App secret into Polixor.
4. Choose **Connect** next to Facebook (or Instagram), sign in, and select
   your Page(s).

Every Page you can post to is added. So is the Instagram professional
account (Business or Creator) linked to each Page.

What you can publish:

- **Instagram:** Reels up to 15 minutes. You can pick a cover frame.
  Instagram can't schedule through its API, so Polixor publishes at the
  scheduled time and must be running then.
- **Facebook:** Reels (3–90 seconds, vertical) or long videos on the Page.
  Facebook schedules both itself: Reels from 10 minutes to 29 days ahead,
  videos up to 6 months ahead.

Until Meta approves your app in App Review, only the app's admins and testers
can connect. Details are in `docs/providers/meta.md`.

**TikTok:**

1. In TikTok for Developers, create an app and add *Login Kit* and the
   *Content Posting API* (Direct Post).
2. Register the redirect URI shown under **Developer apps → TikTok**.
3. Paste the client key and client secret into Polixor, then **Connect**.

The Publish dialog follows TikTok's posting rules:

- It shows which account you're posting as.
- You must choose who can watch; nothing is preselected.
- Comments, duets and stitches start off.
- It asks you to disclose commercial content.
- It shows the music-usage confirmation.

Until TikTok audits your app, posts can only be "Only me", to a private
account. After the audit is approved, turn on *Settings → Publishing → "My
TikTok app passed TikTok's audit"*. TikTok has no scheduling in its API, so
Polixor publishes at the scheduled time. Details are in
`docs/providers/tiktok.md`.

## 5. AI features and what they need

| Feature | Works out of the box? | What to configure |
|---|---|---|
| Transcription (subtitles, titles, analysis) | Yes. Local faster-whisper, free | Needs internet **once** to download the model. Settings → Analysis & transcription lets you pick the model size and CPU/GPU |
| Titles, descriptions, moment finding | Yes. Local heuristic mode, free | Optional: **Settings → AI engine** → *Local Ollama* (free; install [Ollama](https://ollama.com), then run `ollama pull llama3.1`) or *Cloud model* (paste an Anthropic or OpenAI key). **Test AI connection** checks the key |
| AI director, audio mastering, captions, B-roll decisions | Yes, local | Nothing |
| AI images (intro/B-roll/background) | Needs a key | **Settings → AI images** → provider *OpenAI Images* → paste your OpenAI API key. Without a key, pick *Local card (not AI)*: it makes graphic cards so you can test placing images into a clip; they are always labeled "not AI" |
| Background music | Needs your own file | Settings → Editing style → Background music → full path to an audio file you have rights to |
| Link import / live recording | Yes | Internet access to the site |

API keys are stored **encrypted on your computer**. They are only used by the
local server and are never sent to the browser. You can also set them as
environment variables (`POLIXOR_OPENAI_API_KEY`, `POLIXOR_ANTHROPIC_API_KEY`).

## 6. Where things are stored

- **Windows:** `%LOCALAPPDATA%\Polixor`
- **macOS:** `~/Library/Application Support/Polixor`
- **Linux:** `~/.local/share/polixor`

This folder holds your projects, clips, the transcription model and settings.
To start completely fresh, stop Polixor and delete that folder.

## 7. If something goes wrong

| Symptom | Fix |
|---|---|
| Browser says "can't connect" | The black window (or terminal) must stay open. Look at its last lines for the reason |
| "FFmpeg not found" | Windows: run `winget install Gyan.FFmpeg`, then open Polixor again. Mac: `brew install ffmpeg` |
| Transcript says the model couldn't be downloaded | The computer couldn't reach huggingface.co. Check the internet connection or proxy, then press *Analyze again* on the project |
| Link import fails | Update the downloader: `.venv\Scripts\python -m pip install -U yt-dlp` (Mac/Linux: `.venv/bin/python -m pip install -U yt-dlp`) |
| iPhone can't open the address | Same Wi-Fi? Started with `Polixor-Phone.bat` / `--phone`? Firewall allowed? Try the other printed address |
| A clip shows **Needs review** | This is the automatic quality check reporting a real finding, for example loudness off target. The clip is still usable, and the reason is shown on the clip |

## 8. Measure performance on your own video

This runs the real pipeline (real transcription, analysis and rendering) on
one video and reports the time per stage and the real-time factor (RTF:
processing time ÷ video length; 0.25 means four times faster than real time):

```bash
# macOS / Linux / Codespaces (from the polixor folder)
.venv/bin/python scripts/profile_media.py /path/to/video.mp4 --language he
# Windows
.venv\Scripts\python.exe scripts\profile_media.py C:\path\to\video.mp4 --language he
```

Add `--analyze-only` to skip rendering. The report (`profile.md` and
`profile.json`) is written to a new `polixor-profile-…` folder. Please send
it along with any performance feedback.

**Reference measurement (development machine: 4 CPUs, no GPU; 30-minute
720p stream; fixture transcript; 3 short clips rendered):**

| | before | after |
|---|---:|---:|
| Fast profile – analysis | 23.6 s | 7.9 s |
| Fast profile – whole job | 65.7 s | 46.4 s |
| Quality profile – analysis | 115.7 s | 38.4 s |
| Quality profile – whole job | 151.5 s | 73.7 s |

The changes that produced these numbers:

- The profile-face detector now runs only when no frontal face is found.
  The layouts and the facecam box were identical on both test videos.
- In quality mode, visual analysis and layout detection share one decode of
  the video instead of two.
- Silences come from the same audio read as the other audio features, not
  from a separate ffmpeg pass. They match ffmpeg's silencedetect to within
  10 ms.
- A faster histogram gives identical results.

Transcription speed was **not** measured here, because the speech models
can't be downloaded in the development environment. Speech recognition now
uses all physical CPU cores instead of CTranslate2's default of 4, and
`POLIXOR_ASR_THREADS` overrides this. The real before/after numbers come from
`profile_media.py` on your own long stream.

## 9. Acceptance review on your own video

The real test is whether you would post the clips. This produces a review
page for one of your videos, with the following for each clip:

- the clip itself, playable on the page;
- its hook and payoff, and why they were chosen;
- the score parts and penalties;
- where it starts and ends, and why;
- uncertain or corrected subtitle words;
- a *Would you post it?* rating (yes / maybe / no) with a note.

It also lists the rejected near misses, the removed duplicates and the time
per stage with real-time factors.

```bash
# a video you already processed in Polixor (the id is the end of the project address)
.venv/bin/python scripts/acceptance_report.py --project <project id>
# or process a file now
.venv/bin/python scripts/acceptance_report.py --media /path/to/stream.mp4 --language he
```

Open `report.html` from the new `polixor-acceptance-…` folder. Your ratings
stay in the page while you work. **Download my ratings (JSON)** saves them so
they can be sent back for tuning.

To tune the quality bar from those ratings yourself:

```bash
.venv/bin/python scripts/calibrate_from_ratings.py polixor-ratings-<project id>.json
```

It prints every rated clip with its score, the score parts and penalties
that separate "yes" from "no", and a suggested quality bar. Nothing changes
automatically; set the bar in **Settings → Clips → Quality bar**.

When an AI provider is connected, **Settings → Clips → Second opinion from
the language model** lets it review the top candidates. It can only move a
clip's score slightly or reject a weak clip. It never writes subtitle or
clip text.

### Subtitle timing and accuracy

- **Settings → Transcription → Align subtitle timing to the speech** is on by
  default. It checks each subtitle word against the audio. A word never
  appears before the speaker starts or stays after they stop, and words don't
  overlap or flicker. Sentences the recogniser "heard" in silence (for
  example "thanks for watching") are removed. The words themselves never
  change.
- **Precise word timing (forced alignment)** is optional and works in quality
  mode only. Install it first with
  `pip install -r backend/requirements-alignment.txt` (about 1 GB).
- **Cloud second opinion** is optional and works in quality mode only. It
  needs an OpenAI key saved in Settings. Only the few seconds around an
  uncertain sentence are sent.

To compare timing methods on your own video before switching anything on:

```bash
.venv/bin/python scripts/alignment_spike.py --project <project id> --clips-only
# add --truth fixed.srt to compare with subtitles you corrected by hand
```

For each method (raw / energy / forced) it prints how many words start or
end in silence, overlap, or are too short or too long. With `--truth` it also
prints the average start and end error in milliseconds.

## 10. Known limitations

- Undoing a single AI-director decision from the interface isn't available
  yet; the plan is view-only.
- Explanations written during processing are stored in the project's language
  at that time. Opening the project later in the other interface language
  doesn't translate them.
- The interface language is chosen automatically, and there is no selector.
  Israel (from a trusted country header), an Israeli time zone, or a Hebrew
  browser gives Hebrew; anything else gives English. To see the other
  language for testing, add `?lang=en` or `?lang=he` to the address. It lasts
  for that browser tab only; `?lang=auto` returns to automatic.
- No automatic speaker identification (diarization).
- The Docker image is provided but was not built during development.
- Real downloads from YouTube/Twitch/Kick, OpenAI image generation and cloud
  language models were **not** tested in the development environment (those
  sites were blocked there). They are implemented and should work on a normal
  internet connection. Please report anything that doesn't.
