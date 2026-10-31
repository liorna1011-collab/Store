"""QC on the *decoded* ProRes 4444 MOV (what an editor will actually see)."""
import sys, subprocess, numpy as np, cv2, json
from timeline import *
mov = sys.argv[1]; qcdir = sys.argv[2]
W, H = 1080, 1920
p = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', mov, '-vf', 'scale=in_color_matrix=bt709:in_range=tv:flags=accurate_rnd+full_chroma_int,format=rgba', '-f', 'rawvideo', '-'], stdout=subprocess.PIPE)
keys = [0, 6, 12, 18, 24, 30, 40, 54, 60, 85, 93, 96, 100, 106, 114, 140, 150, 153, 158, 166, 185, 210, 230, 238, 246, 252, 260, 268, 278, 290, 299]
stats = []
kept = {}
union = np.zeros((H, W), bool)
for f in range(NFRAMES):
    buf = p.stdout.read(W * H * 4)
    if len(buf) < W * H * 4:
        print('SHORT READ at frame', f); break
    a = np.frombuffer(buf, np.uint8).reshape(H, W, 4)
    al = a[..., 3]
    nz = al > 0
    union |= al > 8
    if nz.any():
        ys, xs = np.where(nz)
        bb = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
    else:
        bb = None
    # opaque near-black area (possible "black box") : alpha>0.9 and luma<10
    dark_opaque = int(((al > 230) & (a[..., :3].max(-1) < 10)).sum())
    stats.append(dict(frame=f, t=round(f / FPS, 3), max_alpha=int(al.max()), nonzero=int(nz.sum()), bbox=bb, dark_opaque=dark_opaque))
    if f in keys:
        kept[f] = a.copy()
p.wait()
json.dump(stats, open(f'{qcdir}/frame_stats.json', 'w'), indent=0)
print('frames decoded:', len(stats))
print('frame 0   max alpha:', stats[0]['max_alpha'], ' nonzero px:', stats[0]['nonzero'])
print('frame 299 max alpha:', stats[-1]['max_alpha'], ' nonzero px:', stats[-1]['nonzero'])
empty = [s['frame'] for s in stats if s['max_alpha'] == 0]
print('fully transparent frames:', empty[:5], '...', empty[-8:])
ys, xs = np.where(union)
print('union of visible content (alpha>8): x', xs.min(), '-', xs.max(), ' y', ys.min(), '-', ys.max())
print('max opaque near-black px in any frame:', max(s['dark_opaque'] for s in stats))
# contact sheets over bright & dark
def sheet(bg, name, crop=(0, 860, 1080, 1420), sc=0.5, cols=5):
    tiles = []
    for f in keys:
        a = kept[f].astype(np.float32) / 255
        x0, y0, x1, y1 = crop
        a = a[y0:y1, x0:x1]
        c = a[..., :3] * a[..., 3:4] + np.array(bg, np.float32) * (1 - a[..., 3:4])
        c = (np.clip(c, 0, 1) * 255).astype(np.uint8)[..., ::-1].copy()
        c = cv2.resize(c, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA)
        col = (0, 0, 0) if sum(bg) > 1.5 else (0, 255, 255)
        cv2.putText(c, f'#{f} {f/FPS:.2f}s', (5, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv2.LINE_AA)
        tiles.append(c)
    while len(tiles) % cols:
        tiles.append(np.full_like(tiles[0], 128))
    rows = [np.concatenate(tiles[i:i + cols], 1) for i in range(0, len(tiles), cols)]
    cv2.imwrite(f'{qcdir}/{name}', np.concatenate(rows, 0))
sheet((0.06, 0.06, 0.08), 'qc_sheet_dark_bg.png')
sheet((0.96, 0.96, 0.97), 'qc_sheet_bright_bg.png')
# safe-area guide: full frame at hold over mid-gray with generic Shorts UI zones
a = kept[210].astype(np.float32) / 255
bg = np.full((H, W, 3), 0.35, np.float32)
c = a[..., :3] * a[..., 3:4] + bg * (1 - a[..., 3:4])
c = (np.clip(c, 0, 1) * 255).astype(np.uint8)[..., ::-1].copy()
ov = c.copy()
zones = [((0, 0, 1080, 260), 'top: search / menu'), ((960, 900, 1080, 1700), 'right: action buttons'), ((0, 1560, 1080, 1920), 'bottom: title / channel / audio')]
for (x0, y0, x1, y1), lab in zones:
    cv2.rectangle(ov, (x0, y0), (x1, y1), (60, 60, 230), -1)
c = cv2.addWeighted(ov, 0.35, c, 0.65, 0)
for (x0, y0, x1, y1), lab in zones:
    cv2.rectangle(c, (x0, y0), (x1, y1), (60, 60, 255), 2)
    cv2.putText(c, lab, (x0 + 10, y0 + 34) if x0 == 0 else (x0 - 330, y0 + 34), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2, cv2.LINE_AA)
x0, y0, x1, y1 = xs.min(), ys.min(), xs.max(), ys.max()
cv2.rectangle(c, (int(x0), int(y0)), (int(x1), int(y1)), (0, 255, 255), 2)
cv2.putText(c, 'overlay extent (all frames)', (int(x0), int(y0) - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2, cv2.LINE_AA)
cv2.imwrite(f'{qcdir}/qc_safe_area.png', cv2.resize(c, (540, 960), interpolation=cv2.INTER_AREA))
# 200% zoom of headline right edge + bell junction, from decoded frames
tiles = []
for f in [60 if 60 in kept else 54, 96, 114, 158, 210, 246]:
    if f not in kept: continue
    a = kept[f].astype(np.float32) / 255
    for bg in [(0.06, 0.06, 0.08), (0.96, 0.96, 0.97)]:
        cc = a[..., :3] * a[..., 3:4] + np.array(bg, np.float32) * (1 - a[..., 3:4])
        cc = cc[1095:1225, 740:960]
        cc = cv2.resize((np.clip(cc, 0, 1) * 255).astype(np.uint8)[..., ::-1], None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
        cv2.putText(cc, f'#{f} 200%', (5, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 255), 1, cv2.LINE_AA)
        tiles.append(cc)
rows = [np.concatenate(tiles[i:i + 4], 1) for i in range(0, len(tiles) - len(tiles) % 4, 4)]
cv2.imwrite(f'{qcdir}/qc_zoom200_headline_bell.png', np.concatenate(rows, 0))
