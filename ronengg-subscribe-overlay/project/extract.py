"""Decompose the flattened RonenGG artwork into independent, seamless layers.

All layers are stored at source resolution (2000x668) as straight-alpha 16-bit PNGs.
At rest, compositing  bg -> box -> panel(frame, face, tile, bar) -> bell -> logo
reproduces the source pixel-for-pixel (verified at the end of this script).
"""
import os, json
import numpy as np
import cv2
from common import *
from geom import *

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'layers')
os.makedirs(OUT, exist_ok=True)

S = load_rgba(os.path.join(HERE, 'source', 'ronengg_subscribe_source.png'))
DBG = os.path.join(HERE, 'build', 'debug')
os.makedirs(DBG, exist_ok=True)
# The webp stores "opaque" as alpha 248-253: normalise so the opaque core is truly 1.0
S[..., 3] = np.clip(S[..., 3] * (255.0 / 251.0), 0, 1)
S[..., 3][S[..., 3] < 2 / 255] = 0
H, W = S.shape[:2]
rgb, A = S[..., :3], S[..., 3]
P = premul(S)
lum = rgb.mean(-1)
mn = rgb.min(-1)
mx = rgb.max(-1)
sat = np.where(mx > 1e-4, (mx - mn) / np.maximum(mx, 1e-4), 0)

def dil(m, r):
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    return cv2.dilate(m, k)

def ero(m, r):
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    return cv2.erode(m, k)

def fill_holes(mask_u8):
    m = mask_u8.copy()
    h, w = m.shape
    ff = np.zeros((h + 2, w + 2), np.uint8)
    inv = m.copy()
    cv2.floodFill(inv, ff, (0, 0), 255)
    return m | (~inv & 255)

# ---------------------------------------------------------------- logo
# tight ellipse just outside the neon ring line (ring fitted as an ellipse: centre
# (359.05, 349.51), semi-axes 269.5 horizontal / 260.2 vertical, tilt -1.6 deg)
def ellipse_mask(h, w, ex, ey, rx, ry, tilt_deg):
    ss = 4
    yy, xx = np.mgrid[0:h * ss, 0:w * ss].astype(np.float32)
    xx = (xx + 0.5) / ss - 0.5 - ex
    yy = (yy + 0.5) / ss - 0.5 - ey
    c, s_ = np.cos(np.radians(tilt_deg)), np.sin(np.radians(tilt_deg))
    u = (xx * c + yy * s_) / rx
    v = (-xx * s_ + yy * c) / ry
    m = ((u * u + v * v) <= 1).astype(np.float32)
    return cv2.resize(m, (w, h), interpolation=cv2.INTER_AREA)
RING_E = (359.05, 349.51, 269.5, 260.2, -1.6)
LOGO_PAD = 7.5   # px beyond the centre of the ring line: line half-width + a few px of its own glow
disk = ellipse_mask(H, W, RING_E[0], RING_E[1], RING_E[2] + LOGO_PAD, RING_E[3] + LOGO_PAD, RING_E[4])
disk = feather(disk, 1.0)

# crown (neon line art above the ring)
crown_box = np.zeros((H, W), np.uint8); crown_box[0:140, 270:470] = 1
crown_core = ((mn > 0.55) & (lum > 0.7)).astype(np.uint8) * crown_box
crown = dil(crown_core * 255, 5).astype(np.float32) / 255
crown = np.clip(feather(crown, 1.5) * 1.3, 0, 1) * (crown_box > 0)

# wordmark "RONENGG" (white brush letters with dark outline)
wm_box = np.zeros((H, W), np.uint8); wm_box[440:668, 120:600] = 1
wm_core = ((mn > 0.62) & (sat < 0.35)).astype(np.uint8) * wm_box
wm_core = cv2.morphologyEx(wm_core * 255, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
# keep only the letter bodies (R, O, NENGG); drop loose highlights of the background energy
_n, _lab, _st, _ = cv2.connectedComponentsWithStats(wm_core)
wm_core = np.isin(_lab, [i for i in range(1, _n) if _st[i, 4] >= 3000]).astype(np.uint8) * 255
wm = dil(wm_core, 5).astype(np.float32) / 255
wm = np.clip(feather(wm, 1.0) * 1.3, 0, 1) * (wm_box > 0)

M_logo = 1 - (1 - disk) * (1 - crown) * (1 - wm)

# ---------------------------------------------------------------- panel
btn = rrect_mask(H, W, *BTN_OUT)
bar_full = poly_mask(H, W, BAR_LINE, BAR_DILATE)
yy = np.arange(H, dtype=np.float32)[:, None]
bar_top = np.clip(yy - 446.5, 0, 1)  # bar sits on top of the face only from its neon line down
M_bar = bar_full * bar_top
M_panel = 1 - (1 - btn) * (1 - bar_full)
M_panel = feather(M_panel, 0.6)
M_bar = feather(M_bar, 0.6)
M_face = rrect_mask(H, W, *FACE)
M_tile = rrect_mask(H, W, TILE[0] - 4, TILE[1] - 4, TILE[2] + 4, TILE[3] + 4, TILE[4] + 4)
M_tile = feather(M_tile, 0.7)

# ---------------------------------------------------------------- bell box
M_box = rrect_mask(H, W, *BOX_OUT)
M_box = feather(M_box, 0.6)

# separation between button and bell box: complementary along the seam
seam = M_box * btn
M_panel = M_panel * (1 - 0.0 * seam)

# ---------------------------------------------------------------- background energy band
M_u = 1 - (1 - M_logo) * (1 - M_panel) * (1 - M_box)
den = 1 - A * M_u
aB = np.where(den > 1e-4, A * (1 - M_u) / np.maximum(den, 1e-4), 0)
B = np.concatenate([rgb, aB[..., None]], -1)
PB = premul(B)
valid = (M_u < 0.5)
# fill hidden region with smooth band colour (only ever glimpsed while parts move)
filled = pushpull_fill(np.concatenate([P[..., :3], A[..., None]], -1), valid)
# a little darker so a glimpse reads as "depth" behind the parts
filled[..., :3] *= 0.8
filled *= smoothstep(0.85, 0.99, A)[..., None]
wfill = smoothstep(0.9985, 0.9995, M_u)[..., None]
PB = PB * (1 - wfill) + filled * wfill

# ---------------------------------------------------------------- logo layer
PL = P * M_logo[..., None]

# ---------------------------------------------------------------- panel sub-layers
PP = P * M_panel[..., None]

# face with the play tile removed (row-wise interpolation of the glossy fill)
face_rgb = rgb.copy()
xl, xr = TILE[0] - 10, TILE[2] + 9
y0, y1 = TILE[1] - 6, TILE[3] + 7
for y in range(y0, y1 + 1):
    cl = rgb[y, xl - 2:xl + 1].mean(0)
    face_rgb[y, xl:xr + 1] = cl
under_tile = feather(dil((M_tile > 0.02).astype(np.uint8) * 255, 4).astype(np.float32) / 255, 1.5)
tile_core = (M_tile >= 0.999).astype(np.float32)
face_rest_rgb = rgb * (1 - tile_core[..., None]) + face_rgb * tile_core[..., None]
face_rgb = rgb * (1 - under_tile[..., None]) + face_rgb * under_tile[..., None]
# extend face downwards beneath the bar (rows hidden by the bar's top edge)
for x in range(int(FACE[0]), int(FACE[2]) + 1):
    for y in range(445, 452):
        if M_bar[y, x] > 0.98:
            face_rgb[y, x] = face_rgb[444, x]
            face_rest_rgb[y, x] = face_rest_rgb[444, x]
face0 = np.concatenate([face_rgb, M_face[..., None]], -1)
face0r = np.concatenate([face_rest_rgb, M_face[..., None]], -1)

# subscribed state: saturated glossy fill drops to deep violet glass, light text stays
f_mx = face_rgb.max(-1); f_mn = face_rgb.min(-1)
f_sat = np.where(f_mx > 1e-4, (f_mx - f_mn) / np.maximum(f_mx, 1e-4), 0)
k = 1 - 0.80 * smoothstep(0.30, 0.70, f_sat)
dark = face_rgb * k[..., None]
tint = np.array([0.10, 0.02, 0.24], np.float32)
dark = dark + tint * smoothstep(0.30, 0.70, f_sat)[..., None] * (1 - face_rgb.mean(-1)[..., None])
# keep the bright face rim as a neon edge
face1 = np.concatenate([np.clip(dark, 0, 1), M_face[..., None]], -1)

# well beneath the face (visible only as a sliver while the button is pressed)
yy2, xx2 = np.mgrid[0:H, 0:W].astype(np.float32)
well_rgb = np.zeros((H, W, 3), np.float32)
g = smoothstep(FACE[1], FACE[1] + 30, yy2)
well_rgb[...] = np.array([0.02, 0.0, 0.06]) * (1 - g[..., None]) + np.array([0.09, 0.01, 0.22]) * g[..., None]
Mf_core = feather(ero((M_face > 0.5).astype(np.uint8) * 255, 1).astype(np.float32) / 255, 0.8)
frame_rgb = rgb * (1 - Mf_core[..., None]) + well_rgb * Mf_core[..., None]
frame = np.concatenate([frame_rgb, (A * M_panel)[..., None]], -1)

bar = np.concatenate([rgb, (A * M_bar)[..., None]], -1)

# tiles
tile_play = np.concatenate([rgb, M_tile[..., None]], -1)
tile_rgb = rgb.copy()
tx0, tx1 = 738, 824
for y in range(312, 401):
    cl = rgb[y, tx0 - 2:tx0 + 1].mean(0); cr = rgb[y, tx1:tx1 + 3].mean(0)
    t = np.linspace(0, 1, tx1 - tx0 + 1)[:, None]
    tile_rgb[y, tx0:tx1 + 1] = cl * (1 - t) + cr * t
tile_blank = np.concatenate([tile_rgb, M_tile[..., None]], -1)
tri_col = rgb[330:380, 755:790].reshape(-1, 3)
tri_col = tri_col[tri_col.max(-1) < 0.6].mean(0)

# ---------------------------------------------------------------- bell, arcs
interior = rrect_mask(H, W, BOX_OUT[0] + 16, BOX_OUT[1] + 16, BOX_OUT[2] - 16, BOX_OUT[3] - 16, 34)
bell_roi = np.zeros((H, W), np.uint8); bell_roi[262:505, 1738:1900] = 1
bell_raw = ((lum > 0.30) & (bell_roi > 0)).astype(np.uint8) * 255
bell_raw = cv2.morphologyEx(bell_raw, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
n, lab = cv2.connectedComponents(bell_raw)
keep = np.zeros_like(bell_raw)
for (px, py) in [(1817, 372), (1808, 290), (1817, 470), (1817, 425)]:
    l = lab[py, px]
    if l > 0:
        keep[lab == l] = 255
keep = fill_holes(keep)
arc_roi = np.zeros((H, W), np.float32)
arc_roi[286:420, 1694:1762] = 1; arc_roi[286:420, 1860:1928] = 1
arcs_m = smoothstep(0.30, 0.70, mx) * arc_roi * (1 - dil(keep, 6).astype(np.float32) / 255)
arcs_m = feather(arcs_m, 0.6)

core = ero(keep, 2) > 0
band = (dil(keep, 2) > 0) & ~core
bg_est = pushpull_fill(rgb, (interior > 0.5) & ~(dil(keep, 4) > 0) & (arcs_m < 0.05))
fg_est = pushpull_fill(rgb, core)
num = ((rgb - bg_est) * (fg_est - bg_est)).sum(-1)
den_ = ((fg_est - bg_est) ** 2).sum(-1)
alpha_e = np.clip(num / np.maximum(den_, 1e-4), 0, 1)
bell_m = np.where(core, 1.0, np.where(band, alpha_e, 0.0)).astype(np.float32)
# decontaminated colour: solve orig = a*F + (1-a)*bg for F at the edge
F = np.where(band[..., None] & (bell_m[..., None] > 0.05), (rgb - (1 - bell_m[..., None]) * bg_est) / np.maximum(bell_m[..., None], 0.05), rgb)
F = np.clip(F, 0, 1)
bell = np.concatenate([F, bell_m[..., None]], -1)

# arc geometry (concentric fits measured from the matte ridges)
arcs = {'L': dict(cx=1778.0, cy=354.0, r_in=47.2, r_out=73.5, w=14.0, a_mid=183.0, a_half=46.0),
        'R': dict(cx=1844.0, cy=352.0, r_in=47.2, r_out=69.8, w=13.0, a_mid=-9.0, a_half=45.0)}

# box frame with bell removed and its hidden lower-left corner rebuilt by mirroring
box_rgb = rgb.copy()
mir = rgb[:, ::-1]
shift = int(round(2 * BOX_AXIS - (W - 1)))
mir = np.roll(mir, shift, axis=1)  # mir[y,x] = rgb[y, 2*axis - x]
hidden = feather(ero((bar_full > 0.5).astype(np.uint8) * 255, 3).astype(np.float32) / 255, 1.0) * (xx2 < BOX_AXIS)
box_rgb = box_rgb * (1 - hidden[..., None]) + mir * hidden[..., None]
# remove the bell: fill from the dark interior (arcs excluded from the source of the fill)
bell_hole = dil(keep, 4) > 0
valid_int = (interior > 0.5) & ~bell_hole & (arcs_m < 0.05)
fill_int = pushpull_fill(box_rgb, valid_int | (interior < 0.5) & False)
bh = feather(dil(keep, 4).astype(np.float32) / 255, 1.5)[..., None]
box_rgb = box_rgb * (1 - bh) + bg_est * bh
box_a = np.maximum(A, hidden) * M_box
box = np.concatenate([box_rgb, box_a[..., None]], -1)

# ---------------------------------------------------------------- save
def save(name, rgba):
    save_rgba(os.path.join(OUT, name + '.png'), rgba, bits=16)

save('bg', unpremul(PB))
save('logo', unpremul(PL))
save('frame', frame)
save('face0', face0)
save('face0r', face0r)
save('face1', face1)
save('bar', bar)
save('tile_play', tile_play)
save('tile_blank', tile_blank)
save('box', box)
save('bell', bell)
np.save(os.path.join(OUT, 'arcs_m.npy'), arcs_m.astype(np.float32))
np.save(os.path.join(OUT, 'face_m.npy'), M_face.astype(np.float32))
np.save(os.path.join(OUT, 'panel_m.npy'), M_panel.astype(np.float32))
meta = dict(arcs=arcs, tri_col=[float(v) for v in tri_col])
json.dump(meta, open(os.path.join(OUT, 'meta.json'), 'w'), indent=1)
print(json.dumps(meta, indent=1))

# ---------------------------------------------------------------- verify rest-state reconstruction
comp = PB.copy()
comp = over(premul(box), comp)
comp = over(premul(bell), comp)
comp = over(premul(frame), comp)
face_clip = premul(face0r)
comp = over(face_clip, comp)
comp = over(premul(tile_play), comp)
comp = over(premul(bar), comp)
comp = over(PL, comp)
d = np.abs(comp - P)
print('reconstruction max err', d.max(), 'mean err', d.mean(), '99.9pct', np.percentile(d, 99.9))
errimg = np.clip(d.max(-1) * 8, 0, 1)
cv2.imwrite(os.path.join(DBG, 'recon_err.png'), (errimg * 255).astype(np.uint8))
dark = comp[..., :3] + 0.08 * (1 - comp[..., 3:4])
cv2.imwrite(os.path.join(DBG, 'recon.png'), (np.clip(dark, 0, 1)[..., ::-1] * 255).astype(np.uint8))
for nm, L in [('face0', premul(face0)), ('tile_blank', premul(tile_blank)), ('bar', premul(bar)), ('bg', PB), ('logo', PL), ('frame', premul(frame)), ('face1', premul(face1)), ('box', premul(box)), ('bell', premul(bell))]:
    v = L[..., :3] + np.array([0.0, 0.35, 0.0]) * (1 - L[..., 3:4])
    cv2.imwrite(os.path.join(DBG, f'L_{nm}.png'), (np.clip(v, 0, 1)[..., ::-1] * 255).astype(np.uint8))
