# Compare a decoded MOV against the rendered PNG frames (PSNR).  usage: python3 cmpq.py <mov> 60,120,200
import sys, subprocess, numpy as np, os, math
from common import load_rgba
mov = sys.argv[1]; frames = [int(x) for x in sys.argv[2].split(',')]
import tempfile; tmp = tempfile.mkdtemp()
res = []
for f in frames:
    out = f'{tmp}/d_{f:04d}.png'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', mov, '-vf', f'select=eq(n\\,{f}),scale=in_color_matrix=bt709:in_range=tv:flags=accurate_rnd+full_chroma_int,format=rgba64le', '-frames:v', '1', out], check=True)
    a = load_rgba(out); b = load_rgba(f'build/frames_portrait/f_{f:04d}.png')
    ca = a[..., :3] * a[..., 3:4] + 0.5 * (1 - a[..., 3:4]); cb = b[..., :3] * b[..., 3:4] + 0.5 * (1 - b[..., 3:4])
    roi = slice(700, 1600)
    mse = ((ca[roi] - cb[roi]) ** 2).mean()
    psnr = 10 * math.log10(1 / mse)
    res.append((f, round(psnr, 2), round(float(np.abs(a[..., 3] - b[..., 3]).max()), 4), round(float(np.abs(ca - cb).max()), 4)))
print(os.path.basename(mov), res)
