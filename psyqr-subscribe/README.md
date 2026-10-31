# PsyQR Moments: Hebrew subscribe animation

The PsyQR Moments banner with the subscribe interaction rebuilt fully in Hebrew. The button reads **תעשו סאב**, and after the click it reads **נרשמתם ✓**. No English subscribe text appears anywhere in the animation.

## Deliverables

| File | Use it for |
|---|---|
| `out/psyqr-subscribe-he-alpha.webm` | Transparent overlay (VP9 with alpha and Opus audio). Works in OBS and browsers. If your editor shows a black background, use the green-screen file or the ProRes `.mov` below. |
| `out/psyqr-subscribe-he-greenscreen.mp4` | Green-screen (#00FF00) version for chroma key in any editor (H.264 and AAC). |
| `out/psyqr-subscribe-he-preview.mp4` | The same animation on a dark backdrop, for watching or sharing. |
| `out/soundtrack.wav` | The synced sound effects on their own (48 kHz stereo). |
| `dist/psyqr-subscribe-he.html` | A self-contained player. Open it in a browser to play with sound, scrub through the timeline and switch the background between dark, green and transparent. |

All videos are 1920×1080, 60 fps and 9.0 s long. The banner is centred at 1.5× its original size.

For a ProRes 4444 `.mov` with alpha, run `node tools/render.cjs --mov`. It is the standard for Premiere and Final Cut, but at about 470 MB it is too large to keep in the repository.

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

To change any timing, edit `TL` at the top of `src/animation.js`, then rebuild and re-render. Sound follows automatically.

## Notes on the source artwork

- **Grey checkerboard removed.** The supplied PNG had a grey checkerboard baked into its outer glow, left over from an earlier background removal. `tools/make_base.py` finds it by flood-filling grey pixels inward from the transparent outside, so grey tones inside the face, hair and lettering are never touched. In that ring it subtracts the grey and keeps only the red glow, faded smoothly. Grey pixels in the outer glow went from 14,988 to 150, and the remaining 150 are real grey edges on the crown and the bell frame.
- **Two layers.** The avatar (ring and crown) and the rest of the banner are split into complementary layers, so the avatar can animate on its own. Added together they reproduce the banner exactly.
- **Stray marks removed:** a clipped glyph at the far left and a bright speck beside the bell frame.
- Heebo is licensed under the SIL Open Font License 1.1, which allows embedding and redistribution.
