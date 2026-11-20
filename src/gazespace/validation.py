"""Independent measurements for camera geometry and gaze-model software output."""
from __future__ import annotations
from datetime import datetime, timezone
from collections import Counter
import hashlib
import json
import numpy as np
import cv2
from .types import CameraIntrinsics, CameraExtrinsics
from .calibration import chessboard_points, find_board, GazeModel, _error_metrics, CONTEXT_KEYS
from .geometry import world_to_camera_point
from .vision import FEATURE_NAMES


def image_digest(image: np.ndarray) -> str:
    if image is None:
        raise ValueError('Cannot fingerprint an unreadable image')
    h=hashlib.sha256()
    h.update(str((image.shape,str(image.dtype))).encode())
    h.update(np.ascontiguousarray(image).tobytes())
    return h.hexdigest()


def camera_training_evidence(images: list[np.ndarray], *, cols=9, rows=6, square_mm=24.0) -> dict:
    return {'image_sha256':[image_digest(im) for im in images],
            'board':{'cols':cols,'rows':rows,'square_mm':square_mm},
            'created_utc':datetime.now(timezone.utc).isoformat()}


def validate_camera_images(images: list[np.ndarray], intrinsics: CameraIntrinsics, *,
                           cols=9,rows=6,square_mm=24.0,training_hashes=None,min_views=3) -> dict:
    intrinsics.validate()
    if intrinsics.source not in ('measured','factory'):
        raise ValueError('Camera validation requires measured or factory intrinsics')
    points=chessboard_points(cols,rows,square_mm)
    banned=set(training_hashes or [])
    seen=set();views=[];errors=[];rejected=[]
    for index,image in enumerate(images):
        digest=image_digest(image)
        if digest in banned:
            raise ValueError('A held-out image was already used for intrinsic training; collect new views')
        if digest in seen:
            raise ValueError('Independent validation images contain a duplicate')
        seen.add(digest)
        if (image.shape[1],image.shape[0])!=(intrinsics.width,intrinsics.height):
            raise ValueError('Validation image resolution differs from camera calibration')
        corners=find_board(image,cols,rows)
        if corners is None:
            rejected.append({'index':index,'reason':'board_not_detected'});continue
        ok,rvec,tvec=cv2.solvePnP(points,corners,intrinsics.camera_matrix,intrinsics.distortion)
        if not ok or tvec[2,0]<=0:
            rejected.append({'index':index,'reason':'board_pose_failed'});continue
        projected,_=cv2.projectPoints(points,rvec,tvec,intrinsics.camera_matrix,intrinsics.distortion)
        per_point=np.linalg.norm(projected.reshape(-1,2)-corners,axis=1)
        if not np.isfinite(per_point).all():raise ValueError('Nonfinite reprojection error')
        errors.extend(per_point.tolist())
        views.append({'index':index,'image_sha256':digest,'rms_px':float(np.sqrt(np.mean(per_point**2))),
                      'p95_px':float(np.percentile(per_point,95))})
    if len(views)<min_views:raise ValueError(f'Need {min_views} fresh detected board views; got {len(views)}')
    values=np.asarray(errors)
    return {'created_utc':datetime.now(timezone.utc).isoformat(),'views':views,'rejected':rejected,
            'independent_of_training_images':bool(training_hashes),
            'intrinsics_refitted':False,
            'reprojection_error_px':{'median':float(np.median(values)),'p95':float(np.percentile(values,95)),
                                     'rms':float(np.sqrt(np.mean(values**2))),'max':float(np.max(values))},
            'note':'Fixed intrinsics; each new board view has an independently estimated pose. Correct physical square measurements remain required.'}


def _same_context_value(left, right) -> bool:
    try:
        return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError('Validation context must contain finite JSON-compatible values') from exc


def _gaze_coverage(labels, targets, kind) -> dict:
    """Minimum acquisition evidence, not user/hardware accuracy thresholds.

    Counts and geometric coverage follow the existing fitting protocol. A
    caller may still calculate raw error for a smaller diagnostic sample.
    """
    counts = Counter(labels)
    centroids = []
    for label in counts:
        selected = targets[np.asarray(labels) == label]
        if kind == 'screen' and not np.allclose(selected, selected[0], atol=1e-9, rtol=0):
            raise ValueError(f'Conflicting screen target coordinates within target_id {label}')
        centroids.append(np.mean(selected, axis=0))
    centroids = np.asarray(centroids)
    distinct = len(np.unique(np.round(centroids, 5), axis=0))
    singular = np.linalg.svd(centroids - centroids.mean(axis=0), compute_uv=False)
    issues = []
    if len(counts) < 9 or distinct < 9:
        issues.append('fewer_than_nine_distinct_targets')
    if min(counts.values()) < 4:
        issues.append('fewer_than_four_samples_per_target')
    spread_minimum = .05 if kind == 'screen' else .01
    if len(singular) < 2 or singular[1] < spread_minimum:
        issues.append('targets_lack_two_dimensional_coverage')
    report = {'target_sample_counts': dict(counts), 'distinct_target_geometry': distinct,
              'spread_singular_values': singular.tolist(),
              'minimum_targets': 9, 'minimum_samples_per_target': 4,
              'second_spread_singular_value_minimum': spread_minimum,
              'policy_basis': 'minimum software acquisition protocol; not an accuracy acceptance criterion'}
    if kind == 'screen':
        span = np.ptp(centroids, axis=0)
        area = float(cv2.contourArea(cv2.convexHull(centroids.astype(np.float32))))
        report.update(normalized_span=span.tolist(), normalized_hull_area=area,
                      minimum_normalized_span=[.4, .35], minimum_normalized_hull_area=.12)
        if span[0] < .4 or span[1] < .35 or area < .12:
            issues.append('targets_cover_too_little_of_the_display')
    report.update(sufficient=not issues, issues=issues)
    return report


def validate_gaze_records(model:GazeModel,records:list[dict], *, context:dict,session_id:str,
                          extrinsics:CameraExtrinsics|None=None) -> dict:
    """Measure fresh target observations without inventing an acceptance pass.

    Corrupt/conflicting data and an exact reuse of the fitted feature/target
    arrays raise ValueError. Valid but undersampled/unprovenanced input retains
    raw errors with a diagnostic-only status. A changed session label alone
    never establishes independence. Even complete acquisition metadata cannot
    prove a person's fixation or detect every possible partial training reuse.
    """
    if not records:
        raise ValueError('Independent validation requires recorded observations')
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError('Validation requires a nonempty acquisition session_id')
    if session_id == model.metadata.get('session_id'):
        raise ValueError('Do not reuse the model training session for validation')
    if not isinstance(context, dict):
        raise ValueError('Validation requires actual capture context')
    mismatches = model.context_mismatches(context, require_present=True)
    if mismatches:
        raise ValueError('Validation configuration mismatch: ' + ', '.join(mismatches))
    model.validate()
    labels, row_contexts = [], []
    seen_context = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict) or record.get('target_id') is None or not str(record['target_id']).strip():
            raise ValueError(f'Validation record {index} needs a target_id')
        labels.append(str(record['target_id']))
        if 'session_id' in record and record['session_id'] != session_id:
            raise ValueError(f'Validation record {index} conflicts with the acquisition session_id')
        row_context = record.get('context', {})
        if not isinstance(row_context, dict):
            raise ValueError(f'Validation record {index} context must be an object')
        row_contexts.append(row_context)
        for key in (*CONTEXT_KEYS, 'screen_size'):
            if key not in row_context:
                continue
            for observed in (context, seen_context):
                if key in observed and not _same_context_value(row_context[key], observed[key]):
                    raise ValueError(f'Validation record {index} has conflicting context: {key}')
            seen_context[key] = row_context[key]
        row_mismatches = model.context_mismatches({**context, **row_context}, require_present=True)
        if row_mismatches:
            raise ValueError(f'Validation record {index} configuration mismatch: ' + ', '.join(row_mismatches))
    try:
        x = np.asarray([r['features'] for r in records], dtype=float)
        y = np.asarray([r['target'] for r in records], dtype=float)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('Invalid validation feature/target records') from exc
    dimensions = 2 if model.kind == 'screen' else 3
    if (x.shape != (len(records), len(FEATURE_NAMES)) or y.shape != (len(records), dimensions)
            or not np.isfinite(x).all() or not np.isfinite(y).all()):
        raise ValueError('Validation needs finite feature vectors and matching target dimensions')
    if model.kind == 'screen':
        if np.any(y < 0) or np.any(y > 1):
            raise ValueError('Validation screen targets must be normalized within [0,1]')
    else:
        lengths = np.linalg.norm(y, axis=1)
        if np.any(lengths < 1e-6):
            raise ValueError('Validation spatial target directions cannot be zero')
        y = y / lengths[:, None]
    # This matches fit_gaze_model's float feature/normalized-target digest.
    # Target IDs, session labels, timestamps and extra metadata cannot conceal
    # reuse of the same ordered fitting arrays.
    digest = hashlib.sha256(x.tobytes() + y.tobytes()).hexdigest()
    training_digest = model.metadata.get('training_digest')
    digest_available = (isinstance(training_digest, str) and len(training_digest) == 64
                        and all(c in '0123456789abcdef' for c in training_digest.lower()))
    if digest_available and digest == training_digest.lower():
        raise ValueError('Validation reuses the exact model training observations; collect a new session')

    coverage = _gaze_coverage(labels, y, model.kind)
    provenance_issues = []
    if not model.metadata.get('session_id'):
        provenance_issues.append('model_training_session_not_recorded')
    if not digest_available:
        provenance_issues.append('model_training_digest_unavailable')
    row_sessions_complete = all(r.get('session_id') == session_id for r in records)
    if not row_sessions_complete:
        provenance_issues.append('per_record_acquisition_session_missing')
    contexts_complete = all(bool(c) for c in row_contexts)
    if not contexts_complete:
        provenance_issues.append('per_record_tracking_context_missing')
    times_present = all('time_s' in r for r in records)
    timestamps = None
    if any('time_s' in r for r in records):
        try:
            available_times = np.asarray([r['time_s'] for r in records if 'time_s' in r], float)
        except (TypeError, ValueError) as exc:
            raise ValueError('Invalid validation observation timestamps') from exc
        if not np.isfinite(available_times).all() or np.any(np.diff(available_times) <= 0):
            raise ValueError('Validation timestamps must be finite, unique and increasing')
        if times_present:
            timestamps = available_times
    if not times_present:
        provenance_issues.append('acquisition_timestamps_missing')
    settled = True
    for record in records:
        acquisition = record.get('acquisition')
        try:
            sufficient = (isinstance(acquisition, dict)
                          and np.isfinite(float(acquisition.get('settle_seconds', np.nan)))
                          and float(acquisition['settle_seconds']) >= .5
                          and np.isfinite(float(acquisition.get('stable_duration_s', np.nan)))
                          and float(acquisition['stable_duration_s']) >= .1 - 1e-9
                          and acquisition.get('target_assumption') == 'user_instructed_fixation')
        except (KeyError, TypeError, ValueError):
            sufficient = False
        # np.isfinite may short-circuit to numpy.bool_ for malformed metadata.
        # Diagnostic reports must remain strict-JSON serializable on all hosts.
        settled = bool(settled and sufficient)
    if not settled:
        provenance_issues.append('settled_target_acquisition_not_recorded')
    durations = {}
    if timestamps is not None:
        for label in dict.fromkeys(labels):
            target_times = timestamps[np.asarray(labels) == label]
            durations[label] = float(target_times[-1] - target_times[0])
            if durations[label] < .1 - 1e-9:
                coverage['issues'].append('target_observation_duration_below_collector_minimum')
            if len(target_times) > 1 and np.max(np.diff(target_times)) > .25 + 1e-9:
                coverage['issues'].append('target_observations_are_not_contiguous')
            for record in (r for r in records if str(r['target_id']) == label):
                acquisition = record.get('acquisition')
                if not isinstance(acquisition, dict):
                    continue
                try:
                    declared_duration = float(acquisition['stable_duration_s'])
                except (KeyError, TypeError, ValueError):
                    continue
                if np.isfinite(declared_duration) and abs(declared_duration-durations[label]) > 1e-6:
                    provenance_issues.append('acquisition_duration_conflicts_with_observation_timestamps')
        coverage['issues'] = list(dict.fromkeys(coverage['issues']))
        coverage['sufficient'] = not coverage['issues']
    coverage.update(target_observation_duration_s=durations,
                    minimum_target_duration_s=.1, maximum_within_target_gap_s=.25)
    if model.kind == 'screen' and 'screen_size' not in context:
        provenance_issues.append('actual_display_dimensions_not_supplied')
    provenance_issues = list(dict.fromkeys(provenance_issues))

    if model.kind == "spatial" and model.metadata.get("spatial_frame") == "head":
        if not all(record.get("head_rotation") is not None for record in records):
            raise ValueError("Head-frame spatial validation requires head_rotation on every record")
        rotations = np.asarray([record["head_rotation"] for record in records], dtype=float)
        predictions = model.predict_camera_direction(x, rotations)
    else:
        predictions = model.predict(x)
    errors = _error_metrics(predictions, y, model.kind, model.metadata.get('screen_size'))
    outside = [i for i, row in enumerate(x) if model.outside_envelope(row)]
    synthetic = any(bool(c.get('synthetic') or c.get('simulated'))
                    or c.get('backend') == 'synthetic' or c.get('eye_origin_source') == 'synthetic_fixture'
                    for c in [model.metadata, context, *records, *row_contexts])
    independent = coverage['sufficient'] and not provenance_issues
    eligibility_issues = list(coverage['issues']) + list(provenance_issues)
    if outside:
        eligibility_issues.append('observations_outside_calibrated_operating_envelope')
    if synthetic:
        eligibility_issues.append('synthetic_data_cannot_establish_physical_acceptance')
    if not isinstance(context.get('subject_id'), str) or not context['subject_id'].strip():
        eligibility_issues.append('operator_identity_not_recorded')
    report = {
        'session_id': session_id, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'independent_session': independent, 'model_fingerprint': model.fingerprint,
        'sample_count': len(records), 'target_count': len(set(labels)),
        'out_of_envelope_samples': len(outside), 'in_envelope_fraction': (len(records)-len(outside))/len(records),
        'screen_error' if model.kind == 'screen' else 'angular_error': errors,
        'raw_metrics_computed': True, 'coverage': coverage,
        'provenance': {
            'declared_session_differs_from_training': True,
            'per_record_session_consistent': row_sessions_complete,
            'per_record_context_available': contexts_complete,
            'timestamps_present_and_ordered': times_present,
            'settled_acquisition_recorded': settled,
            'training_digest_available': digest_available,
            'validation_digest': digest,
            'exact_training_digest_reused': False if digest_available else None,
            'issues': provenance_issues,
            'limitation': 'An aggregate digest checks exact ordered training-array reuse, not all partial or reordered overlap; capture metadata is supplied evidence, not proof of physical fixation.',
        },
        'independence_basis': 'consistent per-record acquisition metadata, sufficient target coverage and nonreused aggregate training digest' if independent else 'independence not established from the available evidence',
        'synthetic': synthetic, 'measurement_provenance': 'synthetic_software_fixture' if synthetic else 'user_supplied_target_acquisition',
        'measurement_basis': 'user-instructed fixation targets, not an independent ground-truth eye tracker',
        'physical_accuracy_verified': False,
        'acceptance_criteria': None, 'acceptance_passed': None,
        'acceptance_note': 'No user-agreed accuracy, precision, drift or hardware criteria were supplied; these measurements are not an acceptance pass.',
    }
    if model.kind == 'spatial':
        geometry_complete = extrinsics is not None and all('target_world_m' in r and 'eye_origin_camera_m' in r for r in records)
        report['spatial_geometry_complete'] = geometry_complete
        if not geometry_complete:
            eligibility_issues.append('spatial_target_or_eye_origin_geometry_missing')
        if extrinsics is not None:
            extrinsics.validate()
            misses, ranges, world_points = [], [], {}
            behind = 0
            for index, (pred, record) in enumerate(zip(predictions, records)):
                if 'target_world_m' not in record or 'eye_origin_camera_m' not in record:
                    continue
                origin = np.asarray(record['eye_origin_camera_m'], float)
                point = np.asarray(record['target_world_m'], float)
                if origin.shape != (3,) or point.shape != (3,) or not np.isfinite(origin).all() or not np.isfinite(point).all():
                    raise ValueError('Spatial validation geometry must contain finite XYZ vectors')
                label = labels[index]
                if label in world_points and not np.allclose(point, world_points[label], atol=1e-9, rtol=0):
                    raise ValueError(f'Conflicting physical target coordinates within target_id {label}')
                world_points[label] = point
                target = world_to_camera_point(point, extrinsics)
                difference = target - origin
                distance = float(np.linalg.norm(difference))
                if distance < .05 or not np.allclose(difference / distance, y[index], atol=1e-6, rtol=0):
                    raise ValueError('Spatial validation direction conflicts with its measured target and eye origin')
                along = float(np.dot(difference, pred))
                if along <= 0:
                    behind += 1
                    continue
                misses.append(float(np.linalg.norm(difference-along*pred))*1000)
                ranges.append(distance)
            report['targets_behind_predicted_ray'] = behind
            if misses:
                report['target_ray_perpendicular_miss_mm'] = {'median': float(np.median(misses)),
                    'p95': float(np.percentile(misses, 95)), 'max': float(np.max(misses)), 'count': len(misses)}
                report['target_distance_range_m'] = [min(ranges), max(ranges)]
            if world_points:
                points = np.asarray(list(world_points.values()))
                distinct_world = len(np.unique(np.round(points, 6), axis=0))
                rank = int(np.linalg.matrix_rank(points-points.mean(axis=0), tol=1e-6))
                report['coverage']['distinct_physical_targets'] = distinct_world
                report['coverage']['world_target_rank'] = rank
                report['coverage']['volumetric_target_coverage'] = rank == 3
                if distinct_world < 9:
                    issue = 'fewer_than_nine_distinct_physical_targets'
                    report['coverage']['issues'].append(issue)
                    report['coverage']['sufficient'] = False
                    eligibility_issues.append(issue)
                if rank < 3:
                    eligibility_issues.append('physical_targets_do_not_cover_a_three_dimensional_volume')
        report['spatial_limit'] = 'Perpendicular ray miss is not depth fixation accuracy; an approximate eye origin affects these measurements.'
    independent = report['coverage']['sufficient'] and not provenance_issues
    report['independent_session'] = independent
    if not independent:
        report['independence_basis'] = 'independence not established from the available evidence'
    report['validation_status'] = 'independent_measurements_available' if independent else 'diagnostic_only'
    report['acceptance_eligible'] = not eligibility_issues
    report['acceptance_issues'] = list(dict.fromkeys(eligibility_issues))
    report['acceptance_status'] = ('pending_user_agreed_criteria' if report['acceptance_eligible']
                                   else 'synthetic_software_only' if synthetic and independent
                                   else 'not_eligible_for_acceptance')
    return report


def write_checkerboard_svg(path,cols=9,rows=6,square_mm=24.0,margin_mm=15.0):
    """Create an exact-size vector pattern, intended for printing at 100% scale."""
    from pathlib import Path
    chessboard_points(cols,rows,square_mm)
    if margin_mm<10:raise ValueError('Use a margin of at least 10 mm')
    width=(cols+1)*square_mm+2*margin_mm;height=(rows+1)*square_mm+2*margin_mm+12
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}mm" height="{height}mm" viewBox="0 0 {width} {height}">',
           f'<rect width="{width}" height="{height}" fill="white"/>']
    for row in range(rows+1):
        for col in range(cols+1):
            if (row+col)%2==0:
                parts.append(f'<rect x="{margin_mm+col*square_mm}" y="{margin_mm+row*square_mm}" width="{square_mm}" height="{square_mm}" fill="black"/>')
    parts.append(f'<text x="{margin_mm}" y="{height-9}" font-family="sans-serif" font-size="3.3" fill="black">GazeSpace | {cols} x {rows} internal corners | square {square_mm} mm | print 100%, measure after printing</text>')
    parts.append(f'<text x="{margin_mm}" y="{margin_mm-5}" font-family="sans-serif" font-size="3.3" fill="black">TOP / preserve this board orientation for world calibration</text></svg>')
    output=Path(path);output.parent.mkdir(parents=True,exist_ok=True);output.write_text('\n'.join(parts),encoding='utf-8')
    return output
