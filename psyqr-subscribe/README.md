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
| 0.00–0.62 | A red light streak opens, and the banner is revealed outward from the centre behind bright edge lines. | Rising whoosh and low riser |
| 0.62–1.15 | Impact flash, then the neon on the button and bell flickers on. | Sub-bass impact, neon buzz and ignition ticks |
| 1.22 | A light sweep crosses **תעשו סאב**. | Soft shimmer |
| 1.40–2.38 | The cursor glides in from bottom right and becomes a hand over the button, which lifts on hover. | Hover tick |
| 2.55 / 2.64 | Press: the button sinks to 93% and springs back. | Mouse down / mouse up |
| 2.64–3.34 | A red energy pulse runs from the click point: shockwave rings and sparks burst from behind the button, the red face wipes to dark neon glass, **תעשו סאב** slides out, **נרשמתם** slides in and the ✓ draws itself. | Energy thump and zap, then a success chime as the ✓ appears |
| 3.40 | The bell gives a small "now me" wiggle and ping. | Soft ping |
| 3.58–4.30 | The cursor moves to the bell, which grows slightly on hover. | Hover tick |
| 4.47 / 4.55 | Bell press, then the bell swings. The clapper lags behind the body, sound waves travel outward and two rings pulse out. The waves stay lit afterwards (notifications on). | Click, then a bell strike on every swing of the clapper |
| 5.15–6.05 | The cursor leaves the frame. | |
| 6.45 | A light sweep crosses **נרשמתם ✓**. | Soft shimmer |
| 7.60–8.65 | Glow surge, then the banner closes into a light streak, which shrinks and fades. | Swell, sweep-away whoosh and a low settle |

All of the timing comes from one timeline (`TL` in `src/animation.js`), so every sound plays on the frame of the motion it belongs to. In the exported soundtrack the measured onsets fall within 5 ms of their frames.

## How the Hebrew is handled

- **The English text was removed, not covered.** `tools/make_base.py` paints the original button, including its play icon and lettering, out of the bitmap. It does the same to the bell icon. The button and bell are then redrawn as vectors, so no pixel of "SUBSCRIBE" exists anywhere in the output, even mid-press when the button scales down.
- **Font.** Heebo Black (900) is the Hebrew companion to Roboto, and matches the bold condensed grotesque of the original lettering. It is embedded in the player, so rendering never depends on fonts installed on the machine.
- **Right-to-left.** The text is drawn with `direction = 'rtl'`, so letter order and the word space come from the browser's own bidi shaping. The ✓ is a drawn vector placed on the left of נרשמתם, which is where it falls when "נרשמתם ✓" is read right to left.
- **Sharpness.** The label is rendered as live vector glyphs on every frame, and its face is drawn last with no shadow or blur. Transitions use masks and uniform scale only, so the text is never blurred or stretched. The intro streak and the click sparks pass *behind* the button so they never cross the letters.
- **Centring.** The label is centred on its actual ink bounds, which corrects for glyph side-bearings, and vertically on the Hebrew letter body. Measured on rendered frames, both labels sit within 0.5 px of the centre of the text area.
- **Fit.** The font size is calculated so that both labels fit with margin, using the same size for each. The button keeps its original 316×84 proportions, and the play icon keeps its position.

## Rebuilding

```bash
python3 tools/make_base.py   # assets/banner-original.png -> assets/banner-base.png (+ preview backdrop)
```

```bash
python3 tools/build.py       # src/ + assets/ -> dist/psyqr-subscribe-he.html
```

```bash
node tools/render.cjs        # dist/ -> out/ (frames, soundtrack, webm, mp4s); needs Playwright + ffmpeg
```

To change any timing, edit `TL` at the top of `src/animation.js`, then rebuild and re-render. Sound follows automatically.

## Notes on the source artwork

- The supplied PNG has a checkerboard pattern baked into its outer glow, left over from an earlier background removal. It is part of the original avatar and logo art, so it shows in the overlay as it did before. Swap in a clean PNG as `assets/banner-original.png` and re-run the three commands above to fix it everywhere.
- A stray clipped glyph sat outside the artwork at the far left. It is removed in the base layer.
- Heebo is licensed under the SIL Open Font License 1.1, which allows embedding and redistribution.
