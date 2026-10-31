# PsyQR Moments: Hebrew subscribe animation

The PsyQR Moments banner with the subscribe interaction rebuilt fully in Hebrew. The button reads **תעשו סאב**, and after the click it reads **נרשמתם ✓**. No English subscribe text appears anywhere in the animation.

## Deliverables

| File | Use it for |
|---|---|
| `out/psyqr-subscribe-he-capcut.mp4` | **For CapCut** (and any editor that doesn't read transparency): one file, the animation on pure green, 4K, with sound. One Chroma key click removes the green. See *Using it in CapCut*. |
| `out/psyqr-subscribe-he-alpha.webm` | Transparent overlay (VP9 with alpha and Opus audio). Works in OBS and browsers. |
| `out/psyqr-subscribe-he-preview.mp4` | The same animation on a dark backdrop, for watching or sharing. |
| `out/png/psyqr-subscribe-he.png` | Still transparent PNG of the banner with **תעשו סאב** (2282×692, twice the original artwork size). Drop it on top of your video. |
| `out/png/psyqr-subscribed-he.png` | The same still with **נרשמתם ✓**. |
| `out/png/*-1080p-frame.png` | The same two stills on a full 1920×1080 transparent frame, placed exactly where the banner sits in the video. |
| `out/soundtrack.wav` | The synced sound effects on their own (48 kHz stereo). |
| `dist/psyqr-subscribe-he.html` | A self-contained player. Open it in a browser to play with sound, scrub through the timeline and switch the background between dark, green and transparent. |

All videos are 1920×1080, 60 fps and 9.0 s long. The banner is centred at 1.5× its original size.

**Transparent video for editors:** `node tools/render.cjs --mov` writes `out/psyqr-subscribe-he-alpha.mov`, a ProRes 4444 file with an alpha channel (1920×1080, 60 fps, with audio). It is the format Premiere, Final Cut, DaVinci Resolve and After Effects read transparency from. It is about 210 MB, measured visually lossless against the rendered frames (PSNR above 50 dB), so it is not kept in the repository (GitHub rejects files over 100 MB).

All outputs are tagged with the HD colour space (BT.709), so editors show the reds as rendered.

## Using it in CapCut

CapCut ignores the transparency channel of video files (transparent areas show as black), so it gets one green-screen file instead: `out/psyqr-subscribe-he-capcut.mp4`.

1. Drag it onto a track above your video.
2. Select it → Video → Remove BG → **Chroma key** → click the green in the preview with the picker.
3. Raise **Intensity** until the green is gone.

How it is made so that it keys cleanly (`node tools/render.cjs --key`, then `tools/capcut.py`):
- **Chroma-key render mode.** Soft glows can't survive a key: a half-transparent red glow on green becomes olive and stays. So in this mode every effect is drawn solid (light streak, rings, sparks, embers, comets) and shrinks away instead of fading, and the artwork has a hard edge instead of its soft outer halo.
- **Nothing is blended with the green.** Each pixel is either solid, at its full colour, or pure green.
- **Rendered in 4K.** MP4 stores colour at half resolution, so at 1080p a thin red line on green blurs into olive. At 4K the colour detail is full-1080p, and CapCut keys the clip and then scales it down to the project, which also smooths every edge.

In a simulated key over a test video, green-tinged pixels fell from about 1,000 per frame (1080p) to a few hundred faint edge pixels (4K), which raising Intensity removes.

## What happens, second by second

| Time | Visual | Sound |
|---|---|---|
| 0.00–0.21 | A red light streak opens from the avatar, which pops in with a spin and lands with a ring of light. | Rising whoosh, then a thud as it lands |
| 0.22–0.62 | The plate and logo sweep out of the avatar to the right behind a bright edge line. | Sweep whoosh, then a sub-bass impact |
| 0.56–0.71 | The button slides in from the right and settles with a spring. | Swish and a soft thud |
| 0.72–0.83 | The bell spins and drops into its frame. | Light bell ting as it lands |
| 0.62–1.15 | The neon on the button and bell flickers on. | Neon buzz and ignition ticks |
| 0.98 / 1.25 | A glossy shine crosses the logo, then a light sweep crosses **תעשו סאב**. | Soft shimmer |
| 1.40–2.38 | The cursor glides in from bottom right and becomes a hand over the button, which lifts on hover. | Hover tick |
| 2.55 / 2.64 | Press: the button sinks to 93% and springs back. | Mouse down / mouse up |
| 2.64–3.34 | A red energy pulse runs from the click point: shockwave rings and sparks burst from behind the button, the red face wipes to dark neon glass, **תעשו סאב** slides out, **נרשמתם** slides in and the ✓ draws itself. | Energy thump and zap, then a success chime as the ✓ appears |
| 3.40 | The bell gives a small "now me" wiggle and ping. | Soft ping |
| 3.58–4.30 | The cursor moves to the bell, which grows slightly on hover. | Hover tick |
| 4.47 / 4.55 | Bell press, then the bell swings. The clapper lags behind the body, sound waves travel outward and two rings pulse out. The waves stay lit afterwards (notifications on). | Click, then a bell strike on every swing of the clapper |
| 5.15–6.05 | The cursor leaves the frame. | |
| 6.15 / 6.45 | Shine across the logo, then a light sweep across **נרשמתם ✓**. | Soft shimmer |
| 7.50–7.88 | Glow surge; the bell spins out and the button slides away. | Swell and two swishes |
| 7.74–8.10 | The plate and logo sweep back into the avatar. | Sweep-back whoosh |
| 8.02–8.70 | The avatar shrinks and vanishes in a burst of sparks; the light streak shrinks into it and fades. | Low boom and a sparkle tail |

Running underneath the whole time, so the banner never sits still:

- a red aura behind the banner that breathes, and flares on every impact
- two energy comets orbiting the avatar ring
- embers rising behind the banner, plus a few in front on the logo side (none cross the face or the button)
- a slow float of the whole banner, about 2 px
- sparkles on the crown tips at 0.55, 3.95 and 6.85 s

All of the timing comes from one timeline (`TL` in `src/animation.js`), so every sound plays on the frame of the motion it belongs to. In the exported soundtrack the clicks, impacts, chime and bell land within 5 ms of their frames, and the intro landing sounds within 15 ms (under one frame at 60 fps).

## How the Hebrew is handled

- **The English text was removed, not covered.** `tools/make_base.py` paints the original button, including its play icon and lettering, out of the bitmap. It does the same to the bell icon. The button and bell are then redrawn as vectors, so no pixel of "SUBSCRIBE" exists anywhere in the output, even mid-press when the button scales down.
- **Font.** Heebo Black (900) is the Hebrew companion to Roboto, and matches the bold condensed grotesque of the original lettering. It is embedded in the player, so rendering never depends on fonts installed on the machine.
- **Right-to-left.** The text is drawn with `direction = 'rtl'`, so letter order and the word space come from the browser's own bidi shaping. The ✓ is a drawn vector placed on the left of נרשמתם, which is where it falls when "נרשמתם ✓" is read right to left.
- **Sharpness.** The label is rendered as live vector glyphs on every frame, and its face is drawn last with no shadow or blur. Transitions use masks and uniform scale only, so the text is never blurred or stretched. The intro streak and the click sparks pass *behind* the button so they never cross the letters.
- **Centring.** The label is centred on its actual ink bounds, which corrects for glyph side-bearings, and vertically on the Hebrew letter body. Measured on rendered frames, both labels sit within 0.5 px of the centre of the text area.
- **Fit.** The font size is calculated so that both labels fit with margin, using the same size for each. The button keeps its original 316×84 proportions, and the play icon keeps its position.

## Rebuilding

```bash
python3 tools/make_base.py   # assets/banner-original.png -> clean avatar + plate layers (+ preview backdrop)
```

```bash
python3 tools/build.py       # src/ + assets/ -> dist/psyqr-subscribe-he.html
```

```bash
node tools/render.cjs        # dist/ -> out/ (frames, soundtrack, webm, mp4s); needs Playwright + ffmpeg
```

```bash
node tools/render.cjs --png  # dist/ -> out/png/ (the still transparent PNGs)
```

To change any timing, edit `TL` at the top of `src/animation.js`, then rebuild and re-render. Sound follows automatically.

## Notes on the source artwork

- **Grey checkerboard removed.** The supplied PNG had a grey checkerboard baked into its outer glow, left over from an earlier background removal. `tools/make_base.py` finds it by flood-filling grey pixels inward from the transparent outside, so grey tones inside the face, hair and lettering are never touched. In that ring it subtracts the grey and keeps only the red glow, faded smoothly. Grey pixels in the outer glow went from 14,988 to 150, and the remaining 150 are real grey edges on the crown and the bell frame.
- **Two layers.** The avatar (ring and crown) and the rest of the banner are split into complementary layers, so the avatar can animate on its own. Added together they reproduce the banner exactly.
- **Stray marks removed:** a clipped glyph at the far left and a bright speck beside the bell frame.
- Heebo is licensed under the SIL Open Font License 1.1, which allows embedding and redistribution.
