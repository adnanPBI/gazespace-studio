"""Software-only regressions for the client HIL evidence tools.

All fixtures in this test are SYNTHETIC. They are not physical validation.
"""
from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_tool(filename, module_name):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


hil = load_tool("evaluate_client_hil.py", "gazespace_hil_checker")
host = load_tool("verify_host_contract.py", "gazespace_host_checker")


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def screen_rows():
    items = []
    for phase, base in (("initial", 1.0), ("repeat", 13.0)):
        for i, (dx, dy) in enumerate(((0,0),(.6,0),(0,.6),(.6,.6))):
            items.append({
                "participant_id": "synthetic-person-1",
                "validation_session_id": "synthetic-validation",
                "training_session_id": "synthetic-training",
                "target_id": "anchor-a", "condition": "fixture",
                "time_s": base+i*.04,
                "target_x_px": 320, "target_y_px": 200,
                "gaze_x_px": 320+dx + (1 if phase == "repeat" else 0),
                "gaze_y_px": 200+dy, "valid": "true", "anchor_phase": phase,
            })
    return items


def spatial_rows():
    return [{
        "participant_id": "synthetic-person-1",
        "training_session_id": "synthetic-training",
        "validation_session_id": "synthetic-validation",
        "target_id": f"target-{i}", "target_x_m": 0, "target_y_m": 0,
        "target_z_m": 1.0, "origin_x_m": 0, "origin_y_m": 0,
        "origin_z_m": 0, "direction_x": 0, "direction_y": 0,
        "direction_z": 1,
    } for i in range(3)]


def latency_rows():
    return [{
        "trial_id": str(i), "rig_id": "synthetic-rig",
        "exposure_reference_ns": 1_000_000_000 + i*1_000_000_000,
        "display_photon_ns": 1_020_000_000 + i*1_000_000_000,
        "clock_domain": "synthetic_shared_clock",
        "synchronization_verified": "true",
        "measurement_method": "trigger_photodiode",
    } for i in range(4)]


def test_screen_metrics_compute_stable_jitter_drift_and_valid_ratio(tmp_path):
    path = tmp_path/"screen.csv"
    write_csv(path, screen_rows())
    result = hil.score_screen(path)
    assert result["valid_fraction"] == 1.0
    assert result["worst_drift_px"] == pytest.approx(1.0)
    assert result["minimum_drift_interval_s"] == pytest.approx(12.0)
    assert result["worst_stationary_jitter_rms_px"] < 1.0
    assert result["worst_participant_p95_px"] < 2.0


def test_training_session_reuse_rejected(tmp_path):
    rows = screen_rows()
    rows[0]["validation_session_id"] = rows[0]["training_session_id"]
    path = tmp_path/"screen.csv"
    write_csv(path, rows)
    with pytest.raises(hil.EvidenceError, match="distinct"):
        hil.score_screen(path)


def test_spatial_world_ray_is_metric_and_angular(tmp_path):
    path = tmp_path/"spatial.csv"
    write_csv(path, spatial_rows())
    result = hil.score_spatial(path)
    assert result["angular_error_deg"]["p95"] == 0
    assert result["target_miss_mm"]["p95"] == 0


def test_unsynchronized_latency_fails_closed(tmp_path):
    path = tmp_path/"latency.csv"
    rows = latency_rows()
    rows[0]["synchronization_verified"] = "false"
    write_csv(path, rows)
    with pytest.raises(hil.EvidenceError, match="unverified clock"):
        hil.score_latency(path)


def test_photodiode_latency_metrics(tmp_path):
    path = tmp_path/"latency.csv"
    write_csv(path, latency_rows())
    result = hil.score_latency(path)
    assert result["exposure_to_display_ms"]["p95"] == pytest.approx(20)


def test_absent_physical_evidence_never_marks_customer_acceptance(tmp_path):
    manifest = tmp_path/"manifest.json"
    manifest.write_text(json.dumps({
        "source_commit": "4"*40,
        "criteria": {}, "evidence": {},
        "rig": {"physical_device_confirmed": False},
    }), encoding="utf-8")
    result = hil.evaluate(manifest)
    assert result["status"] == "incomplete_or_failed"
    assert result["customer_acceptance_passed"] is None
    assert result["checks"]["physical_rig_documented"] is False
    assert result["checks"]["criteria_complete"] is False


def events():
    records = []
    for index, case in enumerate(("valid","blink","stale","disconnect","stop","recovery")):
        valid = case in ("valid", "recovery")
        records.append({
            "case": case, "host_event_time_s": index+1,
            "host_gaze_visible": valid, "engine_valid": valid,
            "host_screen_xy_px": [200,150] if valid else None,
        })
    return records


def test_host_invalid_states_clear_the_existing_app_output():
    assert all(host.validate_events(events()).values())
    data = events()
    data[2]["host_gaze_visible"] = True
    with pytest.raises(ValueError, match="invalid gaze"):
        host.validate_events(data)


def test_host_fixture_is_not_original_client_acceptance(tmp_path):
    trace = tmp_path/"events.jsonl"
    trace.write_text("\n".join(json.dumps(x) for x in events())+"\n", encoding="utf-8")
    source = tmp_path/"client-host.exe"
    source.write_bytes(b"synthetic fixture")
    result = host.verify(trace, source, physical_test_confirmed=False)
    assert result["status"] == "pending_original_host_test"
    assert result["original_client_app_tested"] is False
    assert result["host_application_build_sha256"]


def test_signature_verifier_is_explicit_publisher_only():
    ps=(ROOT/"scripts"/"verify_client_signatures.ps1").read_text(encoding="utf-8")
    assert "Get-AuthenticodeSignature" in ps
    assert "SignerThumbprint" in ps
    assert "Cert:\\CurrentUser\\My" not in ps  # verifier never selects a private key
    assert "New-SelfSignedCertificate" not in ps
