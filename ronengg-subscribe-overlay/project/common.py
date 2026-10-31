"""Shared helpers: premultiplied-alpha float images, anti-aliased shape masks."""
import numpy as np
import cv2

SS = 4  # supersampling factor for vector masks


def load_rgba(path):
    im = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if im.dtype == np.uint16:
        im = im.astype(np.float32) / 65535.0
    else:
        im = im.astype(np.float32) / 255.0
    return im[..., [2, 1, 0, 3]].copy()  # BGRA -> RGBA


def save_rgba(path, rgba, bits=8):
    """Save straight-alpha RGBA float image."""
    x = np.clip(rgba, 0, 1)[..., [2, 1, 0, 3]]
    if bits == 16:
        cv2.imwrite(path, (x * 65535 + 0.5).astype(np.uint16))
    else:
        cv2.imwrite(path, (x * 255 + 0.5).astype(np.uint8))


def premul(rgba):
    out = rgba.copy()
    out[..., :3] *= out[..., 3:4]
    return out


def unpremul(p):
    out = p.copy()
    a = out[..., 3:4]
    out[..., :3] = np.where(a > 1e-6, out[..., :3] / np.maximum(a, 1e-6), 0)
    return np.clip(out, 0, 1)


def over(top, bottom):
    """Porter-Duff over, premultiplied."""
    return top + bottom * (1.0 - top[..., 3:4])


def _ss_canvas(h, w):
    return np.zeros((h * SS, w * SS), np.uint8)


def _down(m, h, w):
    return cv2.resize(m.astype(np.float32) / 255.0, (w, h), interpolation=cv2.INTER_AREA)


def poly_mask(h, w, pts, dilate=0.0):
    c = _ss_canvas(h, w)
    p = np.round((np.asarray(pts, np.float64) + 0.5) * SS - 0.5).astype(np.int32)
    cv2.fillPoly(c, [p], 255, lineType=cv2.LINE_8)
    if dilate > 0:
        r = int(round(dilate * SS))
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
        c = cv2.dilate(c, k)
    return _down(c, h, w)


def rrect_mask(h, w, x0, y0, x1, y1, r):
    """Anti-aliased rounded rectangle (inclusive-ish float coords)."""
    yy, xx = np.mgrid[0:h * SS, 0:w * SS].astype(np.float32)
    xx = (xx + 0.5) / SS - 0.5
    yy = (yy + 0.5) / SS - 0.5
    cx = np.clip(xx, x0 + r, x1 - r)
    cy = np.clip(yy, y0 + r, y1 - r)
    d = np.hypot(xx - cx, yy - cy)
    m = (d <= r).astype(np.uint8) * 255
    return _down(m, h, w)


def rrect_sdf(h, w, x0, y0, x1, y1, r):
    """Signed distance (negative inside) to rounded rect."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cxm, cym = (x0 + x1) / 2, (y0 + y1) / 2
    hx, hy = (x1 - x0) / 2 - r, (y1 - y0) / 2 - r
    qx = np.abs(xx - cxm) - hx
    qy = np.abs(yy - cym) - hy
    outside = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0))
    inside = np.minimum(np.maximum(qx, qy), 0)
    return outside + inside - r


def circle_mask(h, w, cx, cy, rfun):
    """rfun(theta) -> radius, theta in radians (atan2(dy,dx))."""
    yy, xx = np.mgrid[0:h * SS, 0:w * SS].astype(np.float32)
    xx = (xx + 0.5) / SS - 0.5 - cx
    yy = (yy + 0.5) / SS - 0.5 - cy
    th = np.arctan2(yy, xx)
    m = (np.hypot(xx, yy) <= rfun(th)).astype(np.uint8) * 255
    return _down(m, h, w)


def feather(m, sigma):
    if sigma <= 0:
        return m
    return cv2.GaussianBlur(m, (0, 0), sigma)


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def pushpull_fill(rgb, valid, levels=9):
    """Fill invalid pixels by pyramid push-pull diffusion (smooth, seamless)."""
    w = valid.astype(np.float32)
    img = rgb * w[..., None]
    pyr = [(img, w)]
    for _ in range(levels):
        img, w = pyr[-1]
        if min(img.shape[:2]) < 4:
            break
        i2 = cv2.pyrDown(img)
        w2 = cv2.pyrDown(w)
        pyr.append((i2, w2))
    # pull up
    img, w = pyr[-1]
    filled = img / np.maximum(w[..., None], 1e-6)
    for img, w in reversed(pyr[:-1]):
        up = cv2.pyrUp(filled, dstsize=(img.shape[1], img.shape[0]))
        ww = np.clip(w, 0, 1)[..., None]
        cur = img / np.maximum(w[..., None], 1e-6)
        filled = np.where(w[..., None] > 1e-3, cur * ww + up * (1 - ww), up)
    out = rgb.copy()
    out[~valid] = filled[~valid]
    return out
