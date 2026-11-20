"""IR-compatible dark/bright pupil detection from eye ROI, with glint diagnostics."""
from __future__ import annotations
from dataclasses import dataclass
import cv2
import numpy as np

@dataclass
class Pupil:
    xy: tuple[float, float]
    radius_px: float
    confidence: float
    ellipse_axes: tuple[float, float]
    glint_xy: tuple[float, float] | None = None


def preprocess_ir(image: np.ndarray, clip_limit: float = 2.0) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    if gray.dtype != np.uint8:
        raise ValueError("preprocess_ir expects uint8")
    return cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(8, 8)).apply(gray)


def detect_pupil(image: np.ndarray, roi: tuple[int, int, int, int],
                 polarity: str = "dark", min_radius: float = 3.0,
                 max_radius: float = 40.0) -> Pupil | None:
    """Detect near-round contrast pupil candidates under uneven grayscale lighting.

    Glints are diagnostic only; this is not a validated pupil–corneal-reflection model.
    """
    if polarity not in ("dark", "bright"):
        raise ValueError("polarity must be 'dark' or 'bright'")
    if not np.isfinite([min_radius, max_radius]).all() or min_radius <= 0 or max_radius <= min_radius:
        raise ValueError("pupil radius limits must be finite, positive and ordered")
    if image is None or image.dtype != np.uint8 or image.ndim not in (2, 3):
        raise ValueError("pupil detection expects uint8 grayscale or BGR image")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    height, width = gray.shape[:2]
    x, y, w, h = (int(v) for v in roi)
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(width, x+w), min(height, y+h)
    crop_w, crop_h = x1-x0, y1-y0
    if crop_w < 12 or crop_h < 12:
        return None
    cropped = gray[y0:y1, x0:x1]
    if float(np.percentile(cropped, 95)-np.percentile(cropped, 5)) < 8:
        return None
    filtered = cv2.GaussianBlur(cropped, (3, 3), 0)
    flag = cv2.THRESH_BINARY_INV if polarity == "dark" else cv2.THRESH_BINARY
    _, otsu = cv2.threshold(filtered, 0, 255, flag | cv2.THRESH_OTSU)
    masks = [otsu]
    for pct in ((12, 22) if polarity == "dark" else (88, 78)):
        _, mask = cv2.threshold(filtered, float(np.percentile(filtered, pct)), 255, flag)
        masks.append(mask)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    center_x, center_y = crop_w/2, crop_h/2
    yy, xx = np.ogrid[:crop_h, :crop_w]
    candidates = []
    for raw in masks:
        mask = cv2.morphologyEx(raw, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            area = cv2.contourArea(c)
            if not np.pi*min_radius**2*.45 <= area <= np.pi*max_radius**2*1.8:
                continue
            bx, by, bw, bh = cv2.boundingRect(c)
            if bx <= 0 or by <= 0 or bx+bw >= crop_w-1 or by+bh >= crop_h-1 or len(c) < 5:
                continue
            try:
                (cx, cy), axes, _ = cv2.fitEllipse(c)
            except cv2.error:
                continue
            if not np.isfinite([cx, cy, axes[0], axes[1]]).all():
                continue
            major, minor = max(axes), min(axes)
            if major <= 1 or minor/major < .4:
                continue
            radius = (major+minor)/4
            if not min_radius <= radius <= max_radius:
                continue
            dist2 = (xx-cx)**2+(yy-cy)**2
            core = cropped[dist2 < max(1.5, radius*.65)**2]
            ring = cropped[(dist2 > (radius*1.2)**2) & (dist2 < (radius*1.7)**2)]
            if not len(core) or not len(ring):
                continue
            contrast = float(np.median(ring)-np.median(core))
            if polarity == "bright":
                contrast *= -1
            if contrast < 8:
                continue
            circularity = 4*np.pi*area/max(1., cv2.arcLength(c, True))**2
            relative_center = np.hypot((cx-center_x)/max(1,crop_w), (cy-center_y)/max(1,crop_h))
            center_weight = max(.25, 1-.5*relative_center)
            score = float(np.clip(np.clip(circularity, 0, 1)*np.sqrt(minor/major) *
                                  center_weight*min(1., contrast/35.), 0, 1))
            if score >= .15:
                candidates.append((score, cx, cy, radius, (float(axes[0]),float(axes[1]))))
    if not candidates:
        return None
    score, cx, cy, radius, axes = max(candidates)
    glare = (cropped >= max(np.percentile(cropped, 99.7), 245)).astype(np.uint8)
    n, _, stats, centers = cv2.connectedComponentsWithStats(glare, 8)
    glints = []
    for i in range(1,n):
        if 1 <= stats[i,cv2.CC_STAT_AREA] <= 30:
            gx, gy = centers[i]
            dist = np.hypot(gx-cx, gy-cy)
            if dist <= max(15,radius*2):
                glints.append((dist,gx,gy))
    glint = None
    if glints:
        _, gx, gy = min(glints)
        glint = (float(x0+gx), float(y0+gy))
    return Pupil((float(x0+cx),float(y0+cy)),float(radius),score,axes,glint)


def ratio_xy(point: tuple[float, float], roi: tuple[int, int, int, int]) -> tuple[float, float]:
    x,y,w,h = roi
    return ((point[0] - x)/w, (point[1] - y)/h)
