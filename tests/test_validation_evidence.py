"""Evidence eligibility checks with explicit mathematical test fixtures."""
from copy import deepcopy

import numpy as np
import pytest

from gazespace.calibration import fit_gaze_model
from gazespace.types import CameraExtrinsics
from gazespace.validation import validate_gaze_records


def fixture(kind='screen', *, synthetic=False):
    context = {'subject_id': 'person-A', 'camera_id': '0',
               'backend': 'synthetic' if synthetic else 'dshow'}
    rows = []
    if kind == 'screen':
        targets = [(x, y) for y in (.2, .5, .8) for x in (.1, .5, .9)]
        context['screen_size'] = [1920, 1080]
    else:
        targets = [(x, y, z) for z in (1., 1.3) for y in (-.15, .15) for x in (-.2, 0., .2)]
    for index, target in enumerate(targets):
        for repeat in range(4):
            x, y = target[:2]
            row = {'features': [.3+.4*x, .3+.2*y, .3+.4*x, .3+.2*y, 0, 0, 0, 0, 0, 0],
                   'target_id': str(index), 'session_id': 'training-session',
                   'time_s': 10+index+repeat*.04, 'quality': .9,
                   'context': deepcopy(context),
                   'acquisition': {'settle_seconds': .6, 'stable_duration_s': .12,
                                   'target_assumption': 'user_instructed_fixation'}}
            if kind == 'screen':
                row['target'] = list(target)
            else:
                origin = np.array([0., 0., .6])
                direction = np.asarray(target)-origin
                row.update(target=(direction/np.linalg.norm(direction)).tolist(),
                           target_world_m=list(target), eye_origin_camera_m=origin.tolist())
            rows.append(row)
    model = fit_gaze_model(rows, kind=kind, context=context, session_id='training-session',
                           screen_size=(1920, 1080) if kind == 'screen' else None,
                           do_group_validation=False)
    # This supplies expected static feature version/head conventions in these
    # mathematical fixtures; production callers use their observed tracker context.
    current = {**model.metadata, **context}
    fresh = deepcopy(rows)
    for index, row in enumerate(fresh):
        row['session_id'] = 'fresh-session'
        row['time_s'] += 100
        # Different acquired feature measurements, not merely renamed labels.
        row['features'][0] += (index % 4 + 1) * .0002
        row['features'][2] += (index % 4 + 1) * .0002
    return model, rows, fresh, current


def test_renamed_exact_training_arrays_are_rejected():
    model, training, _, context = fixture()
    reused = deepcopy(training)
    for row in reused:
        row['session_id'] = 'renamed-session'
        row['target_id'] = 'renamed-' + row['target_id']
        row['time_s'] += 500
    with pytest.raises(ValueError, match='exact model training observations'):
        validate_gaze_records(model, reused, context=context, session_id='renamed-session')


def test_spatial_training_digest_uses_same_normalization_as_fit():
    model, rows, _, context = fixture('spatial')
    for row in rows:
        row['session_id'] = 'renamed-session'
    with pytest.raises(ValueError, match='exact model training observations'):
        validate_gaze_records(model, rows, context=context, session_id='renamed-session')


def test_conflicting_per_record_subject_is_rejected():
    model, _, fresh, context = fixture()
    fresh[-1]['context']['subject_id'] = 'person-B'
    with pytest.raises(ValueError, match='conflicting context: subject_id'):
        validate_gaze_records(model, fresh, context=context, session_id='fresh-session')


def test_row_context_cannot_change_an_unbound_capture_setting():
    model, _, fresh, context = fixture()
    fresh[0]['context']['mirror'] = False
    fresh[1]['context']['mirror'] = True
    with pytest.raises(ValueError, match='conflicting context: mirror'):
        validate_gaze_records(model, fresh, context=context, session_id='fresh-session')


def test_per_record_session_cannot_disagree_with_validation_session():
    model, _, fresh, context = fixture()
    fresh[4]['session_id'] = 'training-session'
    with pytest.raises(ValueError, match='acquisition session_id'):
        validate_gaze_records(model, fresh, context=context, session_id='fresh-session')


def test_one_sample_retains_metrics_but_is_diagnostic_only():
    model, _, fresh, context = fixture()
    report = validate_gaze_records(model, fresh[:1], context=context, session_id='fresh-session')
    assert report['raw_metrics_computed'] is True
    assert report['screen_error']['count'] == 1
    assert report['independent_session'] is False
    assert report['validation_status'] == 'diagnostic_only'
    assert report['acceptance_eligible'] is False
    assert report['acceptance_passed'] is None
    assert 'fewer_than_nine_distinct_targets' in report['coverage']['issues']
    assert 'fewer_than_four_samples_per_target' in report['coverage']['issues']


def test_duplicate_target_geometry_cannot_gain_coverage_by_relabeling():
    model, _, fresh, context = fixture()
    for row in fresh:
        row['target'] = [.5, .5]
    report = validate_gaze_records(model, fresh, context=context, session_id='fresh-session')
    assert report['target_count'] == 9
    assert report['coverage']['distinct_target_geometry'] == 1
    assert not report['independent_session'] and not report['acceptance_eligible']


def test_complete_fresh_measurement_has_no_automatic_accuracy_pass():
    model, _, fresh, context = fixture()
    report = validate_gaze_records(model, fresh, context=context, session_id='fresh-session')
    assert report['independent_session'] is True
    assert report['coverage']['sufficient'] is True
    assert report['provenance']['issues'] == []
    assert report['validation_status'] == 'independent_measurements_available'
    assert report['acceptance_eligible'] is True
    assert report['acceptance_status'] == 'pending_user_agreed_criteria'
    assert report['acceptance_criteria'] is None and report['acceptance_passed'] is None
    assert report['physical_accuracy_verified'] is False


def test_missing_acquisition_metadata_never_establishes_independence_from_id_alone():
    model, _, fresh, context = fixture()
    for row in fresh:
        for key in ('session_id', 'time_s', 'acquisition', 'context'):
            row.pop(key)
    report = validate_gaze_records(model, fresh, context=context, session_id='fresh-session')
    assert report['screen_error']['count'] == 36
    assert not report['independent_session']
    assert report['validation_status'] == 'diagnostic_only'
    assert 'per_record_acquisition_session_missing' in report['provenance']['issues']


def test_malformed_settling_metadata_keeps_diagnostic_report_json_safe():
    import json
    model, _, fresh, context = fixture()
    fresh[0]['acquisition']['settle_seconds'] = float('nan')
    report = validate_gaze_records(model, fresh, context=context, session_id='fresh-session')
    assert report['provenance']['settled_acquisition_recorded'] is False
    assert report['validation_status'] == 'diagnostic_only'
    assert not report['acceptance_eligible']
    json.dumps(report, allow_nan=False)


def test_synthetic_provenance_survives_valid_fresh_measurement():
    model, _, fresh, context = fixture(synthetic=True)
    report = validate_gaze_records(model, fresh, context=context, session_id='fresh-session')
    assert report['independent_session'] is True
    assert report['synthetic'] is True
    assert report['measurement_provenance'] == 'synthetic_software_fixture'
    assert report['acceptance_status'] == 'synthetic_software_only'
    assert not report['acceptance_eligible']
    assert report['physical_accuracy_verified'] is False


def test_duplicate_observation_timestamps_are_rejected():
    model, _, fresh, context = fixture()
    fresh[1]['time_s'] = fresh[0]['time_s']
    with pytest.raises(ValueError, match='unique and increasing'):
        validate_gaze_records(model, fresh, context=context, session_id='fresh-session')


def test_missing_training_digest_leaves_metrics_unverified():
    model, _, fresh, context = fixture()
    del model.metadata['training_digest']
    report = validate_gaze_records(model, fresh, context=context, session_id='fresh-session')
    assert report['screen_error']['count'] == len(fresh)
    assert not report['independent_session']
    assert report['provenance']['exact_training_digest_reused'] is None
    assert 'model_training_digest_unavailable' in report['provenance']['issues']


@pytest.mark.parametrize('field,value', [('features', [float('nan')]*10), ('target', [.2, .4, .8])])
def test_malformed_numeric_records_cannot_produce_accepted_metrics(field, value):
    model, _, fresh, context = fixture()
    fresh[0][field] = value
    with pytest.raises(ValueError, match='feature|target|dimensions'):
        validate_gaze_records(model, fresh, context=context, session_id='fresh-session')


def test_spatial_fresh_geometry_reports_ray_miss_without_claiming_depth_accuracy():
    model, _, fresh, context = fixture('spatial')
    report = validate_gaze_records(model, fresh, context=context, session_id='fresh-session',
                                   extrinsics=CameraExtrinsics(np.eye(3), np.zeros(3)))
    assert report['independent_session'] is True
    assert report['coverage']['volumetric_target_coverage'] is True
    assert report['spatial_geometry_complete'] is True
    assert report['target_ray_perpendicular_miss_mm']['count'] == len(fresh)
    assert report['acceptance_passed'] is None


def test_spatial_direction_cannot_disagree_with_its_physical_target():
    model, _, fresh, context = fixture('spatial')
    fresh[0]['target_world_m'][0] += .2
    with pytest.raises(ValueError, match='direction conflicts'):
        validate_gaze_records(model, fresh, context=context, session_id='fresh-session',
                               extrinsics=CameraExtrinsics(np.eye(3), np.zeros(3)))
