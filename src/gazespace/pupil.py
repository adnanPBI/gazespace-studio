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
    """Detect pupil by thresholded near-round contour; emits no detection on failure.

    roi is x,y,width,height in image coordinates. Not a biometric guarantee.
    Can process grayscale active-IR (dark or bright pupils) or RGB inputs.
    """
    if polarity not in ("dark", "bright"):
        raise ValueError("polarity must be 'dark' or 'bright'")
    if not np.isfinite([min_radius, max_radius]).all() or min_radius <= 0 or max_radius <= min_radius:
        raise ValueError("pupil radius limits must be finite, positive and ordered")
    if image is None or image.dtype != np.uint8 or image.ndim not in (2, 3):
        raise ValueError("pupil detection expects uint8 grayscale or BGR image")
    if image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image
    height, width = gray.shape[:2]
    x, y, w, h = (int(v) for v in roi)
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(width, x + w), min(height, y + h)
    if x1-x0 < 12 or y1-y0 < 12:
        return None
    cropped = gray[y0:y1, x0:x1]
    if float(np.percentile(cropped, 95) - np.percentile(cropped, 5)) < 8:
        return None
    filtered = cv2.GaussianBlur(cropped, (5, 5), 0)
    flag = cv2.THRESH_BINARY_INV if polarity == "dark" else cv2.THRESH_BINARY
    _, mask = cv2.threshold(filtered, 0, 255, flag | cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    # Ignore border-connected dark shadows and lens edge artifacts.
    # Pupils can be nested inside a bright sclera surrounded by dark eyelids.
    # RETR_EXTERNAL drops that inner component; contour/contrast checks below
    # select real candidates without assuming the pupil is an outer component.
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    center_x, center_y = (x1 - x0) / 2, (y1 - y0) / 2
    candidates = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < np.pi * min_radius**2 * 0.45 or area > np.pi * max_radius**2 * 1.8:
            continue
        bx, by, bw, bh = cv2.boundingRect(c)
        if bx <= 0 or by <= 0 or bx + bw >= x1 - x0 - 1 or by + bh >= y1 - y0 - 1:
            continue
        if len(c) < 5:
            continue
        (cx, cy), axes, _ = cv2.fitEllipse(c)
        major, minor = max(axes), min(axes)
        if major <= 1 or minor / major < 0.40:
            continue
        radius = (major + minor) / 4
        if not (min_radius <= radius <= max_radius):
            continue
        # A near-round contour is insufficient: require optical contrast against
        # its immediate surroundings (also rejects near-uniform compression noise).
        yy, xx = np.ogrid[:cropped.shape[0], :cropped.shape[1]]
        distance2 = (xx - cx)**2 + (yy - cy)**2
        core = cropped[distance2 < (max(2, radius * .65))**2]
        ring = cropped[(distance2 > (radius * 1.2)**2) & (distance2 < (radius * 1.7)**2)]
        if not len(core) or not len(ring):
            continue
        contrast = float(np.median(ring) - np.median(core))
        if polarity == "bright":
            contrast *= -1
        if contrast < 8:
            continue
        perimeter = max(1.0, cv2.arcLength(c, True))
        circularity = 4 * np.pi * area / perimeter**2
        relative_center = np.hypot((cx-center_x)/max(1,w), (cy-center_y)/max(1,h))
        score = float(np.clip(np.clip(circularity, 0, 1) * (minor/major)**0.5 *
                      max(0, 1 - relative_center) * min(1., contrast / 35.), 0, 1))
        if score >= 0.15:
            candidates.append((score, cx, cy, radius, (float(axes[0]), float(axes[1]))))
    if not candidates:
        return None
    score, cx, cy, r, axes = max(candidates)
    # Glint is an optional optical quality diagnostic, not a true 3D PCCR model.
    local = cropped.copy()
    glare_threshold = np.percentile(local, 99.7)
    glare = (local >= max(glare_threshold, 245)).astype(np.uint8)
    glint = None
    n, labels, stats, centers = cv2.connectedComponentsWithStats(glare, 8)
    best = []
    for i in range(1, n):
        if 1 <= stats[i, cv2.CC_STAT_AREA] <= 30:
            gx, gy = centers[i]
            if np.hypot(gx - cx, gy - cy) <= max(15, r * 2):
                best.append((np.hypot(gx - cx, gy - cy), gx, gy))
    if best:
        _, gx, gy = min(best)
        glint = (float(x0 + gx), float(y0 + gy))
    return Pupil((float(x0 + cx), float(y0 + cy)), float(r), score, axes, glint)


def ratio_xy(point: tuple[float, float], roi: tuple[int, int, int, int]) -> tuple[float, float]:
    x,y,w,h = roi
    return ((point[0] - x)/w, (point[1] - y)/h)
