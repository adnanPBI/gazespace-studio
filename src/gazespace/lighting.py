"""Basic exposure/blur/dynamic-range warnings for changing RGB/IR illumination.

These heuristics are diagnostics, not calibrated physical measurements.
"""
from __future__ import annotations
import numpy as np
import cv2


def frame_quality(image_bgr: np.ndarray) -> dict:
    if image_bgr.ndim==3:
        g=cv2.cvtColor(image_bgr,cv2.COLOR_BGR2GRAY)
    else:
        g=image_bgr
    sample=g[::4,::4]
    p05,p50,p95=np.percentile(sample,(5,50,95))
    dark_fraction=float(np.mean(sample<12))
    bright_fraction=float(np.mean(sample>245))
    contrast=float(p95-p05)
    # Stable downscaled Laplacian to keep CPU impact small.
    blur_variance=float(cv2.Laplacian(cv2.resize(g,(320,180)),cv2.CV_64F).var())
    alerts=[]
    if p50<35:
        alerts.append("underexposure_or_weak_illumination")
    if p50>225:
        alerts.append("overexposure_or_strong_illumination")
    if dark_fraction>.65:
        alerts.append("substantial_dark_clipping")
    if bright_fraction>.65:
        alerts.append("substantial_bright_clipping")
    if contrast<25:
        alerts.append("low_dynamic_range")
    if blur_variance<10:
        alerts.append("possible_defocus_or_motion_blur")
    return {"p05":float(p05),"median":float(p50),"p95":float(p95),
            "dark_fraction":dark_fraction,"bright_fraction":bright_fraction,
            "contrast":contrast,"sharpness_laplacian":blur_variance,"warnings":alerts}
