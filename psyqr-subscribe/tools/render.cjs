#!/usr/bin/env node
/* Render the animation to video, frame-exact, with the synced soundtrack.
 *
 *   node tools/render.cjs                 # full render -> out/
 *   node tools/render.cjs --stills 1.2,3.4 # just a few PNG stills (for review)
 *   node tools/render.cjs --mov           # also write a ProRes 4444 .mov with alpha
 *   node tools/render.cjs --png           # static transparent PNGs of the banner -> out/png/
 *
 * Requires Playwright (Chromium) and ffmpeg on PATH.
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const HTML = path.join(ROOT, 'dist', 'psyqr-subscribe-he.html');
const OUT = path.join(ROOT, 'out');
const args = process.argv.slice(2);
const flag = n => args.includes(n);
const opt = n => { const i = args.indexOf(n); return i >= 0 ? args[i + 1] : undefined; };

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  page.on('pageerror', e => { console.error('page error:', e); process.exitCode = 1; });
  await page.goto('file://' + HTML);
  await page.evaluate(() => window.PSY.ready);
  const meta = await page.evaluate(() => ({ FPS: PSY.FPS, DURATION: PSY.DURATION }));

  const save = async (t, file) => {
    const url = await page.evaluate(t => window.PSY.frame(t), t);
    fs.writeFileSync(file, Buffer.from(url.split(',')[1], 'base64'));
  };

  if (flag('--png')) {
    // static transparent PNGs of the settled banner, for use as a still overlay
    const dir = path.join(OUT, 'png');
    fs.mkdirSync(dir, { recursive: true });
    const jobs = [
      ['psyqr-subscribe-he.png', false, 'banner'],
      ['psyqr-subscribed-he.png', true, 'banner'],
      ['psyqr-subscribe-he-1080p-frame.png', false, 'frame'],
      ['psyqr-subscribed-he-1080p-frame.png', true, 'frame'],
    ];
    for (const [name, done, mode] of jobs) {
      const url = await page.evaluate(([d, m]) => window.PSY.still(d, m, 2), [done, mode]);
      fs.writeFileSync(path.join(dir, name), Buffer.from(url.split(',')[1], 'base64'));
    }
    await browser.close();
    console.log('png ->', dir);
    return;
  }

  if (opt('--stills')) {
    const dir = opt('--dir') || path.join(OUT, 'stills');
    fs.mkdirSync(dir, { recursive: true });
    for (const s of opt('--stills').split(',')) await save(+s, path.join(dir, `t${(+s).toFixed(3)}.png`));
    await browser.close();
    console.log('stills ->', dir);
    return;
  }

  const frames = path.join(OUT, 'frames');
  fs.rmSync(frames, { recursive: true, force: true });
  fs.mkdirSync(frames, { recursive: true });
  const n = Math.round(meta.FPS * meta.DURATION);
  const t0 = Date.now();
  for (let i = 0; i < n; i++) {
    await save(i / meta.FPS, path.join(frames, String(i).padStart(4, '0') + '.png'));
    if (i % 60 === 0) process.stdout.write(`frame ${i}/${n}\r`);
  }
  console.log(`rendered ${n} frames in ${((Date.now() - t0) / 1000).toFixed(1)}s`);

  const wav = path.join(OUT, 'soundtrack.wav');
  fs.writeFileSync(wav, Buffer.from(await page.evaluate(() => window.PSY.audio(48000)), 'base64'));
  await browser.close();

  const ff = a => execFileSync('ffmpeg', ['-y', '-hide_banner', '-loglevel', 'error', ...a], { stdio: 'inherit' });
  const seq = ['-framerate', String(meta.FPS), '-i', path.join(frames, '%04d.png')];
  const bgDark = path.join(ROOT, 'assets', 'preview-bg.png');

  // 1. transparent overlay (VP9 + alpha, Opus)
  ff([...seq, '-i', wav, '-c:v', 'libvpx-vp9', '-pix_fmt', 'yuva420p', '-b:v', '0', '-crf', '24', '-row-mt', '1',
    '-auto-alt-ref', '0', '-c:a', 'libopus', '-b:a', '192k', '-shortest', path.join(OUT, 'psyqr-subscribe-he-alpha.webm')]);
  // 2. green screen (H.264, AAC)
  ff(['-f', 'lavfi', '-i', `color=c=0x00FF00:s=1920x1080:r=${meta.FPS}`, ...seq, '-i', wav,
    '-filter_complex', '[0][1]overlay=shortest=1:format=auto,format=yuv420p', '-map', '2:a',
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '16', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', '-shortest',
    path.join(OUT, 'psyqr-subscribe-he-greenscreen.mp4')]);
  // 3. preview on a dark backdrop
  ff(['-loop', '1', '-framerate', String(meta.FPS), '-i', bgDark, ...seq, '-i', wav,
    '-filter_complex', '[0][1]overlay=shortest=1:format=auto,format=yuv420p', '-map', '2:a',
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '17', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', '-shortest',
    path.join(OUT, 'psyqr-subscribe-he-preview.mp4')]);
  // 4. optional ProRes 4444 with alpha (large; editors' standard for transparency)
  if (flag('--mov')) {
    ff([...seq, '-i', wav, '-c:v', 'prores_ks', '-profile:v', '4444', '-pix_fmt', 'yuva444p10le', '-alpha_bits', '16',
      '-vendor', 'apl0', '-c:a', 'pcm_s16le', '-shortest', path.join(OUT, 'psyqr-subscribe-he-alpha.mov')]);
  }
  console.log('videos ->', OUT);
})();
