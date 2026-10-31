# RonenGG Daily: Subscribe Overlay

A 5-second animated subscribe overlay with true transparency and synced sound, built from the original RonenGG subscribe artwork.

## Files

| File | What it is |
|---|---|
| **`RonenGG_Subscribe_Overlay_1080x1920_60fps_ProRes4444.mov`** | **Main file.** 1080×1920, 60 fps, 5.000 s (300 frames), Apple ProRes 4444 (`ap4h`) with a real alpha channel, BT.709, 48 kHz stereo PCM audio |
| `RonenGG_Subscribe_Preview_over_sample_video_1080x1920.mp4` | H.264 preview: the MOV composited over a moving sample background (built from the decoded MOV, so it shows the real alpha) |
| `RonenGG_Subscribe_Still_Subscribed_HiRes.png` | Transparent PNG of the final (subscribed) artwork at full source resolution (2016×679) |
| `RonenGG_Subscribe_Still_Original_HiRes.png` | Same, in the original (before-click) state |
| `RonenGG_Subscribe_Still_Subscribed_1080x1920.png` | The final state as a full 1080×1920 transparent frame, in the exact position used by the video |
| `RonenGG_Subscribe_Audio_48k_24bit.wav` | The sound mix on its own (48 kHz / 24-bit), already in sync from 0:00 |
| `landscape-16x9/` | Bonus: the same animation on a 1920×1080 canvas as a centred lower third, for regular (non-Shorts) videos, plus its preview |
| `layers/` | The artwork split into 10 independent, transparent 2000×668 layers (aligned; stack them at 0,0 in the numbered order) |
| `project/` | The full production project: source artwork, scripts and `build.sh` to rebuild everything |
| `qc/` | Quality-control evidence (see below) |

## Using it in CapCut

1. **Desktop:** import the `.mov`, then drag it onto a track **above** your main video. The transparency is read from the file, so there is nothing to key out.
2. **Placement:** leave it at 100% scale and position 0,0. It is pre-positioned for Shorts: at rest the graphic sits at x ≈ 75–975, y ≈ 1000–1290. That keeps it clear of the top bar, the right-hand action buttons and the bottom title area (see `qc/qc_safe_area.png`).
3. **Audio:** the sound effects are already in the file. Lower the clip's volume if your voice-over runs over it.
4. **Regular 16:9 videos:** use `landscape-16x9/RonenGG_Subscribe_Overlay_1920x1080_60fps_ProRes4444.mov` the same way.

**Limits of what was tested:**
- **CapCut itself was not opened.** The build ran on a Linux server where CapCut can't run. What *was* checked: the file is standard ProRes 4444 (`ap4h`, `yuva444p12le`, straight alpha, BT.709 tags, PCM audio), the kind After Effects, Premiere and Final Cut export for transparent overlays. It was decoded back with FFmpeg, and transparency, frames and audio were verified from that decode.
- **Phones:** some Android phones can't decode ProRes. If the clip won't import there, use CapCut desktop.

## The animation (5.0 s)

| Time | What happens |
|---|---|
| 0.00 | Fully transparent frame |
| 0.00 – 0.55 | Purple light streaks and energy particles gather; the round logo flies in from the left with motion blur, overshoots slightly and settles |
| 0.22 – 0.66 | The subscribe panel slides and scales out from behind the logo |
| 0.34 – 0.85 | The bell box enters on its own from the right with a rotation; the bell wiggles as it lands |
| 0.45 – 0.95 | The spiky energy band ignites behind everything, from the logo outward |
| 0.95 – 1.47 | A clean white cursor enters from outside the graphic and glides to the button |
| **1.550** (frame 93) | **Subscribe click:** button face presses into its bezel and springs back; a light pulse runs around the border; sparks burst; the face turns from bright purple to dark "subscribed" violet (radial wipe from the click point); the YouTube play tile flips over and a check mark draws on |
| 1.92 – 2.40 | Cursor moves to the bell |
| **2.500** (frame 150) | **Bell click:** bell compresses, then swings and settles (damped oscillation); the arcs flash; two waves of notification arcs expand outward; a short purple shockwave ring (with slight refraction) |
| 2.72 – 3.12 | Cursor leaves and fades out; no cursor remains in the artwork |
| 3.30 – 4.00 | Hold on the subscribed state with gentle neon breathing and a few floating motes |
| 3.86 – 4.08 | The energy band dissolves into sparks |
| 4.02 – 4.80 | Exit with motion blur and particle trails: bell first (out to the right), then the panel (back into the logo), then the logo (out to the left) |
| 4.87 – 5.00 | Fully transparent (frames 292–299) |

**Sound** is synthesized and placed by the same timeline as the picture:
- soft entrance whoosh and landing thump
- panel and bell-box swishes
- soft cursor-movement airs
- a two-part tactile mouse click (press, then release) on both clicks
- a neon energy pulse on subscribe
- a two-note bell chime with a softer swing-back strike, panned right toward the bell
- three directional exit whooshes

There is no music. Measured loudness is −20.4 LUFS integrated with a true peak of −0.8 dBTP, so it sits under a voice-over without clipping.

## How it was built (and why there are no seams)

The PNG is flattened, so slicing it into rectangles would leave seams and holes. Instead `project/extract.py` splits it into layers with **shape-accurate masks**:

- **Shapes:** the ring is fitted as a circle; the button, face, tile and bell box are fitted rounded rectangles; the text bar is a fitted hexagon.
- **Fine detail:** the crown and wordmark get keyed mattes, and the bell is cut out with known-background matting.
- **Background:** the energy band layer is computed so that, with every part at rest, the stack rebuilds the original exactly. 99.9% of pixels are within 1.6% of the source; the largest single-pixel differences are on the bell's anti-aliased outline. Areas the original never shows are filled smoothly.
- **Parts the original hides** were rebuilt, so every part is complete when it moves on its own:
  - the bell box's lower-left corner, hidden under the text bar, was rebuilt from its mirror-image corner
  - the button face under the play tile was filled in
  - a bezel "well" was added under the face so the button can press in
- **Button–bell joint:** the button and bell-box masks overlap by 4 px, so no hairline gap can open between them.
- **New elements:** the subscribed-state face and the check tile are derived from the original pixels. The check mark, cursor and all effects are drawn as vectors at render time.

Ronen's photo, crown and wordmark are original pixels and were never repainted.

## Quality control (run on the decoded MOV, not on intermediate frames)

| Check | Result |
|---|---|
| First / last frame fully transparent | Frame 0 and frame 299 have max alpha **0** (frames 292–299 are all empty) |
| Entrance, both clicks, hold and exit inspected | `qc/qc_sheet_dark_bg.png`, `qc/qc_sheet_bright_bg.png` (30 key frames each) |
| Right edge of headline and button–bell joint at 200% | `qc/qc_zoom200_headline_bell.png`: full ת, continuous borders, no gaps, in every state |
| Hebrew headline readable | Yes, in both the original and the subscribed state (white text on purple, then on dark violet) |
| No stationary cursor | Cursor exists only between 0.95 and 3.12 s and is drawn live, not baked into the art |
| Both clicks change the UI | Click 1: face goes purple → dark violet, play → ✓. Click 2: bell compresses and swings, arcs flash and expand |
| Sound sync | Measured click onsets in the MOV's audio: **+0.12 ms** (subscribe, frame 93) and **+0.06 ms** (bell, frame 150). See `qc/qc_audio_sync.png` |
| Bright and dark backgrounds | Both sheets clean; no dark fringes on white, no halos boxed in on black |
| No black rectangles / checkerboard | None. The only opaque near-black areas are the art's own dark bar, bell-box interior and shirt; the area stays stable from frame to frame (`qc/frame_stats.json`) |
| Encoding quality | ProRes `-qscale:v 4`: **56.5 dB PSNR** against the uncompressed render (visually lossless). Alpha is 8-bit, within 1/255 of the source; the file stays under GitHub's 100 MB limit |
| Opens and works in CapCut | **Not tested.** Format verified with FFmpeg (see the CapCut section) |

## Rebuilding

```bash
cd project
./build.sh            # needs: python3 with numpy, opencv-python-headless, scipy, pillow; ffmpeg 6+
```

Change timings in `timeline.py`, placement and scale in `render.py` (`LAYOUTS`), and sound levels in `audio.py`. Everything (video, stills, previews and audio) re-renders in a few minutes on 4 cores.
