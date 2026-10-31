/* PsyQR Moments – Hebrew subscribe animation.
 *
 * Everything is a pure function of time t (seconds), so the live player and the
 * frame-by-frame video export draw identical frames, and the soundtrack is
 * scheduled from the same timeline (TL) as the visuals.
 *
 * Coordinate spaces
 *   stage  : 1920 x 1080 video frame
 *   banner : 1045 x 250 artwork pixels (the supplied PNG), scaled by BS on stage
 */
'use strict';

// ───────────────────────────── constants ─────────────────────────────
const W = 1920, H = 1080, FPS = 60, DURATION = 9.0;
const BW = 1045, BH = 250, BS = 1.5;
const BX = (W - BW * BS) / 2, BY = (H - BH * BS) / 2;

const BTN = { x: 587, y: 115, w: 316, h: 84, r: 14 };
BTN.cx = BTN.x + BTN.w / 2; BTN.cy = BTN.y + BTN.h / 2;
const ICON = { cx: 641, cy: 157, w: 62, h: 44, r: 12 };
const TEXT_AREA = { x0: 684, x1: 889 };
TEXT_AREA.cx = (TEXT_AREA.x0 + TEXT_AREA.x1) / 2;
const BELL = { cx: 961, cy: 154, r: 50.5, pivotY: 120.5 };
const AV = { cx: 179.3, cy: 133.3, rx: 101.5, ry: 91.5 };   // avatar ring (elliptical in the source art)
const CROWN_TIPS = [[108, 24], [82, 44]];

const FAMILY = 'PsyHeebo';
const STR_SUB = 'תעשו סאב';      // before the click
const STR_DONE = 'נרשמתם';        // after the click, followed (visually, on the left) by a ✓
const LABEL_DONE = 'נרשמתם ✓';

// Master timeline (seconds). Visuals and sound both read from this.
const TL = {
  // staggered entrance: avatar pops in, plate + logo sweep out from it, button slides in, bell drops in
  avatarIn: [0.06, 0.50], wipeIn: [0.22, 0.62], impact: 0.62,
  buttonIn: [0.56, 0.92], bellIn: [0.72, 1.08],
  flicker: [[0.62, 1], [0.665, 0.18], [0.705, 1], [0.765, 0.32], [0.795, 0.08], [0.86, 1], [0.93, 0.5], [0.965, 1]],
  flickerEnd: 1.15,
  shine1: 0.98, sweep1: 1.25,
  move1: [1.40, 2.38],
  btnDown: 2.55, btnUp: 2.64,
  bellPing: 3.40,
  move2: [3.58, 4.30],
  bellDown: 4.47, bellUp: 4.55,
  move3: [5.15, 6.05],
  shine2: 6.15, sweep2: 6.45,
  crown: [0.55, 3.95, 6.85],
  // staggered exit, in reverse
  outro: 7.50, bellOut: [7.50, 7.74], buttonOut: [7.58, 7.88], wipeOut: [7.74, 8.10],
  avatarOut: [8.02, 8.32], streakOut: [8.22, 8.70],
};
// Moment each spring entrance first reaches its resting place: eOutBack(x, s) hits 1 at x = 1 / (s + 1).
const LAND = {
  avatar: TL.avatarIn[0] + (TL.avatarIn[1] - TL.avatarIn[0]) / (2.0 + 1),
  button: TL.buttonIn[0] + (TL.buttonIn[1] - TL.buttonIn[0]) / (1.4 + 1),
  bell: TL.bellIn[0] + (TL.bellIn[1] - TL.bellIn[0]) / (2.2 + 1),
};
const BELL_FREQ = 3.1, BELL_DAMP = 2.4, BELL_AMP = 24 * Math.PI / 180;
const BELL_STRIKES = [0, 1, 2, 3, 4, 5].map(k => {
  const tau = (2 * k + 1) / (4 * BELL_FREQ);
  return { t: TL.bellUp + tau, amp: Math.exp(-BELL_DAMP * tau), dir: k % 2 ? -1 : 1 };
});

// ───────────────────────────── math ─────────────────────────────
const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const lerp = (a, b, t) => a + (b - a) * t;
const prog = (t, a, b) => clamp((t - a) / (b - a));
const sstep = x => { x = clamp(x); return x * x * (3 - 2 * x); };
const eOutCubic = x => 1 - Math.pow(1 - clamp(x), 3);
const eInCubic = x => Math.pow(clamp(x), 3);
const eInOutCubic = x => { x = clamp(x); return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; };
const eOutExpo = x => x >= 1 ? 1 : 1 - Math.pow(2, -10 * clamp(x));
const pulse = x => (x <= 0 || x >= 1) ? 0 : Math.sin(Math.PI * x);
const eOutBack = (x, s = 1.70158) => { x = clamp(x); return 1 + (s + 1) * Math.pow(x - 1, 3) + s * Math.pow(x - 1, 2); };
const mixHex = (a, b, t) => {
  const pa = [1, 3, 5].map(i => parseInt(a.substr(i, 2), 16));
  const pb = [1, 3, 5].map(i => parseInt(b.substr(i, 2), 16));
  return 'rgb(' + pa.map((v, i) => Math.round(lerp(v, pb[i], t))).join(',') + ')';
};

function cubicBezierEase(x1, y1, x2, y2) {
  const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
  const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
  const sx = s => ((ax * s + bx) * s + cx) * s;
  const sy = s => ((ay * s + by) * s + cy) * s;
  const dsx = s => (3 * ax * s + 2 * bx) * s + cx;
  return x => {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    let s = x;
    for (let i = 0; i < 10; i++) {
      const e = sx(s) - x, d = dsx(s);
      if (Math.abs(e) < 1e-7 || Math.abs(d) < 1e-7) break;
      s -= e / d;
    }
    return sy(clamp(s));
  };
}
function bez(p0, p1, p2, p3, s) {
  const u = 1 - s;
  const a = u * u * u, b = 3 * u * u * s, c = 3 * u * s * s, d = s * s * s;
  return [a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]];
}
function rng(seed) { // mulberry32
  return () => {
    seed |= 0; seed = seed + 0x6D2B79F5 | 0;
    let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}
const toStage = (u, v) => [BX + u * BS, BY + v * BS];
const toBanner = (x, y) => [(x - BX) / BS, (y - BY) / BS];

// ───────────────────────────── cursor ─────────────────────────────
const CLICK1 = toStage(812, 161);       // on the Hebrew label
const BELLPT = toStage(958, 151);       // on the bell
const PATHS = [
  { t0: TL.move1[0], t1: TL.move1[1], p: [[1760, 1140], [1700, 880], [1455, 705], CLICK1], ease: cubicBezierEase(0.32, 0, 0.12, 1.06) },
  { t0: TL.move2[0], t1: TL.move2[1], p: [CLICK1, [1450, 522], [1566, 516], BELLPT], ease: cubicBezierEase(0.42, 0, 0.14, 1.05) },
  { t0: TL.move3[0], t1: TL.move3[1], p: [BELLPT, [1690, 650], [1820, 900], [1990, 1140]], ease: cubicBezierEase(0.5, 0, 0.85, 0.55) },
];
function cursorPos(t) {
  if (t < PATHS[0].t0) return PATHS[0].p[0];
  for (let i = 0; i < PATHS.length; i++) {
    const P = PATHS[i];
    if (t < P.t0) return PATHS[i - 1].p[3];
    if (t <= P.t1) return bez(P.p[0], P.p[1], P.p[2], P.p[3], P.ease(prog(t, P.t0, P.t1)));
  }
  return PATHS[PATHS.length - 1].p[3];
}
function inButton(u, v) {
  const { x, y, w, h, r } = BTN;
  if (u < x || u > x + w || v < y || v > y + h) return false;
  const cx = clamp(u, x + r, x + w - r), cy = clamp(v, y + r, y + h - r);
  return (u - cx) ** 2 + (v - cy) ** 2 <= r * r;
}
const inBell = (u, v) => (u - BELL.cx) ** 2 + (v - BELL.cy) ** 2 <= (BELL.r + 3) ** 2;
function hoverTarget(t) {
  const [u, v] = toBanner(...cursorPos(t));
  return inButton(u, v) ? 'btn' : inBell(u, v) ? 'bell' : null;
}
// Enter/leave times for hover, found by sampling the deterministic cursor path.
const HOVER = { btn: [], bell: [] };
(function computeHover() {
  let prev = null, start = 0;
  const dt = 1 / 1000;
  for (let t = 0; t <= DURATION + dt; t += dt) {
    const cur = hoverTarget(t);
    if (cur !== prev) {
      if (prev) HOVER[prev].push([start, t]);
      if (cur) start = t;
      prev = cur;
    }
  }
  if (prev) HOVER[prev].push([start, DURATION + 1]);
})();
function hoverAmt(kind, t) {
  let v = 0;
  for (const [a, b] of HOVER[kind]) v = Math.max(v, sstep((t - a) / 0.12) * (1 - sstep((t - b) / 0.16)));
  return v;
}

// ───────────────────────────── state ─────────────────────────────
function flickerValue(t) {
  if (t < TL.flicker[0][0]) return 0.12;
  if (t >= TL.flickerEnd) return 1;
  let v = 1;
  for (const [kt, kv] of TL.flicker) if (t >= kt) v = kv;
  return v;
}
function neonGlow(t) {
  let g = 0.15 + 0.85 * flickerValue(t);
  g *= 1 + 0.06 * Math.sin(2 * Math.PI * 0.8 * t);              // idle breathing
  g += 0.9 * Math.exp(-5 * Math.max(0, t - TL.btnUp)) * (t >= TL.btnUp ? 1 : 0); // click surge
  g += 0.55 * pulse(prog(t, TL.outro, TL.outro + 0.3));            // outro surge
  return g;
}
// How far the plate + logo layer is revealed (0..1), sweeping out from the avatar.
function wipeAmount(t) {
  if (t < TL.outro) return eInOutCubic(prog(t, TL.wipeIn[0], TL.wipeIn[1]));
  return 1 - eInOutCubic(prog(t, TL.wipeOut[0], TL.wipeOut[1]));
}
function wipeEdges(e) { return [AV.cx - (AV.cx + 60) * e, AV.cx + (BW + 60 - AV.cx) * e]; }
function avatarState(t) {
  const p = prog(t, TL.avatarIn[0], TL.avatarIn[1]);
  let scale = lerp(0.25, 1, eOutBack(p, 2.0)), rot = -0.55 * (1 - eOutCubic(p)), alpha = sstep(p * 3);
  const q = prog(t, TL.avatarOut[0], TL.avatarOut[1]);
  if (q > 0) {
    scale *= q < 0.3 ? 1 + 0.07 * sstep(q / 0.3) : 1.07 * (1 - eInCubic((q - 0.3) / 0.7));
    rot += 0.7 * eInCubic(q);
    alpha *= 1 - sstep((q - 0.55) / 0.45);
  }
  return { scale, rot, alpha };
}
// slow hover-float of the whole banner once it has landed
function floatY(t) {
  const env = sstep(prog(t, 1.0, 1.6)) * (1 - sstep(prog(t, TL.outro - 0.2, TL.outro)));
  return 1.6 * Math.sin(2 * Math.PI * (t - 1.0) / 3.4) * env;
}

function buttonState(t) {
  const hv = hoverAmt('btn', t);
  const base = 1 + 0.035 * hv;
  let scale = base, press = 0;
  if (t >= TL.btnDown && t < TL.btnUp) {
    press = eOutCubic(prog(t, TL.btnDown, TL.btnDown + 0.06));
    scale = lerp(base, 0.93, press);
  } else if (t >= TL.btnUp) {
    const tau = t - TL.btnUp;
    const baseAtUp = 1 + 0.035 * hoverAmt('btn', TL.btnUp);
    scale = base + (0.93 - baseAtUp) * Math.exp(-9 * tau) * Math.cos(2 * Math.PI * 3.0 * tau);
    press = Math.max(0, 1 - tau / 0.08);
  }
  const u = t - TL.btnUp;
  const m = t >= TL.btnUp ? eOutCubic(prog(u, 0, 0.42)) : 0;
  const tOut = prog(u, 0.02, 0.22), tIn = prog(u, 0.18, 0.46);
  return {
    hover: hv, scale, press, morph: m,
    wipeFront: t >= TL.btnUp ? 1 - prog(u, 0.25, 0.45) : 0,
    oldA: t >= TL.btnUp ? 1 - eInCubic(tOut) : 1, oldDy: t >= TL.btnUp ? 18 * eInCubic(tOut) : 0,
    newA: eOutCubic(tIn), newDy: -18 * (1 - eOutCubic(tIn)),
    check: eOutCubic(prog(u, 0.40, 0.70)),
    checkPop: 1 + 0.18 * pulse(prog(u, 0.55, 0.85)),
    icon: sstep(prog(u, 0.12, 0.32)),
    iconPop: 1 + 0.14 * pulse(prog(u, 0.12, 0.42)),
    glow: neonGlow(t) * (1 + 0.25 * hv),
    flicker: flickerValue(t),
    dx: 80 * (1 - eOutBack(prog(t, TL.buttonIn[0], TL.buttonIn[1]), 1.4)) + 110 * eInCubic(prog(t, TL.buttonOut[0], TL.buttonOut[1])),
    alpha: sstep(prog(t, TL.buttonIn[0], TL.buttonIn[1]) * 2.2) * (1 - sstep(prog(t, TL.buttonOut[0], TL.buttonOut[1]) * 1.3)),
    sweep: Math.max(prog(t, TL.sweep1, TL.sweep1 + 0.55) * (t < TL.sweep1 + 0.55 ? 1 : 0),
      prog(t, TL.sweep2, TL.sweep2 + 0.6) * (t < TL.sweep2 + 0.6 ? 1 : 0)),
  };
}

function bellState(t) {
  const hv = hoverAmt('bell', t);
  let scale = 1 + 0.045 * hv;
  if (t >= TL.bellDown && t < TL.bellUp) scale = lerp(scale, 0.9, eOutCubic(prog(t, TL.bellDown, TL.bellDown + 0.06)));
  else if (t >= TL.bellUp) {
    const tau = t - TL.bellUp;
    scale += (0.9 - (1 + 0.045 * hoverAmt('bell', TL.bellUp))) * Math.exp(-10 * tau) * Math.cos(2 * Math.PI * 3.2 * tau);
  }
  let angle = 0, clap = 0;
  // little attention wiggle after subscribing
  const wig = t - TL.bellPing;
  if (wig > 0 && wig < 0.6) angle += 9 * Math.PI / 180 * Math.exp(-6 * wig) * Math.sin(2 * Math.PI * 5 * wig);
  const tau = t - TL.bellUp;
  if (tau > 0 && tau < 2.4) {
    const env = BELL_AMP * Math.exp(-BELL_DAMP * tau);
    angle += env * Math.sin(2 * Math.PI * BELL_FREQ * tau);
    clap = 1.35 * env * Math.sin(2 * Math.PI * BELL_FREQ * tau - 0.75);
  }
  const pin = prog(t, TL.bellIn[0], TL.bellIn[1]), pout = prog(t, TL.bellOut[0], TL.bellOut[1]);
  const enter = eOutBack(pin, 2.2) * (1 - eInCubic(pout));
  const spin = -2.4 * (1 - eOutCubic(pin)) + 2.6 * eInCubic(pout);
  return {
    hover: hv, scale: scale * enter, spin, alpha: sstep(pin * 2.5) * (1 - sstep(pout * 1.2)),
    angle, clap: angle + (clap - angle) * 0.6,
    active: sstep(prog(t, TL.bellUp, TL.bellUp + 0.25)),
    ping: prog(t, TL.bellPing, TL.bellPing + 0.7),
    flash: t >= TL.bellUp ? Math.exp(-6 * (t - TL.bellUp)) : 0,
    glow: neonGlow(t) * (1 + 0.3 * hv),
  };
}

// ───────────────────────────── drawing helpers ─────────────────────────────
let K = 1; // device pixels per banner unit (shadowBlur/offset ignore transforms)

function rrect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
function glow(ctx, color, blur) { ctx.shadowColor = color; ctx.shadowBlur = blur * K; ctx.shadowOffsetX = 0; ctx.shadowOffsetY = 0; }
function noGlow(ctx) { ctx.shadowColor = 'rgba(0,0,0,0)'; ctx.shadowBlur = 0; ctx.shadowOffsetX = 0; ctx.shadowOffsetY = 0; }

// ───────────────────────────── text metrics ─────────────────────────────
const TXT = {};
function setupText(ctx) {
  ctx.save();
  ctx.font = `900 100px ${FAMILY}`;
  ctx.direction = 'rtl';
  const wSub = ctx.measureText(STR_SUB).width / 100;
  const wDone = ctx.measureText(STR_DONE).width / 100;
  const avail = TEXT_AREA.x1 - TEXT_AREA.x0;
  const CHECK = 0.62, GAP = 0.2;                         // relative to font size
  const size = Math.min(50, (avail * 0.92) / wSub, (avail * 0.92) / (wDone + CHECK + GAP));
  ctx.font = `900 ${size}px ${FAMILY}`;
  // Optical centre: use letters that sit on the baseline with no descender,
  // so a small tail (e.g. on ע) does not pull the label upwards.
  const ref = ctx.measureText('במתסה');
  const asc = ref.actualBoundingBoxAscent, desc = Math.max(0, ref.actualBoundingBoxDescent);
  // Horizontal centring on the actual ink, not the advance box (side bearings
  // would otherwise push the label a few pixels off-centre).
  ctx.textAlign = 'center';
  const ink = str => { const m = ctx.measureText(str); return { L: m.actualBoundingBoxLeft, R: m.actualBoundingBoxRight }; };
  const iSub = ink(STR_SUB), iDone = ink(STR_DONE);
  const checkW = CHECK * size, half = 0.065 * size;      // half of the check's white stroke
  const cA = 0.06 * checkW - half, cB = 0.96 * checkW + half; // check ink, relative to its box
  const groupW = (cB - cA) + GAP * size + (iDone.L + iDone.R);
  const groupLeft = TEXT_AREA.cx - groupW / 2;
  Object.assign(TXT, {
    size, font: `900 ${size}px ${FAMILY}`,
    checkW,
    subX: TEXT_AREA.cx + (iSub.L - iSub.R) / 2,
    checkX: groupLeft - cA,
    doneX: groupLeft + (cB - cA) + GAP * size + iDone.L,
    baseline: BTN.cy + (asc - desc) / 2, letterH: asc + desc,
  });
  ctx.restore();
}

// ───────────────────────────── button ─────────────────────────────
function drawYouTubeIcon(ctx, s) {
  const { cx, cy, w, h, r } = ICON;
  const draw = (dark, alpha, scale) => {
    if (alpha <= 0.001) return;
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.translate(cx, cy); ctx.scale(scale, scale); ctx.translate(-cx, -cy);
    // depth shadow
    ctx.save();
    ctx.shadowColor = dark ? 'rgba(255,30,30,0.9)' : 'rgba(80,0,0,0.65)';
    ctx.shadowBlur = (dark ? 12 : 4) * K; ctx.shadowOffsetY = (dark ? 0 : 2) * K;
    rrect(ctx, cx - w / 2, cy - h / 2, w, h, r);
    const g = ctx.createLinearGradient(0, cy - h / 2, 0, cy + h / 2);
    if (dark) { g.addColorStop(0, '#ff4a3f'); g.addColorStop(1, '#d4000a'); }
    else { g.addColorStop(0, '#ffffff'); g.addColorStop(1, '#f1e4e4'); }
    ctx.fillStyle = g; ctx.fill();
    ctx.restore();
    // triangle
    const tw = 17, th = 20, ox = 2;
    ctx.beginPath();
    ctx.moveTo(cx - tw / 2 + ox, cy - th / 2);
    ctx.lineTo(cx + tw / 2 + ox, cy);
    ctx.lineTo(cx - tw / 2 + ox, cy + th / 2);
    ctx.closePath();
    ctx.lineJoin = 'round'; ctx.lineWidth = 2.2;
    ctx.fillStyle = ctx.strokeStyle = dark ? '#ffffff' : '#e3000c';
    ctx.fill(); ctx.stroke();
    ctx.restore();
  };
  draw(false, 1 - s.icon, s.iconPop);
  draw(true, s.icon, s.iconPop);
}

function drawCheck(ctx, x, yMid, size, p, pop) {
  if (p <= 0) return;
  const pts = [[0.06, 0.52], [0.38, 0.84], [0.96, 0.14]].map(([a, b]) => [x + a * size, yMid + (b - 0.5) * size]);
  const l1 = Math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]);
  const l2 = Math.hypot(pts[2][0] - pts[1][0], pts[2][1] - pts[1][1]);
  const L = l1 + l2;
  ctx.save();
  const cx = x + size / 2;
  ctx.translate(cx, yMid); ctx.scale(pop, pop); ctx.translate(-cx, -yMid);
  ctx.beginPath(); ctx.moveTo(...pts[0]); ctx.lineTo(...pts[1]); ctx.lineTo(...pts[2]);
  ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  ctx.setLineDash([L * p, L]);
  ctx.lineWidth = 0.175 * TXT.size;
  glow(ctx, 'rgba(255,40,40,0.95)', 10);
  ctx.strokeStyle = '#ff3b3b'; ctx.stroke();
  noGlow(ctx);
  ctx.lineWidth = 0.13 * TXT.size;
  ctx.strokeStyle = '#ffffff'; ctx.stroke();
  ctx.restore();
}

function drawLabel(ctx, which, alpha, dy, darkness, s) {
  if (alpha <= 0.002) return;
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.font = TXT.font; ctx.direction = 'rtl'; ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
  const y = TXT.baseline + dy;
  const str = which === 'sub' ? STR_SUB : STR_DONE;
  const tx = which === 'sub' ? TXT.subX : TXT.doneX;
  // depth shadow under the letters (as in the original lettering)
  ctx.save();
  ctx.shadowColor = 'rgba(60,0,0,0.8)'; ctx.shadowBlur = 3 * K; ctx.shadowOffsetY = 2.2 * K;
  ctx.fillStyle = '#5a0000';
  ctx.fillText(str, tx, y);
  ctx.restore();
  if (darkness > 0) { // neon halo on the dark (subscribed) button
    ctx.save();
    ctx.globalAlpha = alpha * darkness;
    glow(ctx, 'rgba(255,35,35,0.9)', 14);
    ctx.fillStyle = '#ff6b6b';
    ctx.fillText(str, tx, y);
    ctx.restore();
  }
  // crisp face – no shadow, no blur, drawn last so the glyphs stay razor sharp
  const g = ctx.createLinearGradient(0, y - TXT.letterH, 0, y);
  g.addColorStop(0, '#ffffff'); g.addColorStop(0.55, '#ffffff'); g.addColorStop(1, '#f6e2e2');
  ctx.fillStyle = g;
  ctx.fillText(str, tx, y);
  if (which === 'done') drawCheck(ctx, TXT.checkX, TXT.baseline + dy - TXT.letterH / 2, TXT.checkW, s.check, s.checkPop);
  ctx.restore();
}

function drawButton(ctx, t, s, click) {
  const { x, y, w, h, r, cx, cy } = BTN;
  const path = () => rrect(ctx, x, y, w, h, r);
  if (s.alpha <= 0.002) return;
  ctx.save();
  ctx.globalAlpha = s.alpha;
  ctx.translate(cx + s.dx, cy); ctx.scale(s.scale, s.scale); ctx.translate(-cx, -cy);

  // 1. outer neon glow
  ctx.save();
  const ga = clamp(0.55 * s.glow, 0, 1);
  glow(ctx, `rgba(255,22,22,${ga})`, 14 + 10 * s.glow);
  path(); ctx.fillStyle = '#c00008'; ctx.fill();
  glow(ctx, `rgba(255,40,30,${clamp(0.35 * s.glow, 0, 1)})`, 34 + 14 * s.glow);
  ctx.fill();
  ctx.restore();

  // 2. red body
  path();
  let g = ctx.createLinearGradient(0, y, 0, y + h);
  g.addColorStop(0, '#ff4337'); g.addColorStop(0.42, '#f40d12'); g.addColorStop(1, '#b80007');
  ctx.fillStyle = g; ctx.fill();

  // 3. dark (subscribed) body revealed by an energy wipe from the click point
  const maxR = Math.max(...[[x, y], [x + w, y], [x, y + h], [x + w, y + h]].map(([px, py]) => Math.hypot(px - click[0], py - click[1]))) + 12;
  const wipeR = maxR * s.morph;
  if (wipeR > 0.5) {
    ctx.save();
    path(); ctx.clip();
    ctx.beginPath(); ctx.arc(click[0], click[1], wipeR, 0, Math.PI * 2); ctx.clip();
    path();
    g = ctx.createLinearGradient(0, y, 0, y + h);
    g.addColorStop(0, '#3c0a0a'); g.addColorStop(0.5, '#1d0404'); g.addColorStop(1, '#0d0101');
    ctx.fillStyle = g; ctx.fill();
    // inner rim light of the dark glass
    rrect(ctx, x + 1.5, y + 1.5, w - 3, h - 3, r - 1.5);
    ctx.lineWidth = 7; ctx.strokeStyle = 'rgba(255,40,40,0.22)'; ctx.stroke();
    ctx.restore();
  }

  // 4. gloss + bevel
  ctx.save();
  path(); ctx.clip();
  g = ctx.createLinearGradient(0, y, 0, y + h * 0.55);
  g.addColorStop(0, `rgba(255,255,255,${lerp(0.30, 0.12, s.morph)})`); g.addColorStop(1, 'rgba(255,255,255,0)');
  rrect(ctx, x + 3, y + 3, w - 6, h * 0.5, r - 3); ctx.fillStyle = g; ctx.fill();
  g = ctx.createLinearGradient(0, y + h * 0.6, 0, y + h);
  g.addColorStop(0, 'rgba(60,0,0,0)'); g.addColorStop(1, 'rgba(60,0,0,0.35)');
  ctx.fillStyle = g; ctx.fillRect(x, y, w, h);
  // power-on dimming while the neon flickers
  if (s.flicker < 1) { ctx.fillStyle = `rgba(20,0,0,${0.45 * (1 - s.flicker)})`; ctx.fillRect(x, y, w, h); }
  // pressed inner shadow
  if (s.press > 0) {
    ctx.lineWidth = 14; ctx.strokeStyle = `rgba(40,0,0,${0.45 * s.press})`;
    path(); ctx.stroke();
  }
  // light sweep
  if (s.sweep > 0 && s.sweep < 1) {
    const sx = lerp(x - 90, x + w + 90, eInOutCubic(s.sweep));
    ctx.translate(sx, cy); ctx.transform(1, 0, -0.45, 1, 0, 0);
    g = ctx.createLinearGradient(-38, 0, 38, 0);
    g.addColorStop(0, 'rgba(255,255,255,0)'); g.addColorStop(0.5, 'rgba(255,240,240,0.38)'); g.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = g; ctx.fillRect(-38, -h, 76, h * 2);
  }
  ctx.restore();

  // 5. neon edge line
  ctx.save();
  path();
  ctx.lineWidth = 2.6;
  glow(ctx, `rgba(255,30,30,${clamp(0.9 * s.glow, 0, 1)})`, 7);
  ctx.strokeStyle = mixHex('#ffe4da', '#ff3a34', s.morph);
  ctx.stroke();
  ctx.restore();

  // 6. energy front of the wipe
  if (wipeR > 0.5 && s.wipeFront > 0) {
    ctx.save();
    path(); ctx.clip();
    ctx.globalCompositeOperation = 'lighter';
    ctx.beginPath(); ctx.arc(click[0], click[1], wipeR, 0, Math.PI * 2);
    glow(ctx, 'rgba(255,50,40,1)', 10);
    ctx.lineWidth = 5; ctx.strokeStyle = `rgba(255,70,50,${0.9 * s.wipeFront})`; ctx.stroke();
    ctx.lineWidth = 1.6; ctx.strokeStyle = `rgba(255,235,225,${s.wipeFront})`; ctx.stroke();
    ctx.restore();
  }

  // 7. YouTube play icon
  drawYouTubeIcon(ctx, s);

  // 8. Hebrew label (clipped to the button so the slide stays inside it)
  ctx.save();
  rrect(ctx, x + 3, y + 3, w - 6, h - 6, r - 3); ctx.clip();
  drawLabel(ctx, 'sub', s.oldA, s.oldDy, 0, s);
  drawLabel(ctx, 'done', s.newA, s.newDy, s.morph, s);
  ctx.restore();

  ctx.restore();
}

// ───────────────────────────── bell ─────────────────────────────
function bellBodyPath(ctx) {
  ctx.beginPath();
  ctx.moveTo(0, 5.5);
  ctx.bezierCurveTo(9.5, 5.5, 15.5, 12.5, 15.5, 23);
  ctx.lineTo(16, 37);
  ctx.bezierCurveTo(16.4, 43.5, 20.5, 46.5, 25.5, 48.5);
  ctx.bezierCurveTo(28.2, 49.6, 28.2, 53, 25.5, 53);
  ctx.lineTo(-25.5, 53);
  ctx.bezierCurveTo(-28.2, 53, -28.2, 49.6, -25.5, 48.5);
  ctx.bezierCurveTo(-20.5, 46.5, -16.4, 43.5, -16, 37);
  ctx.lineTo(-15.5, 23);
  ctx.bezierCurveTo(-15.5, 12.5, -9.5, 5.5, 0, 5.5);
  ctx.closePath();
}
function drawWaveArcs(ctx, cx, cy, r, alpha, lw) {
  if (alpha <= 0.002) return;
  ctx.lineWidth = lw; ctx.lineCap = 'round';
  ctx.strokeStyle = `rgba(255,236,232,${alpha})`;
  const a0 = 151 * Math.PI / 180, a1 = 207 * Math.PI / 180;
  ctx.beginPath(); ctx.arc(cx, cy, r, a0, a1); ctx.stroke();
  ctx.beginPath(); ctx.arc(cx, cy, r, Math.PI - a1, Math.PI - a0); ctx.stroke();
}
function drawBell(ctx, t, s) {
  const { cx, cy, r } = BELL;
  if (s.scale > 0.01 && s.alpha > 0.002) drawBellBody(ctx, t, s);
  drawBellRings(ctx, t, s);
}
function drawBellBody(ctx, t, s) {
  const { cx, cy, r } = BELL;
  ctx.save();
  ctx.globalAlpha = s.alpha;
  ctx.translate(cx, cy); ctx.scale(s.scale, s.scale); ctx.rotate(s.spin); ctx.translate(-cx, -cy);

  // disc face
  ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2);
  let g = ctx.createRadialGradient(cx, cy - 6, 2, cx, cy, r);
  g.addColorStop(0, mixHex('#2a0707', '#4a0909', s.active * 0.8 + s.hover * 0.3));
  g.addColorStop(1, '#0c0303');
  ctx.fillStyle = g; ctx.fill();
  ctx.lineWidth = 1.3; ctx.strokeStyle = `rgba(255,40,40,${0.35 + 0.3 * s.hover})`; ctx.stroke();
  if (s.flash > 0.01) {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    g = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
    g.addColorStop(0, `rgba(255,60,40,${0.75 * s.flash})`); g.addColorStop(1, 'rgba(255,0,0,0)');
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
  }

  // resting sound waves (dim until notifications are switched on)
  const wcx = cx, wcy = cy - 12;
  ctx.save();
  glow(ctx, `rgba(255,30,30,${clamp(0.95 * s.glow, 0, 1)})`, 8);
  const restA = lerp(0.28, 1, s.active);
  drawWaveArcs(ctx, wcx, wcy, 29, restA, 3.4);
  drawWaveArcs(ctx, wcx, wcy, 39, restA, 3.4);
  ctx.restore();

  // travelling waves on every clapper strike
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  glow(ctx, 'rgba(255,40,30,1)', 9);
  for (const st of BELL_STRIKES) {
    const tau = t - st.t;
    if (tau < 0 || tau > 0.7) continue;
    const p = tau / 0.7;
    drawWaveArcs(ctx, wcx, wcy, 33 + 36 * eOutCubic(p), st.amp * (1 - p) * 0.95, 3.2 * (1 - 0.5 * p));
  }
  ctx.restore();

  // the bell itself – swings around its hanging point
  const pivotY = BELL.pivotY;
  ctx.save();
  ctx.translate(cx, pivotY);
  // clapper (secondary motion, lags behind the body)
  ctx.save();
  ctx.rotate(s.clap);
  glow(ctx, `rgba(255,30,30,${clamp(0.9 * s.glow, 0, 1)})`, 7);
  ctx.beginPath(); ctx.moveTo(-7.5, 57); ctx.lineTo(7.5, 57); ctx.ellipse(0, 57, 7.5, 7.2, 0, 0, Math.PI, false); ctx.closePath();
  ctx.fillStyle = '#fbeeee'; ctx.fill();
  ctx.restore();
  ctx.rotate(s.angle);
  // hanger ring
  ctx.save();
  glow(ctx, `rgba(255,30,30,${clamp(0.9 * s.glow, 0, 1)})`, 7);
  ctx.beginPath(); ctx.arc(0, 0, 4.2, 0, Math.PI * 2);
  ctx.lineWidth = 3; ctx.strokeStyle = '#fff4f2'; ctx.stroke();
  // body
  bellBodyPath(ctx);
  g = ctx.createLinearGradient(0, 5, 0, 53);
  g.addColorStop(0, '#ffffff'); g.addColorStop(0.7, '#f7eded'); g.addColorStop(1, '#ecd8d8');
  ctx.fillStyle = g; ctx.fill();
  ctx.restore();
  // soft inner shading for volume
  ctx.save();
  bellBodyPath(ctx); ctx.clip();
  g = ctx.createLinearGradient(-26, 0, 26, 0);
  g.addColorStop(0, 'rgba(120,20,20,0.18)'); g.addColorStop(0.35, 'rgba(255,255,255,0)'); g.addColorStop(1, 'rgba(120,20,20,0.22)');
  ctx.fillStyle = g; ctx.fillRect(-30, 0, 60, 60);
  ctx.restore();
  ctx.restore();
  ctx.restore();
}
function drawBellRings(ctx, t, s) {
  const { cx, cy } = BELL;
  // attention ping and activation ring (outside the disc, not scaled)
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  const ring = (p, r0, r1, lw, a) => {
    if (p <= 0 || p >= 1) return;
    ctx.beginPath(); ctx.arc(cx, cy, lerp(r0, r1, eOutCubic(p)), 0, Math.PI * 2);
    glow(ctx, 'rgba(255,30,30,1)', 12);
    ctx.lineWidth = lw * (1 - 0.6 * p); ctx.strokeStyle = `rgba(255,70,60,${a * (1 - p)})`; ctx.stroke();
  };
  ring(s.ping, 58, 84, 3, 0.6);
  ring(prog(t, TL.bellUp, TL.bellUp + 0.75), 58, 104, 5, 1);
  ring(prog(t, TL.bellUp + 0.12, TL.bellUp + 0.95), 58, 92, 2.5, 0.7);
  // twinkles
  const R = rng(77);
  for (let i = 0; i < 9; i++) {
    const a = R() * Math.PI * 2, rad = 62 + R() * 26, t0 = TL.bellUp + 0.05 + R() * 0.45, life = 0.35 + R() * 0.3;
    const p = prog(t, t0, t0 + life);
    if (p <= 0 || p >= 1) continue;
    const sz = (3 + R() * 3.5) * pulse(p);
    const px = cx + Math.cos(a) * rad, py = cy + Math.sin(a) * rad * 0.9;
    ctx.save(); ctx.translate(px, py); ctx.rotate(p * 1.2);
    glow(ctx, 'rgba(255,40,40,1)', 6);
    ctx.fillStyle = '#ffeae6';
    ctx.beginPath();
    ctx.moveTo(0, -sz); ctx.quadraticCurveTo(0, 0, sz, 0); ctx.quadraticCurveTo(0, 0, 0, sz);
    ctx.quadraticCurveTo(0, 0, -sz, 0); ctx.quadraticCurveTo(0, 0, 0, -sz); ctx.fill();
    ctx.restore();
  }
  ctx.restore();
}

// ───────────────────────────── click energy pulse ─────────────────────────────
function drawClickBurst(ctx, t, click) {
  const u = t - TL.btnUp;
  if (u < 0 || u > 0.9) return;
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  // red bloom over the banner
  const bloom = Math.pow(1 - prog(u, 0, 0.45), 2);
  if (bloom > 0) {
    const g = ctx.createRadialGradient(click[0], click[1], 0, click[0], click[1], 300);
    g.addColorStop(0, `rgba(255,50,30,${0.55 * bloom})`); g.addColorStop(0.4, `rgba(255,0,0,${0.22 * bloom})`); g.addColorStop(1, 'rgba(255,0,0,0)');
    ctx.fillStyle = g; ctx.fillRect(click[0] - 300, click[1] - 300, 600, 600);
  }
  // shockwave rings
  const ring = (p, r1, lw, a) => {
    if (p <= 0 || p >= 1) return;
    ctx.beginPath(); ctx.arc(click[0], click[1], 8 + r1 * eOutCubic(p), 0, Math.PI * 2);
    glow(ctx, 'rgba(255,30,20,1)', 14);
    ctx.lineWidth = lw * (1 - 0.75 * p); ctx.strokeStyle = `rgba(255,80,60,${a * (1 - p)})`; ctx.stroke();
    noGlow(ctx);
    ctx.lineWidth = lw * 0.3 * (1 - 0.75 * p); ctx.strokeStyle = `rgba(255,240,230,${a * (1 - p)})`; ctx.stroke();
  };
  ring(prog(u, 0, 0.62), 330, 8, 1);
  ring(prog(u, 0.09, 0.7), 230, 4, 0.75);
  sparks(ctx, u, click[0], click[1], 1337, 26, 260, 680);
  ctx.restore();
}

// ───────────────────────────── cinematic intro / outro + ambient motion ─────────────────────────────
function impactFlash(t) {
  return (t >= TL.impact ? 0.5 * Math.pow(1 - prog(t, TL.impact, TL.impact + 0.4), 2) : 0)
    + 0.35 * pulse(prog(t, TL.outro, TL.outro + 0.3));
}
const spike = (t, at, k = 4) => t >= at ? Math.exp(-k * (t - at)) : 0;
function auraAmount(t) {
  return 0.42 + 0.1 * Math.sin(2 * Math.PI * t / 1.7)
    + 0.5 * spike(t, TL.impact) + 0.45 * spike(t, TL.btnUp) + 0.35 * spike(t, TL.bellUp)
    + 0.4 * pulse(prog(t, TL.outro, TL.outro + 0.3));
}
function drawWipeEdge(ctx, t, x, k = 1) {
  // bright leading edge of the plate + logo sweep
  const a = k * (t < TL.outro ? pulse(prog(t, TL.wipeIn[0] - 0.03, TL.wipeIn[1] + 0.06))
    : pulse(prog(t, TL.wipeOut[0] - 0.03, TL.wipeOut[1] + 0.04)));
  if (a <= 0.01) return;
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  const g = ctx.createLinearGradient(0, -10, 0, BH + 10);
  g.addColorStop(0, 'rgba(255,40,30,0)'); g.addColorStop(0.5, `rgba(255,90,70,${a})`); g.addColorStop(1, 'rgba(255,40,30,0)');
  glow(ctx, 'rgba(255,30,20,1)', 18);
  ctx.fillStyle = g; ctx.fillRect(x - 2.5, -10, 5, BH + 20);
  ctx.fillStyle = `rgba(255,245,240,${0.8 * a})`; ctx.fillRect(x - 0.8, 40, 1.6, BH - 80);
  ctx.restore();
}
const STREAK_FULL = BW * BS + 300;
function drawStreak(ctx, t) {
  // anamorphic light streak in stage space; it is born from the avatar and returns to it
  let width = 0, a = 0;
  if (t < TL.outro) {
    width = eOutExpo(prog(t, 0, 0.35)) * STREAK_FULL;
    a = prog(t, 0, 0.12) * (1 - sstep(prog(t, TL.impact, TL.impact + 0.45)));
    a += 0.6 * pulse(prog(t, TL.impact - 0.05, TL.impact + 0.25));
  } else {
    width = STREAK_FULL * (1 - eInCubic(prog(t, TL.streakOut[0], TL.streakOut[1])));
    a = sstep(prog(t, TL.wipeOut[0], TL.wipeOut[0] + 0.2)) * (1 - prog(t, TL.streakOut[1] - 0.15, TL.streakOut[1]));
  }
  if (a <= 0.01 || width <= 1) return;
  const [cx, cy] = toStage(AV.cx, AV.cy);
  const x0 = cx - width * 0.26, x1 = cx + width * 0.74, at = 0.26;
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  let g = ctx.createLinearGradient(x0, 0, x1, 0);
  g.addColorStop(0, 'rgba(255,20,20,0)'); g.addColorStop(at, `rgba(255,60,40,${0.55 * a})`); g.addColorStop(1, 'rgba(255,20,20,0)');
  ctx.fillStyle = g; ctx.fillRect(x0, cy - 22, x1 - x0, 44);
  g = ctx.createLinearGradient(x0, 0, x1, 0);
  g.addColorStop(0, 'rgba(255,200,190,0)'); g.addColorStop(at, `rgba(255,245,240,${a})`); g.addColorStop(1, 'rgba(255,200,190,0)');
  ctx.shadowColor = 'rgba(255,30,20,1)'; ctx.shadowBlur = 16 * K / BS;
  ctx.fillStyle = g; ctx.fillRect(x0, cy - 2, x1 - x0, 4);
  const fr = 110 * a;
  g = ctx.createRadialGradient(cx, cy, 0, cx, cy, fr + 1);
  g.addColorStop(0, `rgba(255,235,225,${0.7 * a})`); g.addColorStop(0.3, `rgba(255,60,40,${0.35 * a})`); g.addColorStop(1, 'rgba(255,0,0,0)');
  ctx.fillStyle = g; ctx.fillRect(cx - fr, cy - fr, fr * 2, fr * 2);
  ctx.restore();
}

function star(ctx, x, y, sz, rot, alpha) {
  if (sz <= 0.05 || alpha <= 0.01) return;
  ctx.save(); ctx.translate(x, y); ctx.rotate(rot);
  glow(ctx, 'rgba(255,40,40,1)', 6);
  ctx.fillStyle = `rgba(255,236,232,${alpha})`;
  ctx.beginPath();
  ctx.moveTo(0, -sz); ctx.quadraticCurveTo(0, 0, sz, 0); ctx.quadraticCurveTo(0, 0, 0, sz);
  ctx.quadraticCurveTo(0, 0, -sz, 0); ctx.quadraticCurveTo(0, 0, 0, -sz); ctx.fill();
  ctx.restore();
}
function sparks(ctx, u, cx, cy, seed, n, s0, s1) {
  const R = rng(seed);
  ctx.lineCap = 'round';
  for (let i = 0; i < n; i++) {
    const a = R() * Math.PI * 2, sp = s0 + R() * (s1 - s0), life = 0.32 + R() * 0.3, delay = R() * 0.05;
    const p = prog(u, delay, delay + life);
    if (p <= 0 || p >= 1) continue;
    const dist = sp * (1 - Math.exp(-4 * (u - delay))) / 4;
    const vx = Math.cos(a), vy = Math.sin(a) * 0.75;
    const px = cx + vx * dist, py = cy + vy * dist, tail = 10 + 26 * (1 - p);
    ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(px - vx * tail, py - vy * tail);
    glow(ctx, 'rgba(255,30,20,1)', 6);
    ctx.lineWidth = 2.6 * (1 - p) + 0.4;
    ctx.strokeStyle = `rgba(255,${Math.round(lerp(230, 60, p))},${Math.round(lerp(210, 40, p))},${1 - p})`;
    ctx.stroke();
  }
}

// rising embers – behind the banner (most) and a few in front, away from the button
const EMBERS = (() => { const R = rng(4242); return Array.from({ length: 72 }, (_, i) => ({ P: 2.0 + R() * 1.8, ph: R() * 4, front: i % 5 === 0 })); })();
function drawEmbers(ctx, t, front) {
  const fade = 1 - sstep(prog(t, TL.wipeOut[0], TL.avatarOut[1]));
  if (fade <= 0) return;
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  EMBERS.forEach((e, i) => {
    if (e.front !== front) return;
    const k = Math.floor((t - e.ph) / e.P), ts = e.ph + k * e.P, tau = t - ts;
    if (ts < 0.75 || ts > 7.4) return;
    const R = rng(i * 7919 + k * 104729 + 17);
    const x0 = front ? (R() < 0.6 ? 300 + R() * 250 : 1005 + R() * 50) : -20 + R() * (BW + 40);   // front ones keep off the face and the button
    const y0 = 120 + R() * 140, vy = 26 + R() * 46, amp = 5 + R() * 12, f = 0.5 + R() * 0.9, ph2 = R() * 6.28;
    const size = 1.1 + R() * 2.2, life = e.P * (0.75 + R() * 0.2);
    if (tau > life) return;
    const x = x0 + amp * Math.sin(2 * Math.PI * f * tau + ph2) + 7 * tau;
    const y = y0 - vy * tau - 5 * tau * tau;
    const a = sstep(tau / 0.3) * (1 - sstep((tau - life + 0.7) / 0.7)) * (0.65 + 0.35 * Math.sin(23 * tau + ph2)) * fade;
    if (a <= 0.01) return;
    const age = tau / life, rr = size * (1 - 0.4 * age), gr = rr * 4.5;
    const g = ctx.createRadialGradient(x, y, 0, x, y, gr);
    g.addColorStop(0, `rgba(255,${Math.round(lerp(200, 70, age))},${Math.round(lerp(140, 40, age))},${a})`);
    g.addColorStop(rr / gr, `rgba(255,60,30,${0.55 * a})`);
    g.addColorStop(1, 'rgba(255,20,10,0)');
    ctx.fillStyle = g; ctx.fillRect(x - gr, y - gr, gr * 2, gr * 2);
  });
  ctx.restore();
}

// two energy comets orbiting the avatar ring
function drawComets(ctx, t) {
  const env = sstep(prog(t, 0.42, 0.85)) * (1 - sstep(prog(t, TL.avatarOut[0], TL.avatarOut[0] + 0.12)));
  if (env <= 0.01) return;
  const head = -Math.PI / 2 + 2 * Math.PI * (t - 0.4) / 2.4;
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.lineCap = 'butt';
  for (const [off, inten] of [[0, 1], [Math.PI, 0.55]]) {
    const h = head + off, N = 30, tail = 1.45, rx = AV.rx + 6, ry = AV.ry + 6;
    // tail: a wide faint halo pass and a hot core pass (gradient-free glow, no per-stroke blur)
    for (const [wMul, aMul] of [[4.2, 0.16], [2.2, 0.3], [1, 1]]) {
      for (let i = 0; i < N; i++) {
        const f = 1 - i / N;
        ctx.beginPath(); ctx.ellipse(AV.cx, AV.cy, rx, ry, 0, h - tail * (i + 1) / N, h - tail * i / N);
        ctx.lineWidth = (1.5 + 5 * f) * wMul;
        const hot = aMul === 1 ? f * f * f : 0;
        ctx.strokeStyle = `rgba(255,${Math.round(lerp(30, 200, hot))},${Math.round(lerp(20, 170, hot))},${env * inten * f * aMul})`;
        ctx.stroke();
      }
    }
    const hx = AV.cx + rx * Math.cos(h), hy = AV.cy + ry * Math.sin(h);
    const bl = ctx.createRadialGradient(hx, hy, 0, hx, hy, 24);
    bl.addColorStop(0, `rgba(255,120,90,${0.85 * env * inten})`); bl.addColorStop(0.35, `rgba(255,40,20,${0.4 * env * inten})`); bl.addColorStop(1, 'rgba(255,0,0,0)');
    ctx.fillStyle = bl; ctx.fillRect(hx - 24, hy - 24, 48, 48);
    ctx.fillStyle = `rgba(255,250,245,${env * inten})`;
    ctx.beginPath(); ctx.arc(hx, hy, 4.2, 0, Math.PI * 2); ctx.fill();
  }
  ctx.restore();
}
function drawCrownSparkles(ctx, t) {
  for (const t0 of TL.crown) {
    CROWN_TIPS.forEach(([x, y], j) => {
      const p = prog(t, t0 + j * 0.13, t0 + j * 0.13 + 0.5);
      if (p > 0 && p < 1) star(ctx, x, y, (j ? 6 : 9) * pulse(p), p * 1.6, 1);
    });
  }
}
function drawAvatarLanding(ctx, t) {
  const p = prog(t, LAND.avatar - 0.03, LAND.avatar + 0.5);
  if (p <= 0 || p >= 1) return;
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  const k = 1 + 0.35 * eOutCubic(p);
  ctx.beginPath(); ctx.ellipse(AV.cx, AV.cy, AV.rx * k, AV.ry * k, 0, 0, Math.PI * 2);
  glow(ctx, 'rgba(255,30,20,1)', 14);
  ctx.lineWidth = 6 * (1 - 0.7 * p); ctx.strokeStyle = `rgba(255,90,70,${1 - p})`; ctx.stroke();
  ctx.restore();
}
function drawLogoShine(ctx, t) {
  for (const t0 of [TL.shine1, TL.shine2]) {
    const p = prog(t, t0, t0 + 0.45);
    if (p <= 0 || p >= 1) continue;
    const x = lerp(250, 640, eInOutCubic(p));
    for (const [hw, a] of [[30, 0.22], [18, 0.22], [8, 0.22]]) {   // stacked bands = soft edges
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(x - hw + 40, 15); ctx.lineTo(x + hw + 40, 15); ctx.lineTo(x + hw - 40, 245); ctx.lineTo(x - hw - 40, 245);
      ctx.closePath(); ctx.clip();
      ctx.globalCompositeOperation = 'lighter'; ctx.globalAlpha = a * pulse(p) * 1.6;
      ctx.drawImage(IMG_REST, 0, 0, BW, BH);
      ctx.globalCompositeOperation = 'source-atop'; ctx.globalAlpha = a * pulse(p) * 0.9;
      ctx.fillStyle = '#ffe6e0'; ctx.fillRect(x - 120, 0, 240, BH);
      ctx.restore();
    }
  }
}
function drawFinalBurst(ctx, t) {
  const at = TL.avatarOut[0] + 0.2, u = t - at;
  if (u < 0 || u > 0.9) return;
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  const p = prog(u, 0, 0.6);
  if (p < 1) {
    ctx.beginPath(); ctx.ellipse(AV.cx, AV.cy, 20 + 160 * eOutCubic(p), 18 + 145 * eOutCubic(p), 0, 0, Math.PI * 2);
    glow(ctx, 'rgba(255,30,20,1)', 14);
    ctx.lineWidth = 7 * (1 - 0.75 * p); ctx.strokeStyle = `rgba(255,80,60,${1 - p})`; ctx.stroke();
  }
  sparks(ctx, u, AV.cx, AV.cy, 909, 30, 300, 640);
  ctx.restore();
}

// ───────────────────────────── cursor drawing ─────────────────────────────
function drawArrow(ctx) {
  ctx.beginPath();
  ctx.moveTo(0, 0); ctx.lineTo(0, 17); ctx.lineTo(4, 13.2); ctx.lineTo(6.9, 19.6);
  ctx.lineTo(9.6, 18.4); ctx.lineTo(6.8, 12.2); ctx.lineTo(12, 12.2); ctx.closePath();
  ctx.lineJoin = 'round';
  ctx.save();
  ctx.shadowColor = 'rgba(0,0,0,0.45)'; ctx.shadowBlur = 4 * CUR_K; ctx.shadowOffsetX = 1.2 * CUR_K; ctx.shadowOffsetY = 2 * CUR_K;
  ctx.fillStyle = '#ffffff'; ctx.fill();
  ctx.restore();
  ctx.lineWidth = 1.15; ctx.strokeStyle = '#000000'; ctx.stroke();
}
function handShapes(ctx) {
  rrect(ctx, 4.6, 0, 3.6, 12.5, 1.8); ctx.fill(); ctx.stroke();
  rrect(ctx, 8.2, 5.8, 3.2, 7, 1.6); ctx.fill(); ctx.stroke();
  rrect(ctx, 11.4, 6.8, 3.0, 6.5, 1.5); ctx.fill(); ctx.stroke();
  rrect(ctx, 14.4, 8.2, 2.7, 6, 1.35); ctx.fill(); ctx.stroke();
  rrect(ctx, 4.6, 9.8, 12.5, 9.8, 3.4); ctx.fill(); ctx.stroke();
  rrect(ctx, 6.4, 16.5, 9.4, 5.3, 1.6); ctx.fill(); ctx.stroke();
  ctx.save(); ctx.translate(4.9, 14.2); ctx.rotate(-0.85);
  rrect(ctx, -1.6, -1.6, 7.4, 3.3, 1.6); ctx.fill(); ctx.stroke();
  ctx.restore();
}
function drawHand(ctx) {
  ctx.save();
  ctx.translate(-6.4, 0);
  ctx.lineJoin = 'round';
  // shadow + outline pass (outline = thick black stroke of every part)
  ctx.save();
  ctx.shadowColor = 'rgba(0,0,0,0.45)'; ctx.shadowBlur = 4 * CUR_K; ctx.shadowOffsetX = 1.2 * CUR_K; ctx.shadowOffsetY = 2 * CUR_K;
  ctx.fillStyle = '#000'; ctx.strokeStyle = '#000'; ctx.lineWidth = 2.3;
  handShapes(ctx);
  ctx.restore();
  // white fill pass
  ctx.fillStyle = '#fff'; ctx.strokeStyle = '#fff'; ctx.lineWidth = 0.01;
  handShapes(ctx);
  // finger separations
  ctx.strokeStyle = '#000'; ctx.lineWidth = 0.75; ctx.lineCap = 'round';
  ctx.beginPath();
  ctx.moveTo(8.2, 10.2); ctx.lineTo(8.2, 12.6);
  ctx.moveTo(11.4, 10.6); ctx.lineTo(11.4, 12.9);
  ctx.moveTo(14.4, 11.2); ctx.lineTo(14.4, 13.4);
  ctx.stroke();
  ctx.restore();
}
let CUR_K = 1;
const CURSOR_SCALE = 2.1;
function drawCursor(ctx, t, devScale) {
  const [x, y] = cursorPos(t);
  if (x < -60 || y < -60 || x > W + 60 || y > H + 60) return;
  const tgt = hoverTarget(t);
  let press = 0;
  for (const [d, u] of [[TL.btnDown, TL.btnUp], [TL.bellDown, TL.bellUp]]) {
    if (t >= d && t < u + 0.1) press = t < u ? eOutCubic(prog(t, d, d + 0.05)) : 1 - eOutCubic(prog(t, u, u + 0.1));
  }
  // click ripple under the pointer
  ctx.save();
  for (const [d, u] of [[TL.btnDown, TL.btnUp], [TL.bellDown, TL.bellUp]]) {
    const p = prog(t, u - 0.02, u + 0.4);
    if (p <= 0 || p >= 1) continue;
    ctx.beginPath(); ctx.arc(x, y, 6 + 26 * eOutCubic(p), 0, Math.PI * 2);
    ctx.lineWidth = 3 * (1 - p); ctx.strokeStyle = `rgba(255,255,255,${0.7 * (1 - p)})`; ctx.stroke();
  }
  ctx.restore();
  ctx.save();
  ctx.translate(x, y);
  const sc = CURSOR_SCALE * (1 - 0.13 * press);
  ctx.scale(sc, sc);
  CUR_K = devScale * sc;
  if (tgt) drawHand(ctx); else drawArrow(ctx);
  ctx.restore();
}

// ───────────────────────────── frame ─────────────────────────────
const CLICK_B = toBanner(...CLICK1);
let IMG_AV = null, IMG_REST = null;
const OFF = { c: null, x: null }, AURA = { c: null, x: null };
function offscreen(w, h) {
  if (!OFF.c || OFF.c.width !== w || OFF.c.height !== h) {
    OFF.c = document.createElement('canvas'); OFF.c.width = w; OFF.c.height = h;
    OFF.x = OFF.c.getContext('2d');
  }
  OFF.x.setTransform(1, 0, 0, 1, 0, 0);
  OFF.x.clearRect(0, 0, w, h);
  return OFF.x;
}
function bannerTransform(ctx, devScale, sx, sy, t) {
  ctx.setTransform(devScale, 0, 0, devScale, 0, 0);
  ctx.translate(sx + BX, sy + BY + floatY(t) * BS);
  ctx.scale(BS, BS);
  ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = 'high';
  K = devScale * BS;
}

function drawBanner(o, t, devScale, sx, sy) {
  bannerTransform(o, devScale, sx, sy, t);
  const flash = impactFlash(t);
  // plate + logo layer, swept in from the avatar
  const e = wipeAmount(t);
  if (e > 0) {
    const [l, r] = wipeEdges(e);
    const ra = sstep(e / 0.3);
    o.save();
    o.beginPath(); o.rect(l, -60, r - l, BH + 120); o.clip();
    o.globalAlpha = ra;
    o.drawImage(IMG_REST, 0, 0, BW, BH);
    if (flash > 0.01) { o.globalCompositeOperation = 'lighter'; o.globalAlpha = ra * flash; o.drawImage(IMG_REST, 0, 0, BW, BH); }
    o.globalAlpha = 1; o.globalCompositeOperation = 'source-over';
    drawLogoShine(o, t);
    o.restore();
  }
  // avatar layer: added on top it complements the plate layer exactly
  const av = avatarState(t);
  if (av.alpha > 0.002 && av.scale > 0.01) {
    o.save();
    o.translate(AV.cx, AV.cy); o.rotate(av.rot); o.scale(av.scale, av.scale); o.translate(-AV.cx, -AV.cy);
    o.globalCompositeOperation = 'lighter';
    o.globalAlpha = av.alpha;
    o.drawImage(IMG_AV, 0, 0, BW, BH);
    if (flash > 0.01) { o.globalAlpha = av.alpha * flash; o.drawImage(IMG_AV, 0, 0, BW, BH); }
    o.globalAlpha = 1; o.globalCompositeOperation = 'source-over';
    drawComets(o, t);
    drawCrownSparkles(o, t);
    o.restore();
  }
  drawAvatarLanding(o, t);
  drawBell(o, t, bellState(t));
  drawClickBurst(o, t, CLICK_B);   // under the button: energy bursts out from behind it, label stays clean
  drawButton(o, t, buttonState(t), CLICK_B);
  if (e > 0 && e < 1) {
    const x = wipeEdges(e)[1];
    drawWipeEdge(o, t, x, sstep((x - (AV.cx + AV.rx)) / 40));   // edge light only once it clears the avatar
  }
}

function drawFrame(ctx, t, devScale) {
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
  if (t < 0 || t > DURATION) return;

  // camera shake on the impacts (whole frame, so cursor and banner move together)
  let sx = 0, sy = 0;
  for (const [tt, amp] of [[TL.btnUp, 3.2], [TL.bellUp, 1.6], [TL.impact, 2.4], [TL.avatarOut[0] + 0.2, 2.0]]) {
    const u = t - tt;
    if (u > 0 && u < 0.4) {
      const e = amp * Math.exp(-14 * u);
      sx += e * Math.sin(u * 97); sy += e * Math.cos(u * 83);
    }
  }
  const stage = () => ctx.setTransform(devScale, 0, 0, devScale, sx * devScale, sy * devScale);

  // 1. light streak, behind everything
  ctx.save(); stage(); K = devScale * BS; drawStreak(ctx, t); ctx.restore();

  // 2. the banner is composed in its own layer…
  const o = offscreen(ctx.canvas.width, ctx.canvas.height);
  drawBanner(o, t, devScale, sx, sy);

  // 3. …which casts a breathing red aura (shadow only, the layer itself is drawn later)
  // (computed at quarter resolution – it is a soft blur, so this is invisible and 16x cheaper)
  const aura = auraAmount(t);
  if (aura > 0.01) {
    const cw = ctx.canvas.width, ch = ctx.canvas.height, q = 4;
    if (!AURA.c || AURA.c.width !== Math.ceil(cw / q)) {
      AURA.c = document.createElement('canvas'); AURA.c.width = Math.ceil(cw / q); AURA.c.height = Math.ceil(ch / q);
      AURA.x = AURA.c.getContext('2d');
    }
    const a = AURA.x;
    a.setTransform(1, 0, 0, 1, 0, 0); a.clearRect(0, 0, AURA.c.width, AURA.c.height);
    const BIG = 20000;
    a.shadowColor = `rgba(255,24,18,${clamp(aura)})`;
    a.shadowBlur = 40 * devScale / q; a.shadowOffsetX = BIG;
    a.drawImage(o.canvas, -BIG, 0, cw / q, ch / q);
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(AURA.c, 0, 0, cw, ch);
    ctx.restore();
  }

  // 4. embers behind, the banner, embers in front, final spark burst
  ctx.save(); bannerTransform(ctx, devScale, sx, sy, t); drawEmbers(ctx, t, false); ctx.restore();
  ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.drawImage(o.canvas, 0, 0); ctx.restore();
  ctx.save(); bannerTransform(ctx, devScale, sx, sy, t); drawEmbers(ctx, t, true); drawFinalBurst(ctx, t); ctx.restore();

  // 5. cursor
  ctx.save(); stage(); drawCursor(ctx, t, devScale); ctx.restore();
}

// ───────────────────────────── sound ─────────────────────────────
// All sounds are synthesised with Web Audio, scheduled from TL so they land on
// the exact frame of the visual they belong to. Works with AudioContext (live)
// and OfflineAudioContext (export).
function makeNoise(ac, seconds, seed) {
  const R = rng(seed);
  const buf = ac.createBuffer(2, Math.floor(ac.sampleRate * seconds), ac.sampleRate);
  for (let c = 0; c < 2; c++) { const d = buf.getChannelData(c); for (let i = 0; i < d.length; i++) d[i] = R() * 2 - 1; }
  return buf;
}
function makeIR(ac, seconds, decay) {
  const R = rng(99);
  const len = Math.floor(ac.sampleRate * seconds);
  const buf = ac.createBuffer(2, len, ac.sampleRate);
  for (let c = 0; c < 2; c++) {
    const d = buf.getChannelData(c);
    for (let i = 0; i < len; i++) d[i] = (R() * 2 - 1) * Math.pow(1 - i / len, decay);
  }
  return buf;
}
function buildSoundtrack(ac, when0, offset) {
  const out = ac.createGain(); out.gain.value = 0.85;
  const comp = ac.createDynamicsCompressor();
  comp.threshold.value = -14; comp.knee.value = 8; comp.ratio.value = 3.5; comp.attack.value = 0.002; comp.release.value = 0.18;
  out.connect(comp); comp.connect(ac.destination);
  const verb = ac.createConvolver(); verb.buffer = makeIR(ac, 2.4, 3.2);
  const verbGain = ac.createGain(); verbGain.gain.value = 0.3;
  verb.connect(verbGain); verbGain.connect(out);
  const NOISE = makeNoise(ac, 2.5, 4242);

  const T = x => when0 + (x - offset);
  const ok = x => x >= offset - 0.001;

  function chain(nodes, pan, send) {
    for (let i = 0; i < nodes.length - 1; i++) nodes[i].connect(nodes[i + 1]);
    let last = nodes[nodes.length - 1];
    if (pan !== undefined && ac.createStereoPanner) {
      const p = ac.createStereoPanner();
      if (typeof pan === 'number') p.pan.value = pan; else pan(p.pan);
      last.connect(p); last = p;
    }
    last.connect(out);
    if (send) { const s = ac.createGain(); s.gain.value = send; last.connect(s); s.connect(verb); }
  }
  function env(param, t, attack, peak, decay, floor = 0.0001) {
    param.setValueAtTime(floor, t);
    param.exponentialRampToValueAtTime(peak, t + attack);
    param.exponentialRampToValueAtTime(floor, t + attack + decay);
  }
  function noise(t, dur) {
    const s = ac.createBufferSource(); s.buffer = NOISE;
    s.start(t, (t * 7.31) % 1.0, dur + 0.05); return s;
  }
  function osc(type, t, dur, f0, f1, curve = 'exp') {
    const o = ac.createOscillator(); o.type = type;
    o.frequency.setValueAtTime(f0, t);
    if (f1 !== undefined) {
      if (curve === 'exp') o.frequency.exponentialRampToValueAtTime(f1, t + dur);
      else o.frequency.linearRampToValueAtTime(f1, t + dur);
    }
    o.start(t); o.stop(t + dur + 0.05); return o;
  }

  // ---- library ----
  function whoosh(x, dur, f0, f1, peak, peakAt, panFrom, panTo, send = 0.35) {
    if (!ok(x)) return;
    const t = T(x);
    const n = noise(t, dur);
    const bp = ac.createBiquadFilter(); bp.type = 'bandpass'; bp.Q.value = 0.9;
    bp.frequency.setValueAtTime(f0, t); bp.frequency.exponentialRampToValueAtTime(f1, t + dur);
    const g = ac.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(peak, t + dur * peakAt);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    chain([n, bp, g], p => { p.setValueAtTime(panFrom, t); p.linearRampToValueAtTime(panTo, t + dur); }, send);
  }
  function boom(x, peak, f0 = 100, f1 = 34, len = 1.2) {
    if (!ok(x)) return;
    const t = T(x);
    const o = osc('sine', t, len, f0, f1);
    const g = ac.createGain(); env(g.gain, t, 0.006, peak, len);
    chain([o, g], 0, 0.15);
    const n = noise(t, 0.45);
    const lp = ac.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.value = 900;
    const g2 = ac.createGain(); env(g2.gain, t, 0.003, peak * 0.55, 0.35);
    chain([n, lp, g2], 0, 0.4);
    const n2 = noise(t, 0.08);
    const hp = ac.createBiquadFilter(); hp.type = 'highpass'; hp.frequency.value = 3000;
    const g3 = ac.createGain(); env(g3.gain, t, 0.002, peak * 0.25, 0.07);
    chain([n2, hp, g3], 0, 0.3);
  }
  function neonBuzz() {
    const x0 = TL.flicker[0][0];
    if (!ok(x0)) return;
    const t0 = T(x0), dur = TL.flickerEnd - x0 + 0.05;
    const o1 = osc('sawtooth', t0, dur, 100), o2 = osc('square', t0, dur, 150);
    const bp = ac.createBiquadFilter(); bp.type = 'bandpass'; bp.frequency.value = 1400; bp.Q.value = 0.8;
    const g = ac.createGain(); g.gain.setValueAtTime(0, t0);
    o1.connect(bp); o2.connect(bp);
    for (const [kt, kv] of TL.flicker) g.gain.setValueAtTime(0.085 * kv, T(kt));
    g.gain.setValueAtTime(0.085, T(TL.flickerEnd - 0.08));
    g.gain.linearRampToValueAtTime(0, T(TL.flickerEnd));
    chain([bp, g], -0.1, 0.1);
    // ignition ticks on every switch-on
    let prev = 0;
    for (const [kt, kv] of TL.flicker) {
      if (kv > prev + 0.4) tick(kt, 0.16, 5200);
      prev = kv;
    }
  }
  function tick(x, peak, f = 2200, len = 0.03, pan = 0) {
    if (!ok(x)) return;
    const t = T(x);
    const o = osc('sine', t, len, f, f * 0.7);
    const g = ac.createGain(); env(g.gain, t, 0.002, peak, len);
    chain([o, g], pan, 0.1);
  }
  function shimmer(x, peak) {
    if (!ok(x)) return;
    [3136, 3951, 4699, 5274, 6272].forEach((f, i) => {
      const t = T(x + i * 0.055);
      const o = osc('sine', t, 0.6, f, f * 1.01);
      const g = ac.createGain(); env(g.gain, t, 0.04, peak, 0.5);
      chain([o, g], -0.6 + i * 0.3, 0.7);
    });
  }
  function mouseClick(x, up) {
    if (!ok(x)) return;
    const t = T(x);
    const n = noise(t, 0.02);
    const hp = ac.createBiquadFilter(); hp.type = 'highpass'; hp.frequency.value = up ? 2500 : 1500;
    const g = ac.createGain(); env(g.gain, t, 0.0008, up ? 0.12 : 0.19, up ? 0.012 : 0.018);
    chain([n, hp, g], 0.15, 0.05);
    const o = osc('sine', t, 0.03, up ? 2900 : 3500, up ? 2400 : 2600);
    const g2 = ac.createGain(); env(g2.gain, t, 0.001, up ? 0.035 : 0.055, 0.025);
    chain([o, g2], 0.15);
    if (!up) {
      const b = osc('sine', t, 0.05, 190, 120);
      const g3 = ac.createGain(); env(g3.gain, t, 0.002, 0.16, 0.045);
      chain([b, g3], 0);
    }
  }
  function energyPulse(x) {
    if (!ok(x)) return;
    const t = T(x);
    boom(x, 0.85, 150, 40, 0.75);
    const z = osc('sawtooth', t, 0.3, 1900, 130);
    const lp = ac.createBiquadFilter(); lp.type = 'lowpass'; lp.Q.value = 7;
    lp.frequency.setValueAtTime(5200, t); lp.frequency.exponentialRampToValueAtTime(300, t + 0.3);
    const g = ac.createGain(); env(g.gain, t, 0.004, 0.11, 0.32);
    chain([z, lp, g], 0.1, 0.4);
    whoosh(x, 0.5, 600, 5200, 0.26, 0.12, -0.2, 0.5, 0.45);
  }
  function chime(x) {
    if (!ok(x)) return;
    [[1046.5, 0], [1318.5, 0.065], [1568.0, 0.13], [2093.0, 0.2]].forEach(([f, d], i) => {
      const t = T(x + d);
      const o = osc('sine', t, 1.1, f), o2 = osc('sine', t, 0.6, f * 2), o3 = osc('triangle', t, 0.2, f * 3);
      const g = ac.createGain(); env(g.gain, t, 0.004, 0.14 - i * 0.015, 1.0);
      const g2 = ac.createGain(); g2.gain.value = 0.22;
      const g3 = ac.createGain(); g3.gain.value = 0.05;
      o2.connect(g2); g2.connect(g); o3.connect(g3); g3.connect(g);
      chain([o, g], 0.25, 0.6);
    });
  }
  function bellStrike(x, amp, pan) {
    if (!ok(x)) return;
    const t = T(x);
    const f0 = 1480;
    const partials = [[1, 1, 1.6], [1.0012, 0.5, 1.5], [2.03, 0.42, 0.95], [2.74, 0.33, 0.7], [3.98, 0.17, 0.48], [5.42, 0.1, 0.32], [6.8, 0.05, 0.22]];
    for (const [ratio, a, dec] of partials) {
      const o = osc('sine', t, dec + 0.1, f0 * ratio);
      const g = ac.createGain(); env(g.gain, t, 0.0015, 0.11 * amp * a, dec);
      chain([o, g], pan, 0.45);
    }
    const n = noise(t, 0.02);
    const bp = ac.createBiquadFilter(); bp.type = 'bandpass'; bp.frequency.value = 7000; bp.Q.value = 1.2;
    const g = ac.createGain(); env(g.gain, t, 0.001, 0.1 * amp, 0.015);
    chain([n, bp, g], pan, 0.2);
  }

  // ---- the score ----
  function thud(x, peak, f0 = 180, f1 = 80, len = 0.22, pan = 0) {
    if (!ok(x)) return;
    const t = T(x);
    const o = osc('sine', t, len, f0, f1);
    const g = ac.createGain(); env(g.gain, t, 0.004, peak, len);
    chain([o, g], pan, 0.2);
  }
  const avatarLand = LAND.avatar, buttonLand = LAND.button, bellLand = LAND.bell;
  // intro: light opens, avatar pops in, plate sweeps out, impact, button slides in, bell drops in
  whoosh(0.0, 0.66, 220, 2800, 0.3, 0.85, -0.7, -0.2, 0.4);
  if (ok(0.05)) { // low riser under the opening
    const t = T(0.05);
    const o = osc('sawtooth', t, 0.6, 55, 110);
    const lp = ac.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.value = 500;
    const g = ac.createGain(); g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.12, t + 0.55); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.62);
    chain([o, lp, g], 0, 0.2);
  }
  whoosh(TL.avatarIn[0], 0.26, 500, 2600, 0.2, 0.85, -0.7, -0.5, 0.3);
  thud(avatarLand, 0.42, 240, 85, 0.24, -0.5); tick(avatarLand, 0.05, 4200, 0.04, -0.5);
  whoosh(TL.wipeIn[0], 0.42, 350, 4200, 0.26, 0.8, -0.5, 0.6, 0.35);
  boom(TL.impact, 0.95);
  neonBuzz();
  whoosh(TL.buttonIn[0], 0.24, 3000, 800, 0.16, 0.35, 0.9, 0.3, 0.25);
  thud(buttonLand, 0.26, 170, 90, 0.2, 0.25);
  whoosh(TL.bellIn[0], 0.18, 1500, 4200, 0.1, 0.8, 0.8, 0.5, 0.3);
  bellStrike(bellLand, 0.32, 0.55);
  shimmer(TL.shine1 + 0.02, 0.024);
  for (const t0 of TL.crown.slice(1)) tick(t0, 0.025, 5600, 0.12, -0.6);
  // the subscribe interaction
  for (const [a] of HOVER.btn) tick(a, 0.05, 2300, 0.03, 0.3);
  mouseClick(TL.btnDown, false);
  mouseClick(TL.btnUp, true);
  energyPulse(TL.btnUp + 0.005);
  chime(TL.btnUp + 0.40);
  tick(TL.bellPing, 0.06, 1760, 0.18, 0.5); tick(TL.bellPing + 0.06, 0.04, 2637, 0.16, 0.5);
  for (const [a] of HOVER.bell) tick(a, 0.05, 2500, 0.03, 0.5);
  mouseClick(TL.bellDown, false);
  mouseClick(TL.bellUp, true);
  shimmer(TL.bellUp + 0.02, 0.03);
  for (const st of BELL_STRIKES) bellStrike(st.t, st.amp, 0.45 + 0.2 * st.dir);
  shimmer(TL.shine2 + 0.05, 0.018);
  // outro: surge, bell spins out, button slides away, plate sweeps back, avatar vanishes in a burst
  whoosh(TL.outro - 0.1, 0.36, 300, 3000, 0.22, 0.95, 0.4, 0.0, 0.3);
  whoosh(TL.bellOut[0], 0.22, 1200, 4500, 0.12, 0.6, 0.6, 0.9, 0.3); tick(TL.bellOut[0] + 0.02, 0.04, 2600, 0.08, 0.7);
  whoosh(TL.buttonOut[0], 0.3, 900, 3200, 0.18, 0.6, 0.3, 0.9, 0.3);
  whoosh(TL.wipeOut[0], 0.42, 3600, 300, 0.3, 0.4, 0.6, -0.6, 0.45);
  boom(TL.avatarOut[0] + 0.2, 0.6, 90, 30, 0.75);
  shimmer(TL.avatarOut[0] + 0.22, 0.026);
  return out;
}

// WAV encoder (16-bit PCM) for the export path, with peak normalisation.
function encodeWav(buffer, peakDb = -1) {
  const ch = buffer.numberOfChannels, sr = buffer.sampleRate, n = buffer.length;
  const data = []; let peak = 1e-9;
  for (let c = 0; c < ch; c++) { const d = buffer.getChannelData(c); data.push(d); for (let i = 0; i < n; i++) peak = Math.max(peak, Math.abs(d[i])); }
  const gain = Math.pow(10, peakDb / 20) / peak;
  const fade = Math.floor(sr * 0.12);
  const ab = new ArrayBuffer(44 + n * ch * 2), v = new DataView(ab);
  const str = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  str(0, 'RIFF'); v.setUint32(4, 36 + n * ch * 2, true); str(8, 'WAVE'); str(12, 'fmt ');
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, ch, true); v.setUint32(24, sr, true);
  v.setUint32(28, sr * ch * 2, true); v.setUint16(32, ch * 2, true); v.setUint16(34, 16, true);
  str(36, 'data'); v.setUint32(40, n * ch * 2, true);
  let o = 44;
  for (let i = 0; i < n; i++) {
    const f = i > n - fade ? (n - i) / fade : 1;
    for (let c = 0; c < ch; c++) { const s = clamp(data[c][i] * gain * f, -1, 1); v.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7fff, true); o += 2; }
  }
  return ab;
}

// ───────────────────────────── boot ─────────────────────────────
function b64ToBuf(b64) { const s = atob(b64); const u = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i); return u.buffer; }
async function initAnimation(assets) {
  const faces = [
    new FontFace(FAMILY, b64ToBuf(assets.heeboHebrew), { weight: '100 900', unicodeRange: 'U+0307-0308, U+0590-05FF, U+200C-2010, U+20AA, U+25CC, U+FB1D-FB4F' }),
    new FontFace(FAMILY, b64ToBuf(assets.heeboLatin), { weight: '100 900', unicodeRange: 'U+0000-00FF, U+2000-206F' }),
  ];
  for (const f of faces) { await f.load(); document.fonts.add(f); }
  const load = async b64 => { const im = new Image(); im.src = 'data:image/png;base64,' + b64; await im.decode(); return im; };
  IMG_AV = await load(assets.avatar);
  IMG_REST = await load(assets.rest);
  const c = document.createElement('canvas'); c.width = 8; c.height = 8;
  setupText(c.getContext('2d'));
}
