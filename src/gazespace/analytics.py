"""Offline gaze-stream analytics and self-contained evidence reports.

Precision describes dispersion of model estimates. It is not gaze accuracy:
accuracy requires fresh, independently known fixation targets. Missing frames,
blinks, pauses and long timestamp gaps never accrue dwell time.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from html import escape
from pathlib import Path
import json
import math
import numpy as np

from .sessions import SessionData, load_session, export_csv, _atomic_json, _validate_record


@dataclass(frozen=True)
class AreaOfInterest:
    name: str
    rect: tuple[float, float, float, float]  # normalized x,y,width,height
    surface_name: str | None = None

    def __post_init__(self):
        if not self.name or len(self.rect) != 4 or not np.isfinite(self.rect).all():
            raise ValueError("AOI requires a name and finite normalized rectangle")
        x, y, width, height = self.rect
        if min(x, y) < 0 or min(width, height) <= 0 or x + width > 1 + 1e-9 or y + height > 1 + 1e-9:
            raise ValueError("AOI must fit within normalized [0,1] coordinates")

    def contains(self, xy):
        x, y, width, height = self.rect
        return x <= xy[0] <= x + width and y <= xy[1] <= y + height


def _stats(values):
    data = np.asarray(values, dtype=float)
    if not len(data):
        return {"count": 0, "mean": None, "median": None, "p95": None, "max": None}
    return {"count": int(len(data)), "mean": float(np.mean(data)),
            "median": float(np.median(data)), "p95": float(np.percentile(data, 95)),
            "max": float(np.max(data))}


def precision_metrics(points) -> dict:
    """Spatial RMS of a cluster, not the misleading SD of radial distances."""
    array = np.asarray(points, dtype=float)
    if array.size == 0:
        return {"samples": 0, "mean_xy": None, "std_xy": None, "radial_rms": None,
                "radial_p95": None, "sample_to_sample_rms": None}
    if array.ndim != 2 or array.shape[1] != 2 or not np.isfinite(array).all():
        raise ValueError("precision samples must be a finite Nx2 array")
    center = np.mean(array, axis=0)
    distances = np.linalg.norm(array - center, axis=1)
    differences = np.linalg.norm(np.diff(array, axis=0), axis=1)
    return {"samples": int(len(array)), "mean_xy": center.tolist(),
            "std_xy": np.std(array, axis=0).tolist(),
            "radial_rms": float(np.sqrt(np.mean(distances ** 2))),
            "radial_p95": float(np.percentile(distances, 95)),
            "sample_to_sample_rms": float(np.sqrt(np.mean(differences ** 2))) if len(differences) else None}


def compare_anchors(first_xy, repeat_xy, threshold_px: float = 30.0,
                    target_px=None) -> dict:
    """Compare instructed repeat fixations without silently modifying a model."""
    first, repeat = np.asarray(first_xy, float), np.asarray(repeat_xy, float)
    if first.ndim != 2 or repeat.ndim != 2 or first.shape[1:] != (2,) or repeat.shape[1:] != (2,):
        raise ValueError("anchor samples must be Nx2 pixel arrays")
    if min(len(first), len(repeat)) < 4 or not np.isfinite(first).all() or not np.isfinite(repeat).all():
        raise ValueError("each anchor requires >=4 finite samples")
    if not math.isfinite(threshold_px) or threshold_px <= 0:
        raise ValueError("drift threshold must be positive")
    a, b = np.mean(first, axis=0), np.mean(repeat, axis=0)
    drift = float(np.linalg.norm(b - a))
    report = {"drift_px": drift, "offset_xy_px": (b - a).tolist(),
              "threshold_px": float(threshold_px),
              "outcome": "recalibration_recommended" if drift > threshold_px else "within_selected_drift_threshold",
              "first": precision_metrics(first), "repeat": precision_metrics(repeat),
              "automatic_correction_applied": False,
              "ground_truth": "user_instructed_repeated_fixation",
              "accuracy_verified": False}
    if target_px is not None:
        target = np.asarray(target_px, float)
        if target.shape != (2,) or not np.isfinite(target).all():
            raise ValueError("target must be finite pixel XY")
        report["target_px"] = target.tolist()
        report["first_target_error_px"] = _stats(np.linalg.norm(first - target, axis=1))
        report["repeat_target_error_px"] = _stats(np.linalg.norm(repeat - target, axis=1))
    return report


def _is_stale(record):
    return bool(record.get("stale", False) or "stale" in str(record.get("reason", "")).lower())


def _screen_point(record, screen_size):
    if not record.get("valid") or not record.get("screen_valid", True) or _is_stale(record):
        return None, None
    xy = record.get("gaze_screen_px")
    norm = record.get("gaze_screen_normalized")
    if norm is not None:
        norm = np.asarray(norm, float)
        if norm.shape != (2,) or np.any(norm < 0) or np.any(norm > 1):
            return None, None
    if xy is not None:
        xy = np.asarray(xy, float)
        if xy.shape != (2,):
            return None, None
    if screen_size is not None:
        extent = np.asarray(screen_size, float) - 1
        if xy is not None and (np.any(xy < 0) or np.any(xy > extent + 1e-8)):
            return None, None
        if norm is None and xy is not None:
            norm = xy / extent
        if xy is None and norm is not None:
            xy = norm * extent
    return xy, norm


def _fixations(records, points, times, max_gap_s, radius_px, min_duration_s):
    clusters = []
    buffer = []

    def finish():
        nonlocal buffer
        if len(buffer) >= 3:
            ids = buffer
            duration = times[ids[-1]] - times[ids[0]]
            if duration >= min_duration_s:
                values = np.array([points[i] for i in ids])
                metrics = precision_metrics(values)
                clusters.append({"start_s": float(times[ids[0]]), "end_s": float(times[ids[-1]]),
                                 "duration_s": float(duration), "samples": len(ids),
                                 "centroid_px": metrics["mean_xy"], "jitter_rms_px": metrics["radial_rms"],
                                 "jitter_std_xy_px": metrics["std_xy"]})
        buffer = []

    for index, point in enumerate(points):
        if point is None:
            finish()
            continue
        if buffer:
            gap = times[index] - times[buffer[-1]]
            candidate = np.array([points[i] for i in buffer] + [point])
            center = np.mean(candidate, axis=0)
            radius = float(np.max(np.linalg.norm(candidate - center, axis=1)))
            if gap <= 0 or gap > max_gap_s or radius > radius_px:
                finish()
        buffer.append(index)
    finish()
    return clusters


def analyze_session(records, screen_size: tuple[int, int] | None = None,
                    aois=None, *, max_gap_s: float = 0.15,
                    fixation_radius_px: float = 35.0,
                    min_fixation_duration_s: float = 0.12,
                    heatmap_shape: tuple[int, int] = (32, 18),
                    surface_sizes: dict[str, tuple[float, float]] | None = None) -> dict:
    """Analyze metadata records with explicit timestamp-gap exclusion.

    AOIs use normalized rectangles. A surface AOI sets ``surface_name`` and
    requires its physical (width_m,height_m) in ``surface_sizes``. Overlapping
    AOIs are measured independently, so their dwell totals may overlap.
    """
    rows = [_validate_record(r) for r in records]
    if screen_size is not None and (len(screen_size) != 2 or min(screen_size) < 2 or any(int(v) != v for v in screen_size)):
        raise ValueError("screen dimensions must contain two integers >=2")
    if not all(math.isfinite(v) and v > 0 for v in (max_gap_s, fixation_radius_px, min_fixation_duration_s)):
        raise ValueError("analysis thresholds must be positive and finite")
    cols, height = (int(v) for v in heatmap_shape)
    if not (2 <= cols <= 128 and 2 <= height <= 128):
        raise ValueError("heatmap dimensions must lie within [2,128]")
    areas = [a if isinstance(a, AreaOfInterest) else AreaOfInterest(str(a["name"]), tuple(a["rect"]), a.get("surface_name")) for a in (aois or [])]
    if len({a.name for a in areas}) != len(areas):
        raise ValueError("AOI names must be unique")
    surface_sizes = surface_sizes or {}
    for name, size in surface_sizes.items():
        if len(size) != 2 or not np.isfinite(size).all() or min(size) <= 0:
            raise ValueError(f"invalid metric surface dimensions for {name}")
    timestamps = [float(r.get("session_time_s", r["timestamp_s"])) for r in rows]
    if any(b < a for a, b in zip(timestamps, timestamps[1:])):
        raise ValueError("records must be ordered by session timestamp")
    times = np.array(timestamps, float)
    if len(times):
        times = times - times[0]
    deltas = np.diff(times, prepend=times[0] if len(times) else 0)
    deltas = deltas if len(times) else np.array([], float)
    good_intervals = (deltas > 0) & (deltas <= max_gap_s)
    points, normalized = [], []
    for record in rows:
        pixel, norm = _screen_point(record, screen_size)
        points.append(pixel)
        normalized.append(norm)
    valid_flags = np.array([bool(r.get("valid")) and not _is_stale(r) for r in rows], bool)
    screen_flags = np.array([p is not None or n is not None for p, n in zip(points, normalized)], bool)
    weights = np.zeros(len(rows), float)
    for i in range(1, len(rows)):
        if good_intervals[i] and screen_flags[i] and screen_flags[i - 1]:
            weights[i] = deltas[i]
    heatmap = np.zeros((height, cols), float)
    sample_heatmap = np.zeros((height, cols), int)
    for i, xy in enumerate(normalized):
        if xy is not None:
            column = min(cols - 1, max(0, int(xy[0] * cols)))
            row = min(height - 1, max(0, int(xy[1] * height)))
            heatmap[row, column] += weights[i]
            sample_heatmap[row, column] += 1
    aoi_reports = []
    for area in areas:
        dwell, entries, longest, active_duration = 0.0, 0, 0.0, 0.0
        previous_inside = False
        for i, record in enumerate(rows):
            if area.surface_name is None:
                xy = normalized[i]
            else:
                metric = record.get("surface_xy_m")
                size = surface_sizes.get(area.surface_name)
                xy = (np.asarray(metric) / np.asarray(size) if size is not None and metric is not None
                      and record.get("valid") and record.get("surface_name") == area.surface_name and not _is_stale(record)
                      else None)
            inside = xy is not None and area.contains(xy)
            continuous = i > 0 and good_intervals[i]
            if inside and (not previous_inside or not continuous):
                entries += 1
                active_duration = 0.0
            if inside and previous_inside and continuous:
                dwell += float(deltas[i])
                active_duration += float(deltas[i])
                longest = max(longest, active_duration)
            elif not inside:
                active_duration = 0.0
            previous_inside = inside
        aoi_reports.append({"name": area.name, "rect": list(area.rect), "surface_name": area.surface_name,
                            "dwell_s": dwell, "entries": entries, "longest_contiguous_dwell_s": longest})
    fixations = _fixations(rows, points, times, max_gap_s, fixation_radius_px, min_fixation_duration_s)
    all_points = [p for p in points if p is not None]
    overall = precision_metrics(all_points)
    # Across all targets, dispersion describes coverage, not fixation jitter.
    fixation_precision = _stats([f["jitter_rms_px"] for f in fixations])
    latency = [float(r["processing_ms"]) for r in rows
               if isinstance(r.get("processing_ms"), (int, float)) and r["processing_ms"] >= 0]
    quality = [float(r["quality"]) for r in rows
               if isinstance(r.get("quality"), (int, float)) and 0 <= r["quality"] <= 1]
    reasons = Counter(str(r.get("reason", "unknown")) for r in rows if not r.get("valid") or _is_stale(r))
    surfaces = {}
    for name in sorted({r.get("surface_name") for r in rows if r.get("surface_name")}):
        metric_points = []
        dwell = 0.0
        for i, record in enumerate(rows):
            good = record.get("valid") and record.get("surface_name") == name and record.get("surface_xy_m") is not None and not _is_stale(record)
            if good:
                metric_points.append(record["surface_xy_m"])
                if i > 0 and good_intervals[i] and rows[i - 1].get("valid") and rows[i - 1].get("surface_name") == name and not _is_stale(rows[i - 1]):
                    dwell += float(deltas[i])
        surfaces[name] = {"samples": len(metric_points), "dwell_s": dwell,
                          "overall_dispersion_m": precision_metrics(metric_points)}
    explicit_dropped = [r.get("dropped_frames") for r in rows
                        if isinstance(r.get("dropped_frames"), int) and r["dropped_frames"] >= 0]
    warnings = ["Gaze accuracy is not measured by this stream report; validate against independent fixation targets.",
                "Overall dispersion includes intentional eye movement; use fixation jitter for stability.",
                "Intervals longer than max_gap_s and intervals touching invalid data accrue no dwell time."]
    if screen_size is None:
        warnings.append("Screen dimensions unavailable: heatmap uses supplied normalized coordinates only.")
    for area in areas:
        if area.surface_name and area.surface_name not in surface_sizes:
            warnings.append(f"Surface AOI {area.name}: dimensions unavailable; dwell is not measured.")
    report = {
        "schema_version": 2, "screen_size": list(screen_size) if screen_size else None,
        "summary": {"frames": len(rows), "valid_frames": int(np.count_nonzero(valid_flags)),
                    "valid_fraction": float(np.mean(valid_flags)) if len(rows) else 0.0,
                    "screen_frames": int(np.count_nonzero(screen_flags)),
                    "duration_s": float(times[-1]) if len(times) else 0.0,
                    "valid_screen_dwell_s": float(np.sum(weights)),
                    "timestamp_gap_count": int(np.count_nonzero(deltas > max_gap_s)),
                    "excluded_gap_duration_s": float(np.sum(deltas[deltas > max_gap_s])),
                    "duplicate_session_timestamps": int(np.count_nonzero(deltas[1:] == 0)),
                    "stale_frames": sum(_is_stale(r) for r in rows),
                    "reported_dropped_frames": max(explicit_dropped) if explicit_dropped else None,
                    "processing_latency_ms": _stats(latency), "quality": _stats(quality),
                    "invalid_reasons": dict(reasons)},
        "precision": {"overall_screen_dispersion_px": overall,
                      "fixation_jitter_rms_px": fixation_precision,
                      "method": "radial RMS about centroid; consecutive within-cluster displacement"},
        "fixations": fixations, "aois": aoi_reports, "surfaces": surfaces,
        "heatmap": {"width_bins": cols, "height_bins": height, "dwell_seconds": heatmap.tolist(),
                    "sample_counts": sample_heatmap.tolist(), "coordinates": "normalized screen; origin top-left"},
        "thresholds": {"max_gap_s": max_gap_s, "fixation_radius_px": fixation_radius_px,
                       "minimum_fixation_duration_s": min_fixation_duration_s},
        "physical_accuracy_verified": False, "warnings": warnings,
    }
    return report


def heatmap_svg(report: dict, width: int = 960, height: int = 540) -> str:
    heatmap = report["heatmap"]
    values = np.asarray(heatmap["dwell_seconds"], float)
    unit = "dwell time"
    if not values.size or float(values.max(initial=0)) == 0:
        values = np.asarray(heatmap["sample_counts"], float)
        unit = "sample count"
    peak = float(np.max(values)) if values.size else 0.0
    rows, cols = values.shape
    cell_w, cell_h = width / cols, height / rows
    chunks = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="Gaze heatmap by {unit}">',
              f'<rect width="{width}" height="{height}" rx="14" fill="#101b2a"/>']
    for y in range(rows):
        for x in range(cols):
            if values[y, x] > 0 and peak > 0:
                alpha = 0.18 + 0.82 * math.sqrt(values[y, x] / peak)
                chunks.append(f'<rect x="{x*cell_w:.2f}" y="{y*cell_h:.2f}" width="{cell_w:.2f}" height="{cell_h:.2f}" fill="#52e0ba" opacity="{alpha:.3f}"><title>{values[y,x]:.3f} {unit}</title></rect>')
    for area in report.get("aois", []):
        if area.get("surface_name") is None:
            x, y, rw, rh = area["rect"]
            chunks.append(f'<rect x="{x*width:.2f}" y="{y*height:.2f}" width="{rw*width:.2f}" height="{rh*height:.2f}" fill="none" stroke="#f3b86d" stroke-width="2"/>')
            chunks.append(f'<text x="{x*width+6:.2f}" y="{y*height+17:.2f}" fill="#ffe2b4" font-size="14">{escape(area["name"])}</text>')
    if peak <= 0:
        chunks.append(f'<text x="{width/2}" y="{height/2}" fill="#9dafc4" text-anchor="middle" font-size="24">No calibrated screen coordinates recorded</text>')
    chunks.append('</svg>')
    return "".join(chunks)


def _format(value, decimals=2):
    return "Not measured" if value is None else f"{value:.{decimals}f}"


def export_report(session_or_path: SessionData | str | Path, output_dir: str | Path, *,
                  screen_size: tuple[int, int] | None = None, aois=None,
                  surface_sizes=None, **kwargs) -> dict[str, Path]:
    session = session_or_path if isinstance(session_or_path, SessionData) else load_session(session_or_path)
    if screen_size is None:
        metadata = session.manifest.get("metadata", {})
        candidate = metadata.get("screen_size")
        if candidate:
            screen_size = tuple(candidate)
    report = analyze_session(session.records, screen_size, aois,
                             surface_sizes=surface_sizes, **kwargs)
    report["session"] = {"id": session.manifest.get("session_id"),
                         "created_utc": session.manifest.get("created_utc"),
                         "provenance": session.manifest.get("provenance", "unclassified"),
                         "recorded_video": bool(session.manifest.get("video_file")),
                         "metadata": session.manifest.get("metadata", {})}
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path, html_path, csv_path = output / "report.json", output / "report.html", output / "gaze.csv"
    _atomic_json(json_path, report)
    export_csv(session, csv_path)
    summary = report["summary"]
    cards = [("Recorded frames", str(summary["frames"])),
             ("Valid estimates", f'{summary["valid_fraction"]*100:.1f}%'),
             ("Session duration", _format(summary["duration_s"]) + " s"),
             ("Processing p95", _format(summary["processing_latency_ms"]["p95"]) + " ms"),
             ("Fixation jitter median", _format(report["precision"]["fixation_jitter_rms_px"]["median"]) + " px"),
             ("Excluded gap time", _format(summary["excluded_gap_duration_s"]) + " s")]
    cards_html = "".join(f'<article><span>{escape(name)}</span><strong>{escape(value)}</strong></article>' for name, value in cards)
    aoi_rows = "".join(f'<tr><td>{escape(a["name"])}</td><td>{escape(a.get("surface_name") or "Screen")}</td><td>{a["dwell_s"]:.3f}</td><td>{a["entries"]}</td><td>{a["longest_contiguous_dwell_s"]:.3f}</td></tr>' for a in report["aois"])
    if not aoi_rows:
        aoi_rows = '<tr><td colspan="5">No areas of interest configured.</td></tr>'
    fix_rows = "".join(f'<tr><td>{f["start_s"]:.3f}</td><td>{f["duration_s"]:.3f}</td><td>{f["centroid_px"][0]:.1f}, {f["centroid_px"][1]:.1f}</td><td>{f["jitter_rms_px"]:.3f}</td><td>{f["samples"]}</td></tr>' for f in report["fixations"][:200])
    if not fix_rows:
        fix_rows = '<tr><td colspan="5">No stable clusters met the selected duration and radius.</td></tr>'
    warnings_html = "".join(f'<li>{escape(w)}</li>' for w in report["warnings"])
    doc = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GazeSpace — Session report</title>
<style>body{{margin:0;background:#08111d;color:#e7eef8;font:15px/1.6 system-ui,sans-serif}}main{{max-width:1180px;margin:auto;padding:44px 28px}}header{{border-bottom:1px solid #26364b;padding-bottom:24px;margin-bottom:28px}}h1{{font-size:36px;letter-spacing:-1px;margin:8px 0}}.eyebrow{{color:#57ddba;text-transform:uppercase;letter-spacing:3px;font-size:12px}}p,.muted{{color:#a5b6ca}}.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}}article{{background:#111e2e;border:1px solid #25364c;border-radius:14px;padding:20px}}article span{{display:block;color:#9dafc4}}article strong{{display:block;font-size:28px;font-weight:600;margin-top:5px}}section{{margin-top:30px}}h2{{font-size:21px;font-weight:600}}.heatmap{{border:1px solid #293c53;border-radius:14px;overflow:hidden}}svg{{display:block;width:100%;height:auto}}table{{width:100%;border-collapse:collapse;background:#111e2e;border-radius:12px}}td,th{{padding:12px 16px;border-bottom:1px solid #25364b;text-align:left}}th{{color:#91a7c0;font-weight:500}}.note{{padding:18px 22px;border:1px solid #69543b;border-radius:12px;background:#211e1a;color:#dcc9ac}}code{{word-break:break-all}}@media(max-width:700px){{.cards{{grid-template-columns:repeat(2,1fr)}}table{{font-size:12px}}td,th{{padding:8px}}h1{{font-size:28px}}}}@media print{{body{{background:white;color:#152033}}main{{padding:0}}p,.muted{{color:#42536b}}article,table{{background:white}}article strong{{color:#152033}}.note{{background:#faf7ef}}}}</style></head>
<body><main><header><div class="eyebrow">GazeSpace / local session evidence</div><h1>Tracking and gaze report</h1>
<p>{escape(str(report["session"]["created_utc"] or ""))} · Source: <strong>{escape(str(report["session"]["provenance"]))}</strong><br>Session <code>{escape(str(report["session"]["id"]))}</code></p></header>
<div class="cards">{cards_html}</div>
<section class="note"><strong>What this report measures</strong><p>These statistics describe recorded estimates and software processing time. They do not establish real-world gaze accuracy or end-to-end camera-to-display latency. A separately measured fixation target session is required for those claims.</p></section>
<section><h2>Screen heatmap</h2><p>Dwell-weighted screen coverage. Invalid observations and long gaps do not accrue time.</p><div class="heatmap">{heatmap_svg(report)}</div></section>
<section><h2>Areas of interest</h2><table><thead><tr><th>Area</th><th>Coordinate space</th><th>Dwell (s)</th><th>Entries</th><th>Longest dwell (s)</th></tr></thead><tbody>{aoi_rows}</tbody></table></section>
<section><h2>Stable gaze clusters</h2><p>{len(report["fixations"])} clusters; the table shows up to 200. Radius ≤ {report["thresholds"]["fixation_radius_px"]:.1f} px; duration ≥ {report["thresholds"]["minimum_fixation_duration_s"]:.2f} s. These are algorithmic clusters, not clinical eye-movement classifications.</p><table><thead><tr><th>Start (s)</th><th>Duration (s)</th><th>Centroid (px)</th><th>Radial RMS (px)</th><th>Samples</th></tr></thead><tbody>{fix_rows}</tbody></table></section>
<section><h2>Interpretation and limits</h2><ul>{warnings_html}</ul><p>Complete metrics: <a href="report.json">report.json</a> · Coordinates: <a href="gaze.csv">gaze.csv</a>. All files are local and require no internet connection.</p></section>
</main></body></html>'''
    html_path.write_text(doc, encoding="utf-8")
    return {"json": json_path, "html": html_path, "csv": csv_path}
