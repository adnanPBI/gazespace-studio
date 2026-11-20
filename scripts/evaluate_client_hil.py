"""Evaluate measured, externally collected client HIL data. Never invent a pass.

CSV/JSON schemas are documented in docs/CLIENT_HIL_RUNBOOK.md.
This tool computes measurements and evidence eligibility, not customer sign-off.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys


REQUIRED_CRITERIA = {
    "min_participants", "min_targets_per_participant", "min_valid_fraction",
    "max_screen_p95_error_px", "max_stationary_jitter_rms_px", "max_drift_px",
    "min_drift_interval_s", "max_spatial_p95_angular_deg",
    "max_spatial_p95_miss_mm", "max_latency_p95_ms",
    "min_latency_trials", "min_spatial_samples_per_target",
}

SCREEN_COLUMNS = {
    "participant_id", "validation_session_id", "training_session_id",
    "target_id", "condition", "time_s", "target_x_px", "target_y_px",
    "gaze_x_px", "gaze_y_px", "valid", "anchor_phase",
}
SPATIAL_COLUMNS = {
    "participant_id", "validation_session_id", "training_session_id",
    "target_id", "target_x_m", "target_y_m", "target_z_m",
    "origin_x_m", "origin_y_m", "origin_z_m",
    "direction_x", "direction_y", "direction_z",
}
LATENCY_COLUMNS = {
    "trial_id", "rig_id", "exposure_reference_ns", "display_photon_ns",
    "clock_domain", "synchronization_verified", "measurement_method",
}
HOST_CASES = {"valid", "blink", "stale", "disconnect", "stop", "recovery"}


class EvidenceError(ValueError):
    pass


def _number(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise EvidenceError(f"{label} is not a number") from exc
    if not math.isfinite(number):
        raise EvidenceError(f"{label} must be finite")
    return number


def _true(value):
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def _quantile(values, q):
    if not values:
        raise EvidenceError("cannot calculate a percentile on an empty measurement set")
    values = sorted(values)
    pos = (len(values) - 1) * q
    low, high = math.floor(pos), math.ceil(pos)
    return values[low] + (values[high] - values[low]) * (pos - low)


def _metrics(values):
    return {
        "count": len(values),
        "median": statistics.median(values),
        "p95": _quantile(values, .95),
        "rms": math.sqrt(sum(x*x for x in values) / len(values)),
        "max": max(values),
    }


def _read_csv(path, columns):
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or not columns.issubset(set(reader.fieldnames)):
            missing = columns - set(reader.fieldnames or [])
            raise EvidenceError(f"{path.name}: missing CSV columns {sorted(missing)}")
        rows = list(reader)
    if not rows:
        raise EvidenceError(f"{path.name}: no measurement rows")
    return rows


def _load_json(path):
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise EvidenceError(f"{path.name}: expected an object")
    return result


def _digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _independent(row, label):
    subject = (row.get("participant_id") or "").strip()
    training = (row.get("training_session_id") or "").strip()
    validation = (row.get("validation_session_id") or "").strip()
    target = (row.get("target_id") or "").strip()
    if not all((subject, training, validation, target)) or training == validation:
        raise EvidenceError(f"{label}: distinct labeled participant, training/validation sessions and target are required")
    return subject, validation, target


def score_screen(path):
    rows = _read_csv(path, SCREEN_COLUMNS)
    by_subject, groups, phases = {}, {}, {}
    valid_rows = 0
    for n, row in enumerate(rows, 2):
        participant, session, target = _independent(row, f"screen row {n}")
        condition = (row.get("condition") or "").strip()
        if not condition:
            raise EvidenceError(f"screen row {n}: condition is required")
        time_s = _number(row["time_s"], f"screen row {n} time_s")
        target_xy = (_number(row["target_x_px"], "target x"), _number(row["target_y_px"], "target y"))
        key = (participant, session, target, condition, (row.get("anchor_phase") or "").strip())
        group = groups.setdefault(key, {"values": [], "times": [], "reference": target_xy})
        if group["reference"] != target_xy:
            raise EvidenceError(f"screen row {n}: reference moved within a stationary target group")
        if group["times"] and time_s <= group["times"][-1]:
            raise EvidenceError(f"screen row {n}: repeated observations need increasing target timestamps")
        if key[-1] not in ("", "initial", "repeat"):
            raise EvidenceError(f"screen row {n}: unsupported anchor phase")
        subject = by_subject.setdefault(participant, {"targets": set(), "errors": [], "valid": 0, "total": 0})
        subject["targets"].add(target)
        subject["total"] += 1
        flag = (row.get("valid") or "").strip().lower()
        if flag not in ("true", "false"):
            raise EvidenceError(f"screen row {n}: valid must be true/false")
        if flag == "false":
            continue
        x = _number(row["gaze_x_px"], f"screen row {n} gaze x")
        y = _number(row["gaze_y_px"], f"screen row {n} gaze y")
        error = math.hypot(x - target_xy[0], y - target_xy[1])
        subject["errors"].append(error)
        subject["valid"] += 1
        valid_rows += 1
        group["values"].append((x, y))
        group["times"].append(time_s)
    if valid_rows == 0:
        raise EvidenceError("screen gaze contains no valid human measurements")
    jitters, drifts = [], []
    for key, group in groups.items():
        if len(group["values"]) >= 4:
            values = group["values"]
            cx = statistics.fmean(x for x, _ in values)
            cy = statistics.fmean(y for _, y in values)
            jitters.append(math.sqrt(sum((x-cx)**2 + (y-cy)**2 for x, y in values)/len(values)))
        phase = key[-1]
        if phase in ("initial", "repeat") and group["values"]:
            skey = (key[0], key[2], key[3])
            phases.setdefault(skey, {})[phase] = group
    for group_key, group in phases.items():
        if not {"initial", "repeat"}.issubset(group):
            continue
        before, after = group["initial"], group["repeat"]
        a = (statistics.fmean(x for x, _ in before["values"]),
             statistics.fmean(y for _, y in before["values"]))
        b = (statistics.fmean(x for x, _ in after["values"]),
             statistics.fmean(y for _, y in after["values"]))
        interval = statistics.median(after["times"]) - statistics.median(before["times"])
        if interval <= 0:
            raise EvidenceError("repeated anchor must occur after initial fixation")
        drifts.append({"participant_id": group_key[0], "drift_px": math.dist(a, b), "interval_s": interval})
    if not jitters:
        raise EvidenceError("need at least one stationary target with four valid measurements")
    participants = {
        p: {"distinct_targets": len(v["targets"]), "valid_fraction": v["valid"]/v["total"],
            "error_px": _metrics(v["errors"]) if v["errors"] else None}
        for p, v in by_subject.items()
    }
    return {
        "rows": len(rows), "valid_rows": valid_rows,
        "participants": participants,
        "overall_error_px": _metrics([error for v in by_subject.values() for error in v["errors"]]),
        "worst_participant_p95_px": max(v["error_px"]["p95"] for v in participants.values() if v["error_px"]),
        "worst_stationary_jitter_rms_px": max(jitters),
        "stationary_group_count": len(groups),
        "stationary_groups_with_four_valid_samples": len(jitters),
        "drift_participants": sorted({d["participant_id"] for d in drifts}),
        "drift": drifts,
        "worst_drift_px": max([d["drift_px"] for d in drifts], default=None),
        "minimum_drift_interval_s": min([d["interval_s"] for d in drifts], default=None),
        "valid_fraction": valid_rows/len(rows),
    }


def score_spatial(path):
    rows = _read_csv(path, SPATIAL_COLUMNS)
    angles, misses, participants, group_counts = [], [], {}, {}
    for n, row in enumerate(rows, 2):
        subject, session, target = _independent(row, f"spatial row {n}")
        destination = [_number(row[f"target_{a}_m"], f"spatial target {a}") for a in "xyz"]
        origin = [_number(row[f"origin_{a}_m"], f"spatial origin {a}") for a in "xyz"]
        direction = [_number(row[f"direction_{a}"], f"spatial direction {a}") for a in "xyz"]
        magnitude = math.sqrt(sum(d*d for d in direction))
        distance = math.dist(destination, origin)
        if not 0.8 <= magnitude <= 1.2 or distance <= .001:
            raise EvidenceError(f"spatial row {n}: invalid ray or target geometry")
        direction = [d/magnitude for d in direction]
        relative = [t-o for t, o in zip(destination, origin)]
        along = sum(a*b for a, b in zip(relative, direction))
        if along <= 0:
            raise EvidenceError(f"spatial row {n}: target behind reported ray")
        misses.append(math.sqrt(max(0, distance**2-along**2))*1000.0)
        angles.append(math.degrees(math.acos(max(-1, min(1, along/distance)))))
        participants.setdefault(subject, set()).add(target)
        group_counts[(subject, session, target)] = group_counts.get((subject, session, target), 0) + 1
    return {
        "rows": len(rows), "angular_error_deg": _metrics(angles),
        "target_miss_mm": _metrics(misses),
        "participants": {s: len(v) for s, v in participants.items()},
        "min_samples_per_target": min(group_counts.values()),
    }


def score_latency(path):
    rows = _read_csv(path, LATENCY_COLUMNS)
    delays, domains, rigs = [], set(), set()
    for n, row in enumerate(rows, 2):
        if not (row["trial_id"].strip() and row["rig_id"].strip()):
            raise EvidenceError(f"latency row {n}: missing trial or rig")
        if not _true(row["synchronization_verified"]):
            raise EvidenceError(f"latency row {n}: unverified clock alignment")
        if row["measurement_method"].strip().lower() != "trigger_photodiode":
            raise EvidenceError(f"latency row {n}: real trigger/photodiode reference required")
        domain = row["clock_domain"].strip()
        if not domain:
            raise EvidenceError(f"latency row {n}: missing clock domain")
        domains.add(domain)
        rigs.add(row["rig_id"].strip())
        start = _number(row["exposure_reference_ns"], "exposure reference")
        end = _number(row["display_photon_ns"], "display photon")
        delay_ms = (end-start)/1e6
        if delay_ms < 0 or delay_ms > 30000:
            raise EvidenceError(f"latency row {n}: impossible/negative exposure-to-photon latency")
        delays.append(delay_ms)
    if len(domains) != 1 or len(rigs) != 1:
        raise EvidenceError("latency evidence must share one measured rig and synchronized clock domain")
    return {"trials": len(rows), "rig_id": next(iter(rigs)),
            "clock_domain": next(iter(domains)), "exposure_to_display_ms": _metrics(delays)}


def _resolve(base, value):
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError("evidence file path missing")
    return (base / value).resolve()


def evaluate(manifest_path):
    manifest = _load_json(manifest_path)
    criteria = manifest.get("criteria", {})
    missing = sorted(REQUIRED_CRITERIA - set(criteria)) if isinstance(criteria, dict) else sorted(REQUIRED_CRITERIA)
    report = {
        "schema_version": 1, "generated_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "client-supplied physical evidence; no synthetic fixtures authorized",
        "source_commit": manifest.get("source_commit"), "criteria_missing": missing,
        "measurements": {}, "evidence_sha256": {}, "checks": {},
        "client_signoff": "not_performed_by_software",
        "customer_acceptance_passed": None,
    }

    def gate(name, outcome):
        report["checks"][name] = bool(outcome)

    def get_receipt(name):
        filename = manifest.get("evidence", {}).get(name)
        if not filename:
            report["checks"][name + "_provided"] = False
            return None
        path = _resolve(manifest_path.parent, filename)
        if not path.is_file():
            report["checks"][name + "_provided"] = False
            return None
        report["evidence_sha256"][name] = _digest(path)
        report["checks"][name + "_provided"] = True
        return path

    gate("criteria_complete", not missing)
    rig = manifest.get("rig") or {}
    gate("physical_rig_documented", bool(
        _true(rig.get("physical_device_confirmed"))
        and _true(rig.get("global_shutter_verified"))
        and _true(rig.get("ir_illumination_safety_reviewed"))
        and all(rig.get(k) for k in ("camera_model","camera_serial","lens_id","fixture_id"))
    ))
    commit = manifest.get("source_commit")
    gate("release_identity_pinned", isinstance(commit, str) and len(commit) == 40
         and all(c in "0123456789abcdef" for c in commit.lower()))

    def try_measure(name, func):
        path = get_receipt(name)
        if path is None:
            return
        try:
            report["measurements"][name] = func(path)
            gate(name + "_measured", True)
        except (OSError, ValueError, KeyError, TypeError, csv.Error) as exc:
            gate(name + "_measured", False)
            report.setdefault("errors", {})[name] = str(exc)

    for name, func in (("screen_gaze",score_screen),("spatial_gaze",score_spatial),
                       ("latency",score_latency)):
        try_measure(name, func)

    for name in ("camera","signatures","host"):
        path = get_receipt(name)
        if path is None:
            continue
        try:
            value = _load_json(path)
            report["measurements"][name] = value
            if name == "camera":
                gate("camera_physical_pass", value.get("status") == "capture_checks_completed_gaze_acceptance_pending"
                     and value.get("physical_hardware_tested") is True
                     and value.get("camera_acquisition_requested") is True)
            elif name == "signatures":
                images = value.get("executables") or []
                gate("publisher_signatures_verified", value.get("status") == "passed"
                     and len(images) == 2 and all(
                         image.get("signature_status") == "Valid" and image.get("sha256")
                         and image.get("signer_thumbprint") == value.get("expected_signer_thumbprint")
                         for image in images))
            elif name == "host":
                cases = value.get("cases") or {}
                gate("original_host_integration_verified", value.get("status") == "passed"
                     and value.get("original_client_app_tested") is True
                     and bool(value.get("host_application_build_sha256"))
                     and HOST_CASES.issubset(set(cases))
                     and all(cases.get(k) is True for k in HOST_CASES))
        except (ValueError, TypeError, KeyError, OSError) as exc:
            report.setdefault("errors", {})[name] = str(exc)
            gate(name + "_valid", False)

    if not missing:
        try:
            screen = report["measurements"]["screen_gaze"]
            spatial = report["measurements"]["spatial_gaze"]
            latency = report["measurements"]["latency"]
            gate("criteria_values_valid",
                 int(criteria["min_participants"]) >= 1
                 and int(criteria["min_targets_per_participant"]) >= 1
                 and int(criteria["min_spatial_samples_per_target"]) >= 1
                 and int(criteria["min_latency_trials"]) >= 1
                 and 0 < _number(criteria["min_valid_fraction"], "minimum valid fraction") <= 1
                 and all(_number(criteria[k], k) >= 0 for k in
                         ("max_screen_p95_error_px", "max_stationary_jitter_rms_px",
                          "max_drift_px", "min_drift_interval_s",
                          "max_spatial_p95_angular_deg", "max_spatial_p95_miss_mm",
                          "max_latency_p95_ms")))
            gate("participant_and_target_coverage",
                 len(screen["participants"]) >= int(criteria["min_participants"])
                 and all(p["distinct_targets"] >= int(criteria["min_targets_per_participant"])
                         for p in screen["participants"].values())
                 and len(spatial["participants"]) >= int(criteria["min_participants"])
                 and all(p >= int(criteria["min_targets_per_participant"])
                         for p in spatial["participants"].values()))
            gate("every_stationary_target_sampled",
                 screen["stationary_group_count"] == screen["stationary_groups_with_four_valid_samples"])
            gate("drift_each_participant",
                 set(screen["participants"]).issubset(set(screen["drift_participants"])))
            gate("spatial_samples_per_target",
                 spatial["min_samples_per_target"] >= int(criteria["min_spatial_samples_per_target"]))
            gate("latency_trial_coverage",
                 latency["trials"] >= int(criteria["min_latency_trials"]))
            gate("valid_frame_fraction", min(p["valid_fraction"] for p in
                 screen["participants"].values()) >= _number(criteria["min_valid_fraction"], "minimum valid fraction"))
            gate("screen_p95_px", screen["worst_participant_p95_px"] <= _number(criteria["max_screen_p95_error_px"], "screen p95"))
            gate("stationary_jitter_rms_px", screen["worst_stationary_jitter_rms_px"] <= _number(criteria["max_stationary_jitter_rms_px"], "jitter"))
            gate("drift_and_interval", screen["worst_drift_px"] is not None
                 and screen["worst_drift_px"] <= _number(criteria["max_drift_px"], "drift")
                 and screen["minimum_drift_interval_s"] >= _number(criteria["min_drift_interval_s"], "drift interval"))
            gate("spatial_p95_angle", spatial["angular_error_deg"]["p95"] <= _number(criteria["max_spatial_p95_angular_deg"], "spatial angle"))
            gate("spatial_p95_miss", spatial["target_miss_mm"]["p95"] <= _number(criteria["max_spatial_p95_miss_mm"], "spatial miss"))
            gate("exposure_to_photon_p95", latency["exposure_to_display_ms"]["p95"] <= _number(criteria["max_latency_p95_ms"], "latency"))
        except (KeyError, ValueError, TypeError) as exc:
            report.setdefault("errors", {})["thresholds"] = str(exc)
            gate("all_measurement_thresholds", False)

    report["all_evidence_checks_passed"] = bool(report["checks"]) and all(report["checks"].values())
    report["status"] = ("evidence_thresholds_passed_pending_customer_signoff"
                        if report["all_evidence_checks_passed"] else "incomplete_or_failed")
    report["limitations"] = [
        "Receipt declarations and labels are operator-provided; this tool cannot independently prove physical provenance.",
        "Hardware-only timing and SDK emulator tests are not exposure-to-photon latency measurements.",
        "This program never issues customer acceptance or a clinical/eye-safety certification.",
    ]
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    report = evaluate(args.manifest.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"{report['status']}: {args.output}")
    return 0 if report["all_evidence_checks_passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
