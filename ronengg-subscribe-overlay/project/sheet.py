import sys, os, glob, numpy as np, cv2
from common import load_rgba
d = sys.argv[1]; out = sys.argv[2]; bg = sys.argv[3] if len(sys.argv) > 3 else 'dark'
crop = tuple(int(v) for v in sys.argv[4].split(',')) if len(sys.argv) > 4 else (0, 860, 1080, 1420)
cols = int(sys.argv[5]) if len(sys.argv) > 5 else 3
scale = float(sys.argv[6]) if len(sys.argv) > 6 else 0.5
files = sorted(glob.glob(os.path.join(d, 'f_*.png')))
tiles = []
for f in files:
    im = load_rgba(f)
    x0, y0, x1, y1 = crop
    im = im[y0:y1, x0:x1]
    if bg == 'dark':
        b = np.ones_like(im[..., :3]) * np.array([0.07, 0.07, 0.09])
    elif bg == 'light':
        b = np.ones_like(im[..., :3]) * np.array([0.93, 0.93, 0.95])
    else:
        yy, xx = np.mgrid[0:im.shape[0], 0:im.shape[1]]
        c = (((xx // 16) + (yy // 16)) % 2).astype(np.float32)
        b = (0.55 + 0.25 * c)[..., None] * np.ones(3)
    c = im[..., :3] * im[..., 3:4] + b * (1 - im[..., 3:4])
    c = (np.clip(c, 0, 1) * 255).astype(np.uint8)[..., ::-1].copy()
    c = cv2.resize(c, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    cv2.putText(c, os.path.basename(f)[2:6] + f' {int(os.path.basename(f)[2:6])/60:.2f}s', (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
    tiles.append(c)
while len(tiles) % cols: tiles.append(np.zeros_like(tiles[0]))
rows = [np.concatenate(tiles[i:i + cols], 1) for i in range(0, len(tiles), cols)]
cv2.imwrite(out, np.concatenate(rows, 0))
