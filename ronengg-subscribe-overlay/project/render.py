"""RonenGG Daily - cinematic subscribe overlay renderer.

Renders 1080x1920 @ 60 fps straight-alpha 16-bit PNG frames from the decomposed layers.
Usage:
  python3 render.py --out frames/ [--frames 0,93,150] [--jobs 4] [--layout portrait|landscape]
"""
import os, sys, json, math, argparse
import numpy as np
import cv2
from common import load_rgba, save_rgba, premul, unpremul, smoothstep
from geom import *
from timeline import *

HERE = os.path.dirname(os.path.abspath(__file__))
LAYERS = os.path.join(HERE, 'layers')

LAYOUTS = {
    # canvas W, H, art scale, art origin (top-left of the 2000x668 artwork on canvas)
    'portrait': dict(W=1080, H=1920, S=0.45, X0=70, Y0=1000),
    'landscape': dict(W=1920, H=1080, S=0.45, X0=510, Y0=670),
    'still_hires': dict(W=2100, H=760, S=1.0, X0=50, Y0=46),
}

PURPLE = np.array([0.62, 0.22, 1.00], np.float32)
MAGENTA = np.array([0.95, 0.30, 1.00], np.float32)
VIOLET = np.array([0.45, 0.20, 1.00], np.float32)
WHITE = np.array([1.0, 0.92, 1.0], np.float32)


# =====================================================================================
class Layer:
    """Premultiplied RGBA image living in art-output coordinates with integer offset."""

    def __init__(self, img, ox, oy):
        self.img, self.ox, self.oy = img.astype(np.float32), int(ox), int(oy)
        self.h, self.w = img.shape[:2]
        self._half = None

    def half(self):
        if self._half is None:
            self._half = cv2.resize(self.img, (max(1, self.w // 2), max(1, self.h // 2)), interpolation=cv2.INTER_AREA)
        return self._half


def crop_layer(img, pad=4):
    a = img[..., 3]
    ys, xs = np.where(a > 1e-4)
    if len(ys) == 0:
        return Layer(np.zeros((1, 1, 4), np.float32), 0, 0)
    y0, y1 = max(0, ys.min() - pad), min(img.shape[0], ys.max() + pad + 1)
    x0, x1 = max(0, xs.min() - pad), min(img.shape[1], xs.max() + pad + 1)
    return Layer(img[y0:y1, x0:x1].copy(), x0, y0)


def affine(tx=0, ty=0, rot=0, sc=1, sx=1, sy=1, px=0, py=0):
    """Matrix (2x3) mapping art coords -> animated art coords about pivot (px,py)."""
    r = math.radians(rot)
    c, s = math.cos(r), math.sin(r)
    a, b = sc * sx, sc * sy
    M = np.array([[c * a, -s * b, 0], [s * a, c * b, 0]], np.float64)
    M[:, 2] = np.array([px + tx, py + ty]) - M[:, :2] @ np.array([px, py])
    return M


def is_identity(M):
    return np.allclose(M[:, :2], np.eye(2), atol=1e-6) and np.allclose(M[:, 2], np.round(M[:, 2]), atol=1e-4)


def mat3(M):
    return np.vstack([M, [0, 0, 1]])


class Canvas:
    def __init__(self, W, H, X0, Y0, roi):
        self.W, self.H, self.X0, self.Y0 = W, H, X0, Y0
        self.ry0, self.ry1 = roi
        self.buf = np.zeros((self.ry1 - self.ry0, W, 4), np.float32)

    def place(self, layer, M, interp=cv2.INTER_CUBIC):
        """Return (premultiplied ROI-sized image) of layer warped by art-space affine M."""
        out = np.zeros_like(self.buf)
        self.add_warp(out, layer, M, 1.0, interp)
        return out

    def add_warp(self, out, layer, M, weight, interp=cv2.INTER_CUBIC):
        lin_scale = math.sqrt(abs(np.linalg.det(M[:, :2])))
        use_half = lin_scale < 0.62
        src = layer.half() if use_half else layer.img
        k = 0.5 if use_half else 1.0
        # mip pixel -> art coords
        A = np.array([[1 / k, 0, layer.ox + 0.5 / k - 0.5], [0, 1 / k, layer.oy + 0.5 / k - 0.5], [0, 0, 1]])
        T = np.array([[1, 0, self.X0], [0, 1, self.Y0 - self.ry0], [0, 0, 1]], np.float64)
        F = T @ mat3(M) @ A
        # bounding box on ROI
        h, w = src.shape[:2]
        corners = np.array([[-1, -1, 1], [w + 1, -1, 1], [-1, h + 1, 1], [w + 1, h + 1, 1]], np.float64).T
        pc = (F @ corners)[:2]
        x0 = int(max(0, math.floor(pc[0].min()) - 2)); x1 = int(min(self.W, math.ceil(pc[0].max()) + 2))
        y0 = int(max(0, math.floor(pc[1].min()) - 2)); y1 = int(min(out.shape[0], math.ceil(pc[1].max()) + 2))
        if x1 <= x0 or y1 <= y0:
            return
        if not use_half and is_identity(F[:2]):
            dx, dy = int(round(F[0, 2])), int(round(F[1, 2]))
            sx0, sy0 = max(0, -dx), max(0, -dy)
            dx0, dy0 = max(0, dx), max(0, dy)
            ww = min(w - sx0, out.shape[1] - dx0); hh = min(h - sy0, out.shape[0] - dy0)
            if ww > 0 and hh > 0:
                out[dy0:dy0 + hh, dx0:dx0 + ww] += src[sy0:sy0 + hh, sx0:sx0 + ww] * weight
            return
        F2 = F.copy()
        F2[0, 2] -= x0; F2[1, 2] -= y0
        warped = cv2.warpAffine(src, F2[:2], (x1 - x0, y1 - y0), flags=interp, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        if interp == cv2.INTER_CUBIC:
            np.clip(warped, 0, None, out=warped)
            warped[..., :3] = np.minimum(warped[..., :3], warped[..., 3:4])
        out[y0:y1, x0:x1] += warped * weight

    def over(self, img):
        self.buf = img + self.buf * (1 - img[..., 3:4])


def motion_blurred(cv, layer_fn, t, shutter=0.5, max_samples=24):
    """layer_fn(t) -> (Layer, M, alpha). Accumulates motion-blurred warp into a new ROI image."""
    dt = shutter / FPS
    L0, M0, a0 = layer_fn(t - dt / 2)
    L1, M1, a1 = layer_fn(t + dt / 2)
    Lc, Mc, ac = layer_fn(t)
    # displacement estimate of layer corners
    if Lc is None:
        return None
    pts = np.array([[Lc.ox, Lc.oy, 1], [Lc.ox + Lc.w, Lc.oy, 1], [Lc.ox, Lc.oy + Lc.h, 1], [Lc.ox + Lc.w, Lc.oy + Lc.h, 1]], np.float64).T
    disp = np.abs((M1 - M0) @ pts).max()
    n = int(min(max_samples, max(1, math.ceil(disp / 1.2))))
    out = np.zeros_like(cv.buf)
    if n == 1:
        if ac <= 0:
            return None
        cv.add_warp(out, Lc, Mc, ac)
        return out
    tot = 0
    for i in range(n):
        ts = t - dt / 2 + dt * (i + 0.5) / n
        L, M, a = layer_fn(ts)
        if a > 0:
            cv.add_warp(out, L, M, a, cv2.INTER_LINEAR)
        tot += 1
    out /= tot
    return out


# =====================================================================================
class Assets:
    def __init__(self, S):
        self.S = S
        def ld(name):
            return premul(load_rgba(os.path.join(LAYERS, name + '.png')))
        def sc(img):
            h, w = img.shape[:2]
            return cv2.resize(img, (int(round(w * S)), int(round(h * S))), interpolation=cv2.INTER_AREA)
        self.meta = json.load(open(os.path.join(LAYERS, 'meta.json')))
        raw = {n: ld(n) for n in ['bg', 'logo', 'frame', 'face0', 'face0r', 'face1', 'bar', 'tile_play', 'tile_blank', 'box', 'bell']}
        full = {n: sc(v) for n, v in raw.items()}
        self.full = full
        self.AH, self.AW = full['bg'].shape[:2]
        self.bg = full['bg']
        self.logo = crop_layer(full['logo'])
        # panel sub-layers share one bbox
        y0, y1 = int(200 * S) - 24, int(570 * S) + 24
        x0, x1 = int(615 * S) - 24, int(1830 * S) + 24
        self.pbox = (x0, y0, x1, y1)
        self.p = {n: full[n][y0:y1, x0:x1].copy() for n in ['frame', 'face0', 'face0r', 'face1', 'bar', 'tile_play', 'tile_blank']}
        fm = np.load(os.path.join(LAYERS, 'face_m.npy'))
        self.face_m = cv2.resize(fm, (self.AW, self.AH), interpolation=cv2.INTER_AREA)[y0:y1, x0:x1]
        # bell box sub-layers
        bx0, by0, bx1, by1 = int(1660 * S) - 24, int(225 * S) - 24, int(1975 * S) + 24, int(510 * S) + 24
        self.bbox = (bx0, by0, bx1, by1)
        self.box = full['box'][by0:by1, bx0:bx1].copy()
        self.bell = full['bell'][by0:by1, bx0:bx1].copy()
        am = np.load(os.path.join(LAYERS, 'arcs_m.npy'))
        self.arcs_m = cv2.resize(am, (self.AW, self.AH), interpolation=cv2.INTER_AREA)[by0:by1, bx0:bx1]
        # glow of arcs (emissive, premultiplied)
        g = self.arcs_m[..., None] * MAGENTA[None, None]
        self.arcs_glow = g + cv2.GaussianBlur(g, (0, 0), 2.5) * 1.2 + cv2.GaussianBlur(g, (0, 0), 7) * 0.9
        # box neon border glow (for hover / activation)
        b = self.box
        bs = unpremul(b)
        mxc = bs[..., :3].max(-1); mnc = bs[..., :3].min(-1)
        sat = (mxc - mnc) / np.maximum(mxc, 1e-4)
        neon = smoothstep(0.45, 0.85, mxc) * smoothstep(0.25, 0.6, sat) * b[..., 3] * (1 - self.arcs_m)
        n3 = neon[..., None] * PURPLE[None, None]
        self.box_glow = cv2.GaussianBlur(n3, (0, 0), 2.0) * 0.8 + cv2.GaussianBlur(n3, (0, 0), 6) * 0.8
        # panel neon border glow (for pulse flash)
        fr = self.p['frame']
        frs = unpremul(fr)
        mxc = frs[..., :3].max(-1); mnc = frs[..., :3].min(-1)
        sat = (mxc - mnc) / np.maximum(mxc, 1e-4)
        neon = smoothstep(0.5, 0.9, mxc) * fr[..., 3]
        n3 = neon[..., None] * PURPLE[None, None]
        self.frame_glow = cv2.GaussianBlur(n3, (0, 0), 2.0) * 0.7 + cv2.GaussianBlur(n3, (0, 0), 6) * 0.7
        # logo halo used while the logo travels without the band behind it
        la = self.logo.img[..., 3]
        pad = 40
        halo = np.zeros((self.logo.h + 2 * pad, self.logo.w + 2 * pad, 4), np.float32)
        a = np.zeros(halo.shape[:2], np.float32); a[pad:-pad, pad:-pad] = la
        hg = cv2.GaussianBlur(a, (0, 0), 9) * 0.55 + cv2.GaussianBlur(a, (0, 0), 22) * 0.45
        halo[..., :3] = hg[..., None] * PURPLE[None, None] * 0.85
        halo[..., 3] = hg * 0.85 * PURPLE.max()
        self.logo_halo = Layer(halo, self.logo.ox - pad, self.logo.oy - pad)
        # band glow for its ignition front
        self.tri_col = np.array(self.meta['tri_col'], np.float32)
        self.cursor = make_cursor(S)


def make_cursor(S):
    """Clean white arrow cursor with dark outline and soft shadow (premultiplied), tip at (pad,pad)."""
    ss = 8
    hpx = 50.0  # cursor height on a 1080-wide canvas
    u = hpx / 21.0
    pts = np.array([(0, 0), (0, 17.2), (4.1, 13.4), (6.9, 19.9), (9.6, 18.8), (6.9, 12.5), (12.4, 12.5)], np.float64) * u
    pad = 14
    w = int(13 * u) + 2 * pad
    h = int(21 * u) + 2 * pad
    def poly(pp, dil=0.0):
        c = np.zeros((h * ss, w * ss), np.uint8)
        q = np.round((pp + pad) * ss).astype(np.int32)
        cv2.fillPoly(c, [q], 255, lineType=cv2.LINE_AA)
        if dil > 0:
            r = int(dil * ss)
            c = cv2.dilate(c, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
        return cv2.resize(c.astype(np.float32) / 255, (w, h), interpolation=cv2.INTER_AREA)
    inner = poly(pts)
    outer = poly(pts, 1.6)
    img = np.zeros((h, w, 4), np.float32)
    # shadow
    sh = cv2.GaussianBlur(outer, (0, 0), 3.2)
    M = np.float32([[1, 0, 2.5], [0, 1, 4.0]])
    sh = cv2.warpAffine(sh, M, (w, h)) * 0.42
    img[..., 3] = sh
    # dark outline
    ol = np.zeros_like(img); ol[..., :3] = 0.06 * outer[..., None]; ol[..., 3] = outer
    img = ol + img * (1 - ol[..., 3:4])
    # white body with very subtle vertical sheen
    yy = np.linspace(0, 1, h)[:, None]
    body = np.zeros_like(img)
    col = 1.0 - 0.06 * yy
    body[..., 0] = inner * col; body[..., 1] = inner * col; body[..., 2] = inner * (col + 0.02)
    body[..., 3] = inner
    img = body + img * (1 - body[..., 3:4])
    return dict(img=np.clip(img, 0, 1), tip=(pad, pad))


# =====================================================================================
# Sprite / FX helpers (emissive buffers: rgb light, converted to premultiplied at the end)

def add_blob(buf, x, y, r, col, oy=0):
    """Gaussian blob of radius r (sigma) into emissive buffer (H,W,3)."""
    y = y - oy
    R = int(math.ceil(r * 3)) + 1
    xi, yi = int(round(x)), int(round(y))
    x0, x1 = max(0, xi - R), min(buf.shape[1], xi + R + 1)
    y0, y1 = max(0, yi - R), min(buf.shape[0], yi + R + 1)
    if x1 <= x0 or y1 <= y0:
        return
    xs = np.arange(x0, x1, dtype=np.float32) - x
    ys = np.arange(y0, y1, dtype=np.float32) - y
    g = np.exp(-(ys[:, None] ** 2 + xs[None, :] ** 2) / (2 * r * r))
    buf[y0:y1, x0:x1] += g[..., None] * col[None, None]


def add_streak(buf, x0, y0, x1, y1, width, col, oy=0, head=True):
    """Anti-aliased capsule (motion-stretched spark) into emissive buffer."""
    y0 -= oy; y1 -= oy
    R = width * 3 + 2
    bx0, bx1 = int(max(0, min(x0, x1) - R)), int(min(buf.shape[1], max(x0, x1) + R + 1))
    by0, by1 = int(max(0, min(y0, y1) - R)), int(min(buf.shape[0], max(y0, y1) + R + 1))
    if bx1 <= bx0 or by1 <= by0:
        return
    yy, xx = np.mgrid[by0:by1, bx0:bx1].astype(np.float32)
    dx, dy = x1 - x0, y1 - y0
    L2 = dx * dx + dy * dy + 1e-6
    t = np.clip(((xx - x0) * dx + (yy - y0) * dy) / L2, 0, 1)
    d2 = (xx - x0 - t * dx) ** 2 + (yy - y0 - t * dy) ** 2
    g = np.exp(-d2 / (2 * width * width))
    if head:
        g = g * (0.35 + 0.65 * t)  # brighter head
    buf[by0:by1, bx0:bx1] += g[..., None] * col[None, None]


def emissive_to_premul(e):
    rgb = np.clip(e, 0, None)
    # soft-clip highlights toward white like film
    a = np.clip(rgb.max(-1, keepdims=True), 0, 1)
    over1 = np.clip(rgb.max(-1, keepdims=True) - 1, 0, None)
    rgb = np.clip(rgb + over1 * 0.5, 0, 1)
    rgb = np.minimum(rgb, a)
    return np.concatenate([rgb, a], -1)


def ring_profile(d, R, w):
    return np.exp(-((d - R) / w) ** 2)


# =====================================================================================
class Renderer:
    def __init__(self, layout='portrait'):
        L = LAYOUTS[layout]
        self.L = L
        self.W, self.H, self.S, self.X0, self.Y0 = L['W'], L['H'], L['S'], L['X0'], L['Y0']
        self.A = Assets(self.S)
        S = self.S
        self.roi = (max(0, int(self.Y0 - 330 * self.S / 0.45)), min(self.H, int(self.Y0 + 600 * self.S / 0.45)))
        self.no_fx = False
        a = self.A
        # key points (art-output coords)
        self.logo_c = (RING_C[0] * S, RING_C[1] * S)
        self.click1 = (1240 * S, 385 * S)
        self.click2 = (1836 * S, 386 * S)
        self.bell_pivot = (BELL_PIVOT[0] * S, (BELL_PIVOT[1] - 8) * S)
        self.bell_c = (BELL_CENTER[0] * S, BELL_CENTER[1] * S)
        self.box_c = (BOX_AXIS * S, (BOX_OUT[1] + BOX_OUT[3]) / 2 * S)
        self.panel_pivot = (650 * S, 340 * S)
        self.face_rect = tuple(v * S for v in FACE[:4]) + (FACE[4] * S,)
        self.tile_c = ((TILE[0] + TILE[2]) / 2 * S, (TILE[1] + TILE[3]) / 2 * S)
        self.rng = np.random.default_rng(7)
        self._init_particles()
        self.breath_glow = None
        self.band = self.A.bg
        # border path of the face for the light pulse
        self.border_path = self._rrect_path(*[v * S for v in (FACE[0] - 1, FACE[1] - 1, FACE[2] + 1, FACE[3] + 1)], (FACE[4] + 1) * S)

    # ------------------------------------------------------------------ geometry helpers
    def canvas_xy(self, p):
        return (p[0] + self.X0, p[1] + self.Y0)

    def _rrect_path(self, x0, y0, x1, y1, r, n=1400):
        pts = []
        segs = [((x0 + r, y0), (x1 - r, y0)), ('arc', (x1 - r, y0 + r), -90, 0), ((x1, y0 + r), (x1, y1 - r)), ('arc', (x1 - r, y1 - r), 0, 90),
                ((x1 - r, y1), (x0 + r, y1)), ('arc', (x0 + r, y1 - r), 90, 180), ((x0, y1 - r), (x0, y0 + r)), ('arc', (x0 + r, y0 + r), 180, 270)]
        for s in segs:
            if s[0] == 'arc':
                c, a0, a1 = s[1], s[2], s[3]
                for a in np.linspace(a0, a1, 40, endpoint=False):
                    pts.append((c[0] + r * math.cos(math.radians(a)), c[1] + r * math.sin(math.radians(a))))
            else:
                (ax, ay), (bx, by) = s
                for u in np.linspace(0, 1, 120, endpoint=False):
                    pts.append((ax + (bx - ax) * u, ay + (by - ay) * u))
        P = np.array(pts)
        d = np.r_[0, np.cumsum(np.hypot(*np.diff(P, axis=0).T))]
        return P, d

    # ------------------------------------------------------------------ particles
    def _init_particles(self):
        rng = self.rng
        S = self.S
        lc = self.logo_c
        # A: energy gathering toward the logo during entrance
        n = 42
        ang = rng.uniform(0, 2 * math.pi, n)
        r0 = rng.uniform(150, 330, n)
        self.pA = dict(ang=ang, r0=r0, t0=rng.uniform(0.0, 0.18, n), dur=rng.uniform(0.30, 0.42, n),
                       size=rng.uniform(1.2, 2.6, n), hue=rng.uniform(0, 1, n), spin=rng.uniform(0.6, 1.4, n) * rng.choice([-1, 1], n))
        # B: embers rising off the band (entrance tail + hold)
        n = 60
        self.pB = dict(x=rng.uniform(40 * S, 1990 * S, n), y=rng.uniform(150 * S, 560 * S, n), t0=rng.uniform(0.55, 3.95, n),
                       life=rng.uniform(0.7, 1.4, n), vx=rng.uniform(-14, 14, n), vy=rng.uniform(-55, -20, n),
                       size=rng.uniform(0.9, 2.0, n), hue=rng.uniform(0, 1, n), tw=rng.uniform(5, 11, n))
        # streaks
        self.streaks = [dict(y=(334 + dy) * S, t0=t0, dur=dur, len=ln, w=w, b=b)
                        for dy, t0, dur, ln, w, b in [(-40, 0.00, 0.42, 520, 1.6, 0.9), (55, 0.06, 0.40, 380, 1.2, 0.7),
                                                      (-150, 0.10, 0.45, 300, 1.0, 0.5), (140, 0.14, 0.40, 260, 1.0, 0.45), (5, 0.20, 0.38, 640, 2.0, 1.0)]]
        # click 1 burst
        n = 34
        self.burst1 = dict(ang=rng.uniform(0, 2 * math.pi, n), sp=rng.uniform(160, 620, n), life=rng.uniform(0.28, 0.6, n),
                           size=rng.uniform(1.0, 2.2, n), hue=rng.uniform(0, 1, n))
        n = 26
        self.burst2 = dict(ang=rng.uniform(0, 2 * math.pi, n), sp=rng.uniform(140, 520, n), life=rng.uniform(0.25, 0.55, n),
                           size=rng.uniform(1.0, 2.0, n), hue=rng.uniform(0, 1, n))
        # band dissolve sparkles at exit
        n = 70
        self.pD = dict(x=rng.uniform(20 * S, 1990 * S, n), y=rng.uniform(80 * S, 640 * S, n), t0=rng.uniform(3.86, 4.06, n),
                       life=rng.uniform(0.25, 0.5, n), vx=rng.uniform(-60, 60, n), vy=rng.uniform(-90, -10, n),
                       size=rng.uniform(1.0, 2.2, n), hue=rng.uniform(0, 1, n))
        # exit trails
        self.trail_seed = rng.uniform(0, 1, (3, 40, 4))

    def pcol(self, h, k=1.0):
        c = MAGENTA * (1 - h) + VIOLET * h
        return (c * 0.85 + WHITE * 0.15) * k

    # ------------------------------------------------------------------ animation channels
    def logo_tf(self, t):
        u = t - T_LOGO
        if t < T_LOGO_OUT[0]:
            p = spring(u, 0.70, 2.0, 7.0)
            a = smooth(lin(t, T_LOGO, T_LOGO + 0.10))
            off = snap(1 - p, 430)
            M = affine(tx=-430 * off, ty=12 * off, rot=-24 * off, sc=1 - 0.18 * off, px=self.logo_c[0], py=self.logo_c[1])
            return M, a
        q = ease_in_cubic(lin(t, *T_LOGO_OUT))
        a = 1 - smooth(lin(t, T_LOGO_OUT[1] - 0.16, T_LOGO_OUT[1]))
        M = affine(tx=-520 * q, ty=-6 * q, rot=-20 * q, sc=1 - 0.2 * q, px=self.logo_c[0], py=self.logo_c[1])
        return M, a

    def panel_tf(self, t):
        if t < T_PANEL_OUT[0]:
            p = spring(t - T_PANEL, 0.80, 2.2, 9.0)
            a = smooth(lin(t, T_PANEL, T_PANEL + 0.12))
            off = snap(1 - p, 330)
            return affine(tx=-330 * off, sc=1 - 0.22 * off, px=self.panel_pivot[0], py=self.panel_pivot[1]), a
        q = ease_in_cubic(lin(t, *T_PANEL_OUT))
        a = 1 - smooth(lin(t, T_PANEL_OUT[1] - 0.14, T_PANEL_OUT[1]))
        return affine(tx=-360 * q, sc=1 - 0.25 * q, px=self.panel_pivot[0], py=self.panel_pivot[1]), a

    def box_tf(self, t):
        if t < T_BOX_OUT[0]:
            p = spring(t - T_BOX, 0.70, 2.1, 7.0)
            a = smooth(lin(t, T_BOX, T_BOX + 0.10))
            off = snap(1 - p, 150)
            return affine(tx=150 * off, ty=-46 * off, rot=22 * off, sc=1 - 0.35 * off, px=self.box_c[0], py=self.box_c[1]), a
        q = ease_in_cubic(lin(t, *T_BOX_OUT))
        a = 1 - smooth(lin(t, T_BOX_OUT[1] - 0.12, T_BOX_OUT[1]))
        return affine(tx=330 * q, ty=-70 * q, rot=28 * q, sc=1 - 0.3 * q, px=self.box_c[0], py=self.box_c[1]), a

    def cursor_state(self, t):
        """returns (x,y) canvas tip pos, alpha, press scale."""
        S = self.S
        start = (self.W * 0.93, self.Y0 + 480)
        c1 = self.canvas_xy(self.click1)
        c2 = self.canvas_xy(self.click2)
        end = (self.W * 0.98, self.Y0 + 520)
        if t < T_CUR_IN or t > T_CUR_OUT[1] + 0.02:
            return None
        if t < T_CUR_MOVE2[0]:
            u = min_jerk(lin(t, *T_CUR_MOVE1))
            ctrl = (start[0] - 10, (start[1] + c1[1]) / 2 + 30)
            ctrl = ((start[0] + c1[0]) / 2 + 60, start[1] - 40)
            p = bezier2(start, ctrl, c1, u)
            # tiny settle overshoot correction
            if t > T_CUR_MOVE1[1] - 0.06:
                v = lin(t, T_CUR_MOVE1[1] - 0.06, T_CUR_MOVE1[1] + 0.06)
                p = (p[0] - 1.5 * math.sin(math.pi * v), p[1] - 1.0 * math.sin(math.pi * v))
        elif t < T_CUR_OUT[0]:
            u = min_jerk(lin(t, *T_CUR_MOVE2))
            ctrl = ((c1[0] + c2[0]) / 2, min(c1[1], c2[1]) - 55)
            p = bezier2(c1, ctrl, c2, u)
        else:
            u = ease_in_cubic(lin(t, *T_CUR_OUT)) * 0.6 + 0.4 * min_jerk(lin(t, *T_CUR_OUT))
            ctrl = (c2[0] + 40, c2[1] + 90)
            p = bezier2(c2, ctrl, end, u)
        a = smooth(lin(t, T_CUR_IN, T_CUR_IN + 0.12)) * (1 - smooth(lin(t, T_CUR_OUT[0] + 0.12, T_CUR_OUT[1])))
        press = 1.0
        for tc, tr in [(T_CLICK1, T_REL1), (T_CLICK2, T_REL2)]:
            if tc - 0.03 <= t <= tr + 0.10:
                if t < tc:
                    press = 1 - 0.12 * smooth(lin(t, tc - 0.03, tc))
                elif t < tr:
                    press = 0.88
                else:
                    press = 0.88 + 0.12 * smooth(lin(t, tr, tr + 0.10))
        return p, a, press

    # ------------------------------------------------------------------ panel group
    def panel_group(self, t):
        A = self.A
        S = self.S
        x0, y0, x1, y1 = A.pbox
        P = A.p
        h, w = P['frame'].shape[:2]
        out = P['frame'].copy()
        # face state
        sub_u = lin(t, *T_SWEEP)
        cx, cy = self.click1[0] - x0, self.click1[1] - y0
        flipping = T_FLIP[0] <= t
        if t < T_SWEEP[0]:
            face = P['face0'] if (t >= T_FLIP[0]) else P['face0r']
        elif t >= T_SWEEP[1]:
            face = P['face1']
        else:
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            d = np.hypot(xx - cx, yy - cy)
            R = ease_out_cubic(sub_u) * 560 * S * 2.2
            m = smoothstep(R, R - 26, d)[..., None]
            face = P['face0'] * (1 - m) + P['face1'] * m
        # hover brighten
        hv = smooth(lin(t, T_CLICK1 - 0.14, T_CLICK1 - 0.04)) * (1 - smooth(lin(t, T_CLICK1, T_CLICK1 + 0.1)))
        # press: face sinks into the bezel and springs back
        if T_CLICK1 - 0.01 <= t <= T_CLICK1 + 0.6:
            if t < T_REL1:
                k = 1 - 0.035 * ease_out_cubic(lin(t, T_CLICK1, T_CLICK1 + 0.045))
                dy = 2.2 * ease_out_cubic(lin(t, T_CLICK1, T_CLICK1 + 0.045))
                dark = 0.14 * ease_out_cubic(lin(t, T_CLICK1, T_CLICK1 + 0.045))
            else:
                v = t - T_REL1
                sp = spring(v, 0.38, 4.2, 0.0)  # 0->1 with bounce
                k = 0.965 + 0.035 * sp
                dy = 2.2 * (1 - sp)
                dark = 0.14 * max(0.0, 1 - sp)
            M = affine(ty=dy, sc=k, px=cx, py=cy)
            M2 = mat3(M)
            fw = cv2.warpAffine(face, M, (w, h), flags=cv2.INTER_CUBIC)
            fw = np.clip(fw, 0, None)
            fw[..., :3] *= (1 - dark)
        else:
            M2 = np.eye(3)
            fw = face
            M = None
        if M is not None:
            fw = fw * A.face_m[..., None]  # clip to the bezel opening while pressed
        if hv > 0:
            fw = fw.copy(); fw[..., :3] = np.minimum(fw[..., :3] * (1 + 0.10 * hv), fw[..., 3:4])
        out = fw + out * (1 - fw[..., 3:4])
        # tile (play -> check flip)
        tile = self.tile_image(t, w, h, x0, y0)
        if M is not None:
            tile = np.clip(cv2.warpAffine(tile, M, (w, h), flags=cv2.INTER_CUBIC), 0, None)
        if M is not None:
            tile = tile * A.face_m[..., None]
        out = tile + out * (1 - tile[..., 3:4])
        # bar on top
        out = P['bar'] + out * (1 - P['bar'][..., 3:4])
        return Layer(out, x0, y0)

    def tile_image(self, t, w, h, x0, y0):
        A = self.A
        P = A.p
        tcx, tcy = self.tile_c[0] - x0, self.tile_c[1] - y0
        if t < T_FLIP[0]:
            return P['tile_play']
        u = lin(t, *T_FLIP)
        ang = math.pi * ease_in_out_cubic(u)
        front = ang < math.pi / 2
        sx = abs(math.cos(ang))
        if not front:
            img = P['tile_blank'].copy()
            cp = smooth(lin(t, *T_CHECK))
            if cp > 0:
                img = self.draw_check(img, cp, x0, y0)
        else:
            img = P['tile_play'].copy()
        pop = 1 + 0.07 * math.sin(math.pi * lin(t, T_FLIP[1] - 0.05, T_FLIP[1] + 0.16)) if u >= 0.5 else 1
        sx = max(sx, 0.002)
        # perspective-ish: slight vertical squash at edge-on
        sy = 1 - 0.04 * (1 - sx)
        M = affine(sx=sx * pop, sy=sy * pop, px=tcx, py=tcy)
        out = np.clip(cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC), 0, None)
        out[..., :3] = np.minimum(out[..., :3], out[..., 3:4])
        shade = 0.35 * (1 - sx)
        out[..., :3] *= (1 - shade)
        return out

    def draw_check(self, img, prog, x0, y0):
        S = self.S
        pts = np.array([(728, 358), (762, 392), (822, 322)], np.float64) * S - np.array([x0, y0])
        seg = np.hypot(*np.diff(pts, axis=0).T)
        L = seg.sum() * prog
        path = [pts[0]]
        acc = 0
        for i, sl in enumerate(seg):
            if acc + sl <= L:
                path.append(pts[i + 1]); acc += sl
            else:
                f = (L - acc) / sl
                path.append(pts[i] + (pts[i + 1] - pts[i]) * f)
                break
        ss = 6
        h, w = img.shape[:2]
        c = np.zeros((h * ss, w * ss), np.uint8)
        q = np.round(np.array(path) * ss).astype(np.int32)
        thick = int(round(21 * S * ss))
        cv2.polylines(c, [q], False, 255, thick, lineType=cv2.LINE_AA)
        for pnt in (q[0], q[-1]):
            cv2.circle(c, tuple(int(v) for v in pnt), thick // 2, 255, -1, lineType=cv2.LINE_AA)
        m = cv2.resize(c.astype(np.float32) / 255, (w, h), interpolation=cv2.INTER_AREA)
        col = self.A.tri_col
        # gentle top-down gradient like the original play glyph
        yy = np.linspace(0, 1, h)[:, None, None]
        colimg = col[None, None] * (1.15 - 0.3 * yy)
        m = m * img[..., 3]
        img[..., :3] = img[..., :3] * (1 - m[..., None]) + colimg * m[..., None] * 1.0
        return img

    # ------------------------------------------------------------------ bell box group
    def box_group(self, t):
        A = self.A
        S = self.S
        bx0, by0, bx1, by1 = A.bbox
        out = A.box.copy()
        h, w = out.shape[:2]
        # hover + activation glow on the box neon
        hv = smooth(lin(t, T_CLICK2 - 0.14, T_CLICK2 - 0.03))
        act = 0.0
        if t >= T_CLICK2:
            act = math.exp(-(t - T_CLICK2) / 0.16) * 0.45 + 0.22
        g = (0.35 * hv * (1 - smooth(lin(t, T_CLICK2, T_CLICK2 + 0.1))) + act) * (1 - smooth(lin(t, T_BOX_OUT[0], T_BOX_OUT[0] + 0.1)))
        if g > 0:
            e = A.box_glow * g
            out = emissive_add(out, e)
        # arcs flash
        if t >= T_CLICK2:
            fl = math.exp(-(t - T_CLICK2) / 0.22) * 0.75 + 0.12 + 0.10 * self.breath(t)
            out = emissive_add(out, A.arcs_glow * fl * (1 - smooth(lin(t, T_BOX_OUT[0], T_BOX_OUT[0] + 0.1))))
        # bell: compress + swing about the top knob
        px, py = self.bell_pivot[0] - bx0, self.bell_pivot[1] - by0
        rot = 0.0; sx = sy = 1.0
        if t >= T_CLICK2 - 0.01:
            u = t - T_CLICK2
            if u < 0.05:
                c = ease_out_cubic(u / 0.05)
            else:
                c = max(0.0, 1 - spring(u - 0.05, 0.45, 6.0, 0.0))
            sy = 1 - 0.075 * c
            sx = 1 + 0.035 * c
            rot = damped_sin(t - T_SWING, 22.0, 3.1, 0.33)
        # small settle wiggle after the box lands
        rot += damped_sin(t - (T_BOX + 0.30), 5.0, 3.4, 0.16)
        bell = A.bell
        if abs(rot) > 1e-3 or abs(sy - 1) > 1e-4:
            bottom = (self.bell_c[0] - bx0, (BELL_CENTER[1] + 120) * S - by0)
            Msq = mat3(affine(sx=sx, sy=sy, px=bottom[0], py=bottom[1]))
            Mrot = mat3(affine(rot=rot, px=px, py=py))
            M = (Mrot @ Msq)[:2]
            bell = np.clip(cv2.warpAffine(bell, M, (w, h), flags=cv2.INTER_CUBIC), 0, None)
            bell[..., :3] = np.minimum(bell[..., :3], bell[..., 3:4])
        # bell aura after activation
        if t >= T_CLICK2:
            aura_k = (math.exp(-(t - T_CLICK2) / 0.25) * 0.5 + 0.14) * (1 - smooth(lin(t, T_BOX_OUT[0], T_BOX_OUT[0] + 0.1)))
            ba = bell[..., 3]
            aura = cv2.GaussianBlur(ba, (0, 0), 5 * S / 0.45)[..., None] * MAGENTA[None, None] * 0.55 * aura_k
            out = emissive_add(out, aura)
        out = bell + out * (1 - bell[..., 3:4])
        return Layer(out, bx0, by0)

    def breath(self, t):
        return 0.5 + 0.5 * math.sin(2 * math.pi * (t - T_HOLD[0]) / 0.72 - math.pi / 2)

    # ------------------------------------------------------------------ band (energy spikes)
    def band_image(self, t, cv):
        """Returns premultiplied band in ROI, plus emissive front glow."""
        S = self.S
        A = self.A
        if t < T_BAND[0] or t >= T_BAND_OUT[1]:
            return None, None
        AH, AW = self.band.shape[:2]
        xx = np.arange(AW, dtype=np.float32)
        dx = np.abs(xx - self.logo_c[0])
        front_glow = None
        if t < T_BAND_OUT[0]:
            u = lin(t, *T_BAND)
            f = 960 * S / 0.45 * (u ** 1.4) if u < 1 else 1e6
            m = smoothstep(f + 1, f - 70 * S / 0.45, dx)
            img = self.band * m[None, :, None]
            if u < 1:
                fg = np.exp(-((dx - f + 18) / (22 * S / 0.45)) ** 2) * (1 - u) ** 0.5
                front_glow = (self.band[..., 3] * fg[None, :])[..., None] * (PURPLE * 0.75 + WHITE * 0.06)[None, None]
        else:
            # dissolve: noise threshold rises, edges glow
            u = lin(t, *T_BAND_OUT)
            if not hasattr(self, '_noise'):
                rng = np.random.default_rng(3)
                n = rng.random((AH // 6 + 2, AW // 6 + 2)).astype(np.float32)
                n = cv2.resize(n, (AW, AH), interpolation=cv2.INTER_CUBIC)
                n = cv2.GaussianBlur(n, (0, 0), 2)
                n = (n - n.min()) / (n.max() - n.min())
                # bias: right side goes first (bell exits first)
                n = 0.65 * n + 0.35 * (1 - xx[None, :] / AW)
                self._noise = (n - n.min()) / (n.max() - n.min())
            th = u * 1.08
            m = smoothstep(th - 0.02, th + 0.06, self._noise)
            img = self.band * m[..., None]
            edge = np.exp(-((self._noise - th - 0.02) / 0.03) ** 2) * (1 - u)
            front_glow = (self.band[..., 3] * edge)[..., None] * (MAGENTA * 1.2 + WHITE * 0.2)[None, None]
        out = np.zeros_like(cv.buf)
        lay = Layer(img, 0, 0)
        cv.add_warp(out, lay, affine(), 1.0)
        fg = None
        if front_glow is not None:
            fg_img = np.concatenate([front_glow, np.zeros(front_glow.shape[:2] + (1,), np.float32)], -1)
            tmp = np.zeros_like(cv.buf)
            cv.add_warp(tmp, Layer(fg_img, 0, 0), affine(), 1.0)
            fg = tmp[..., :3]
        return out, fg

    # ------------------------------------------------------------------ FX
    def fx(self, t, cv, E_back, E_front):
        S = self.S
        oy = cv.ry0
        X0, Y0 = self.X0, self.Y0
        lc = self.canvas_xy(self.logo_c)
        # streaks
        for s in self.streaks:
            u = lin(t, s['t0'], s['t0'] + s['dur'])
            if 0 < u < 1:
                xh = -200 + (self.W * 0.95 + 250) * ease_in_out_cubic(u) * 1.0
                xh = X0 - 120 + (900 + 240) * ease_out_cubic(u)
                ln = s['len'] * S / 0.45 * (0.4 + 0.6 * math.sin(math.pi * u))
                k = s['b'] * math.sin(math.pi * u) ** 1.2
                y = Y0 + s['y']
                add_streak(E_front, xh - ln, y, xh, y, s['w'] * 0.9, (PURPLE * 0.9 + WHITE * 0.3) * k * 0.9, oy)
                add_streak(E_front, xh - ln * 0.35, y, xh, y, s['w'] * 0.45, WHITE * k * 0.8, oy)
        # A: gathering particles
        pA = self.pA
        for i in range(len(pA['ang'])):
            u = lin(t, pA['t0'][i], pA['t0'][i] + pA['dur'][i])
            if 0 < u < 1:
                e = ease_in_cubic(u)
                r = pA['r0'][i] * (1 - e) * S / 0.45 + 40 * S / 0.45 * e
                a = pA['ang'][i] + pA['spin'][i] * e * 1.2
                x = lc[0] + r * math.cos(a)
                y = lc[1] + r * math.sin(a) * 0.62
                k = smooth(u / 0.25) * (1 - smooth((u - 0.75) / 0.25))
                # streak along motion
                r2 = pA['r0'][i] * (1 - ease_in_cubic(max(0, u - 0.06))) * S / 0.45 + 40 * S / 0.45 * ease_in_cubic(max(0, u - 0.06))
                a2 = pA['ang'][i] + pA['spin'][i] * ease_in_cubic(max(0, u - 0.06)) * 1.2
                xp, yp = lc[0] + r2 * math.cos(a2), lc[1] + r2 * math.sin(a2) * 0.62
                add_streak(E_front, xp, yp, x, y, pA['size'][i] * 0.7, self.pcol(pA['hue'][i], 1.1 * k), oy)
        # landing flash at the logo
        fl = lin(t, T_LOGO_LAND - 0.02, T_LOGO_LAND + 0.35)
        if 0 < fl < 1:
            k = math.exp(-fl * 6) * smooth(fl / 0.08)
            add_blob(E_back, lc[0], lc[1], 90 * S / 0.45, PURPLE * 0.45 * k, oy)
            R = (128 + 90 * ease_out_cubic(fl)) * S / 0.45
            self.ring(E_back, lc, R, 2.0 * (1 - fl) + 1.0, (PURPLE + WHITE * 0.2) * 0.45 * (1 - fl) ** 2.5, oy, squash=1.0)
        # B: embers / ambient motes
        pB = self.pB
        for i in range(len(pB['x'])):
            u = (t - pB['t0'][i]) / pB['life'][i]
            if 0 < u < 1 and t < T_BAND_OUT[1] + 0.2:
                dt = t - pB['t0'][i]
                x = X0 + pB['x'][i] + pB['vx'][i] * dt
                y = Y0 + pB['y'][i] + pB['vy'][i] * dt
                k = math.sin(math.pi * u) * (0.6 + 0.4 * math.sin(pB['tw'][i] * dt * 6.28))
                hold_k = 0.55 if T_CUR_IN < t < T_HOLD[0] else 1.0
                add_blob(E_back, x, y, pB['size'][i], self.pcol(pB['hue'][i], 0.9 * k * hold_k), oy)
        # click 1: ripple, burst, border pulse
        self.click_fx(t, T_CLICK1, self.canvas_xy(self.click1), self.burst1, E_front, oy, ripple=True)
        self.border_pulse(t, E_front, oy)
        # sweep front highlight across the face
        u = lin(t, *T_SWEEP)
        if 0 < u < 1:
            pass  # handled in panel (radial reveal); front light drawn below
        # click 2: burst, expanding arcs, shockwave ring
        self.bell_fx(t, E_front, oy)
        # exit dissolve sparkles
        pD = self.pD
        for i in range(len(pD['x'])):
            u = (t - pD['t0'][i]) / pD['life'][i]
            if 0 < u < 1:
                dt = t - pD['t0'][i]
                x = X0 + pD['x'][i] + pD['vx'][i] * dt
                y = Y0 + pD['y'][i] + pD['vy'][i] * dt
                k = (1 - u) ** 1.5 * smooth(u / 0.15)
                add_streak(E_front, x - pD['vx'][i] * 0.03, y - pD['vy'][i] * 0.03, x, y, pD['size'][i] * 0.8, self.pcol(pD['hue'][i], 1.0 * k), oy)
        # exit trails behind moving parts
        self.trails(t, E_front, oy)

    def ring(self, E, c, R, w, col, oy, squash=1.0, arc=None):
        x0 = int(max(0, c[0] - R - 4 * w - 2)); x1 = int(min(self.W, c[0] + R + 4 * w + 3))
        y0 = int(max(oy, c[1] - R * squash - 4 * w - 2)); y1 = int(min(oy + E.shape[0], c[1] + R * squash + 4 * w + 3))
        if x1 <= x0 or y1 <= y0:
            return
        yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        d = np.hypot(xx - c[0], (yy - c[1]) / squash)
        g = ring_profile(d, R, w)
        if arc is not None:
            ang = np.degrees(np.arctan2(yy - c[1], xx - c[0]))
            amid, ahalf = arc
            da = np.abs((ang - amid + 180) % 360 - 180)
            g = g * smoothstep(ahalf, ahalf - 12, da)
        E[y0 - oy:y1 - oy, x0:x1] += g[..., None] * col[None, None]

    def click_fx(self, t, tc, c, burst, E, oy, ripple=True):
        u = t - tc
        if u < 0 or u > 0.8:
            return
        S = self.S
        if ripple:
            v = lin(u, 0, 0.42)
            if v < 1:
                R = 6 + 64 * ease_out_cubic(v)
                self.ring(E, c, R, 2.2 + 2 * (1 - v), (WHITE * 0.5 + PURPLE * 0.8) * (1 - v) ** 1.6, oy)
        # flash at contact
        if u < 0.15:
            k = math.exp(-u / 0.035)
            add_blob(E, c[0], c[1], 3.5, WHITE * 0.8 * k, oy)
            add_blob(E, c[0], c[1], 14, PURPLE * 0.32 * k, oy)
        for i in range(len(burst['ang'])):
            v = u / burst['life'][i]
            if 0 < v < 1:
                drag = 6.0
                sp = burst['sp'][i] * S / 0.45
                dist = sp * (1 - math.exp(-drag * u)) / drag
                ang = burst['ang'][i]
                x = c[0] + dist * math.cos(ang)
                y = c[1] + dist * math.sin(ang) + 60 * u * u
                vcur = sp * math.exp(-drag * u)
                tail = min(18, vcur * 0.03 + 2)
                xp = x - tail * math.cos(ang); yp = y - tail * math.sin(ang)
                k = (1 - v) ** 1.3
                add_streak(E, xp, yp, x, y, burst['size'][i] * 0.75, self.pcol(burst['hue'][i], 1.3 * k), oy)

    def border_pulse(self, t, E, oy):
        u = lin(t, T_CLICK1 + 0.01, T_CLICK1 + 0.52)
        if not (0 < u < 1):
            return
        P, d = self.border_path
        Ltot = d[-1]
        # start at the border point closest to the click (top edge)
        ci = int(np.argmin(np.hypot(P[:, 0] - self.click1[0], P[:, 1] - self.click1[1] + 50)))
        s0 = d[ci]
        travel = ease_out_cubic(u) * Ltot / 2
        k = (1 - smooth((u - 0.7) / 0.3)) * 1.0
        tail_len = 150 * self.S / 0.45
        step = 0.6
        for direction in (1, -1):
            if travel <= 0:
                continue
            nb = int(min(travel, tail_len) / step)
            for j in range(nb):
                back = j * step
                s_ = (s0 + direction * (travel - back)) % Ltot
                idx = min(np.searchsorted(d, s_), len(P) - 1)
                cx, cy = self.canvas_xy(P[idx])
                fall = (1 - back / tail_len) ** 2.0
                add_blob(E, cx, cy, 1.3, (WHITE * 0.45 + PURPLE * 0.9) * fall * k * 0.9 * step / 2.6, oy)
            hx, hy = self.canvas_xy(P[min(np.searchsorted(d, (s0 + direction * travel) % Ltot), len(P) - 1)])
            add_blob(E, hx, hy, 2.2, (WHITE * 0.9 + PURPLE * 0.5) * k, oy)
        # whole frame flash
        self._frame_flash = math.exp(-u * 4) * 0.9

    def bell_fx(self, t, E, oy):
        u = t - T_CLICK2
        if u < 0 or u > 1.0:
            return
        S = self.S
        # shockwave ring
        v = lin(u, 0.0, 0.55)
        if v < 1:
            R = (50 + 200 * ease_out_cubic(v)) * S / 0.45
            c = self.canvas_xy(self.bell_c)
            self.ring(E, c, R, (4 * (1 - v) + 1.2) * S / 0.45, (PURPLE * 1.0 + WHITE * 0.25) * (1 - v) ** 2.2 * 0.38, oy, squash=0.82)
        # expanding notification arcs (two waves)
        arcs = self.A.meta['arcs']
        for wave, delay in enumerate((0.0, 0.12)):
            v = lin(u, delay, delay + 0.55)
            if 0 < v < 1:
                grow = ease_out_cubic(v) * 70
                k = (1 - v) ** 1.4 * (1.0 if wave == 0 else 0.75)
                for side in ('L', 'R'):
                    a = arcs[side]
                    c = self.canvas_xy((a['cx'] * S, a['cy'] * S))
                    R = (a['r_out'] + 4 + grow) * S
                    wpx = max(1.2, a['w'] * S * 0.45 * (1 - 0.5 * v))
                    self.ring(E, c, R, wpx, (MAGENTA * 1.2 + WHITE * 0.3) * k, oy, arc=(a['a_mid'], a['a_half'] + 6 * v))
                    self.ring(E, c, R, wpx * 3.0, MAGENTA * 0.35 * k, oy, arc=(a['a_mid'], a['a_half'] + 6 * v))

    def trails(self, t, E, oy):
        S = self.S
        for gi, (fn, T, src_pts, col) in enumerate([
                (self.box_tf, T_BOX_OUT, [(1700, 300), (1700, 440), (1820, 250), (1820, 490)], MAGENTA),
                (self.panel_tf, T_PANEL_OUT, [(1650, 250), (1650, 450), (1200, 230), (1200, 560)], PURPLE),
                (self.logo_tf, T_LOGO_OUT, [(600, 250), (600, 450), (450, 110), (450, 600)], PURPLE)]):
            if not (T[0] < t < T[1] + 0.25):
                continue
            seeds = self.trail_seed[gi]
            for j in range(len(seeds)):
                te = T[0] + (T[1] - T[0]) * seeds[j, 0]
                age = t - te
                life = min(0.18 + 0.2 * seeds[j, 1], T_END - 0.03 - te)
                if life <= 0 or not (0 < age < life):
                    continue
                M, a = fn(te)
                sp = src_pts[int(seeds[j, 2] * len(src_pts)) % len(src_pts)]
                p = M @ np.array([sp[0] * S, sp[1] * S + (seeds[j, 3] - 0.5) * 40 * S, 1])
                M2, _ = fn(te + 0.012)
                p2 = M2 @ np.array([sp[0] * S, sp[1] * S + (seeds[j, 3] - 0.5) * 40 * S, 1])
                vx, vy = (p2 - p) / 0.012
                x = p[0] + vx * 0.15 * age + self.X0
                y = p[1] + vy * 0.15 * age + self.Y0 - 20 * age
                k = (1 - age / life) ** 1.5 * a * 0.9
                add_streak(E, x - vx * 0.012, y - vy * 0.012, x, y, 1.0, (col * 0.9 + WHITE * 0.2) * k, oy)

    # ------------------------------------------------------------------ frame
    def render(self, t, breathing=True):
        cv = Canvas(self.W, self.H, self.X0, self.Y0, self.roi)
        Hroi = cv.buf.shape[0]
        E_back = np.zeros((Hroi, self.W, 3), np.float32)
        E_front = np.zeros((Hroi, self.W, 3), np.float32)
        self._frame_flash = 0.0
        if not self.no_fx:
            self.fx(t, cv, E_back, E_front)
        # back emissive
        cv.over(glowify(E_back, 0.6))
        # band
        band, band_fg = self.band_image(t, cv)
        if band is not None:
            cv.over(band)
        if band_fg is not None:
            cv.over(glowify(band_fg, 0.8))
        # bell box group
        if T_BOX <= t < T_BOX_OUT[1]:
            boxL = self.box_group(t)
            def fbox(ts):
                M, a = self.box_tf(ts)
                return boxL, M, a
            img = motion_blurred(cv, fbox, t)
            if img is not None:
                cv.over(img)
        # panel group (emerges from behind the logo)
        if T_PANEL <= t < T_PANEL_OUT[1]:
            panL = self.panel_group(t)
            if getattr(self, '_frame_flash', 0) > 0:
                fr = self.A.frame_glow * self._frame_flash
                fr_l = np.zeros_like(panL.img)
                x0, y0, _, _ = self.A.pbox
                panL = Layer(emissive_add(panL.img, fr), panL.ox, panL.oy)
            def fpan(ts):
                M, a = self.panel_tf(ts)
                return panL, M, a
            img = motion_blurred(cv, fpan, t)
            if img is not None:
                # clip: only visible to the right of the logo centre while sliding
                if t < T_PANEL + 0.6 or t > T_PANEL_OUT[0]:
                    Ml, _ = self.logo_tf(t)
                    lcx = self.X0 + (Ml @ np.array([self.logo_c[0], self.logo_c[1], 1.0]))[0]
                    xx = np.arange(self.W, dtype=np.float32)
                    m = smoothstep(lcx - 10, lcx + 30, xx)
                    img = img * m[None, :, None]
                cv.over(img)
        # logo group
        if T_LOGO <= t < T_LOGO_OUT[1]:
            def flogo(ts):
                M, a = self.logo_tf(ts)
                return self.A.logo, M, a
            # halo while travelling (band supplies the halo once lit)
            hk = (1 - smooth(lin(t, T_BAND[0], T_BAND[0] + 0.35))) + smooth(lin(t, T_BAND_OUT[0], T_BAND_OUT[1]))
            hk = min(1.0, hk)
            if hk > 0:
                def fhalo(ts):
                    M, a = self.logo_tf(ts)
                    return self.A.logo_halo, M, a * hk
                img = motion_blurred(cv, fhalo, t)
                if img is not None:
                    cv.over(img)
            img = motion_blurred(cv, flogo, t)
            if img is not None:
                cv.over(img)
        # front emissive FX
        cv.over(glowify(E_front, 1.0))
        # shockwave refraction
        self.shock_distort(t, cv)
        # breathing neon during hold
        if breathing:
            bk = smooth(lin(t, T_HOLD[0] - 0.25, T_HOLD[0] + 0.1)) * (1 - smooth(lin(t, T_BAND_OUT[0] - 0.06, T_BAND_OUT[0] + 0.04)))
            if bk > 0:
                G = self.get_breath_glow()
                k = bk * (0.10 + 0.22 * self.breath(t))
                cv.buf = emissive_add(cv.buf, G * k)
        # cursor on top
        cs = None if self.no_fx else self.cursor_state(t)
        if cs is not None:
            self.draw_cursor(cv, t, cs)
        frame = np.zeros((self.H, self.W, 4), np.float32)
        # master tail: guarantees a perfectly empty canvas from T_END on
        master = 1 - smooth(lin(t, T_END - 0.10, T_END))
        if master > 0:
            frame[cv.ry0:cv.ry1] = cv.buf * master
        return np.clip(frame, 0, 1)

    def get_breath_glow(self):
        if self.breath_glow is None:
            f = self.render(T_HOLD[0] + 0.2, breathing=False)[self.roi[0]:self.roi[1]]
            st = unpremul(f)
            mx = st[..., :3].max(-1); mn = st[..., :3].min(-1)
            sat = (mx - mn) / np.maximum(mx, 1e-4)
            w = smoothstep(0.55, 0.95, mx) * smoothstep(0.2, 0.55, sat) * f[..., 3]
            e = f[..., :3] * w[..., None]
            self.breath_glow = cv2.GaussianBlur(e, (0, 0), 3) * 0.7 + cv2.GaussianBlur(e, (0, 0), 10) * 0.6
        return self.breath_glow

    def shock_distort(self, t, cv):
        u = t - T_CLICK2
        if not (0 < u < 0.5):
            return
        S = self.S
        v = u / 0.5
        R = (50 + 200 * ease_out_cubic(v)) * S / 0.45
        amp = 3.0 * (1 - v) ** 1.5
        c = self.canvas_xy(self.bell_c)
        x0 = int(max(0, c[0] - R - 30)); x1 = int(min(self.W, c[0] + R + 30))
        y0 = int(max(cv.ry0, c[1] - R - 30)); y1 = int(min(cv.ry1, c[1] + R + 30))
        yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        dx, dy = xx - c[0], (yy - c[1]) / 0.82
        d = np.hypot(dx, dy) + 1e-3
        disp = amp * np.exp(-((d - R) / (14 * S / 0.45)) ** 2)
        mapx = xx - disp * dx / d - x0
        mapy = yy - disp * dy * 0.82 / d - y0 - 0
        sub = cv.buf[y0 - cv.ry0:y1 - cv.ry0, x0:x1]
        warped = cv2.remap(sub, mapx.astype(np.float32), (mapy).astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        cv.buf[y0 - cv.ry0:y1 - cv.ry0, x0:x1] = warped

    def draw_cursor(self, cv, t, cs):
        (x, y), a, press = cs
        cur = self.A.cursor
        img = cur['img']
        tx, ty = cur['tip']
        # motion blur along path
        dt = 0.5 / FPS
        p0 = self.cursor_state(t - dt / 2)
        p1 = self.cursor_state(t + dt / 2)
        pts = [(x, y)]
        if p0 is not None and p1 is not None:
            d = math.hypot(p1[0][0] - p0[0][0], p1[0][1] - p0[0][1])
            n = int(min(12, max(1, math.ceil(d / 1.5))))
            if n > 1:
                pts = [(p0[0][0] + (p1[0][0] - p0[0][0]) * (i + 0.5) / n, p0[0][1] + (p1[0][1] - p0[0][1]) * (i + 0.5) / n) for i in range(n)]
        acc = np.zeros_like(cv.buf)
        h, w = img.shape[:2]
        for (px, py) in pts:
            M = np.array([[press, 0, px - tx * press], [0, press, py - cv.ry0 - ty * press]], np.float64)
            bx0 = int(max(0, px - tx - 2)); bx1 = int(min(self.W, px - tx + w + 2))
            by0 = int(max(0, py - cv.ry0 - ty - 2)); by1 = int(min(acc.shape[0], py - cv.ry0 - ty + h + 2))
            if bx1 <= bx0 or by1 <= by0:
                continue
            M2 = M.copy(); M2[0, 2] -= bx0; M2[1, 2] -= by0
            wimg = cv2.warpAffine(img, M2, (bx1 - bx0, by1 - by0), flags=cv2.INTER_CUBIC)
            acc[by0:by1, bx0:bx1] += np.clip(wimg, 0, None) / len(pts)
        acc *= a
        cv.over(acc)


def snap(off, scale_px):
    return 0.0 if abs(off) * scale_px < 0.02 else off


def emissive_add(prem, e):
    """Add light e (rgb) onto premultiplied image keeping it representable."""
    out = prem.copy()
    out[..., :3] += e
    a_need = out[..., :3].max(-1)
    out[..., 3] = np.maximum(out[..., 3], np.minimum(a_need, 1))
    out[..., :3] = np.minimum(out[..., :3], out[..., 3:4])
    return out


def glowify(E, k=1.0):
    if not np.any(E):
        return np.zeros(E.shape[:2] + (4,), np.float32)
    small = cv2.resize(E, (E.shape[1] // 2, E.shape[0] // 2), interpolation=cv2.INTER_AREA)
    g1 = cv2.GaussianBlur(E, (0, 0), 2.0)
    g2 = cv2.resize(cv2.GaussianBlur(small, (0, 0), 6.0), (E.shape[1], E.shape[0]), interpolation=cv2.INTER_LINEAR)
    tot = E + g1 * 0.9 * k + g2 * 0.9 * k
    return emissive_to_premul(tot)


# =====================================================================================
def worker(args):
    frames, outdir, layout = args
    r = Renderer(layout)
    for f in frames:
        t = f / FPS
        img = r.render(t)
        save_rgba(os.path.join(outdir, f'f_{f:04d}.png'), unpremul(img) * (img[..., 3:4] > 0), bits=16)
    return len(frames)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(HERE, 'frames'))
    ap.add_argument('--frames', default='')
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--layout', default='portrait')
    ap.add_argument('--still', type=float, default=None, help='render one clean still (no particles/cursor) at time t')
    ap.add_argument('--still_out', default='')
    a = ap.parse_args()
    if a.still is not None:
        r = Renderer(a.layout)
        r.no_fx = True
        img = r.render(a.still, breathing=False)
        if a.layout == 'still_hires':
            al = img[..., 3]
            ys, xs = np.where(al > 1 / 255)
            img = img[max(0, ys.min() - 8):ys.max() + 9, max(0, xs.min() - 8):xs.max() + 9]
        save_rgba(a.still_out, unpremul(img), bits=8)
        return
    os.makedirs(a.out, exist_ok=True)
    frames = [int(x) for x in a.frames.split(',')] if a.frames else list(range(NFRAMES))
    if a.jobs <= 1 or len(frames) <= 2:
        worker((frames, a.out, a.layout))
    else:
        from multiprocessing import Pool
        chunks = [frames[i::a.jobs] for i in range(a.jobs)]
        with Pool(a.jobs) as p:
            p.map(worker, [(c, a.out, a.layout) for c in chunks])


if __name__ == '__main__':
    main()
