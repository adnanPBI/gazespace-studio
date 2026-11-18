import csv
import json
from pathlib import Path
import numpy as np
import pytest

from gazespace.sessions import SessionRecorder, load_session, list_sessions, export_csv, SessionReplay
from gazespace.analytics import (analyze_session, precision_metrics, compare_anchors,
                                AreaOfInterest, export_report, heatmap_svg)


class Clock:
    def __init__(self): self.now=100.
    def __call__(self): return self.now
    def step(self, delta): self.now += delta


def row(stamp, xy=(25.,25.), valid=True, **extra):
    return {'timestamp_s':float(stamp), 'valid':valid, 'reason':'screen_gaze_estimated' if valid else 'blink',
            'quality':.9 if valid else 0., 'source':'synthetic', 'processing_ms':4.,
            'gaze_screen_px':list(xy) if xy is not None else None,
            'gaze_screen_normalized':[xy[0]/100,xy[1]/100] if xy is not None else None,
            **extra}


def test_record_replay_export_roundtrip_keeps_source_and_session_time(tmp_path):
    clock=Clock()
    recorder=SessionRecorder(tmp_path, {'provenance':'synthetic','screen_size':[101,101]}, clock=clock)
    for t in (10.,10.1,0.1,0.2):  # reconnect may restart source clock
        clock.step(.1)
        recorder.append(row(t))
    recorder.event('camera_reconnected', {'serial':'test'})
    path=recorder.close()
    assert recorder.close()==path
    assert not (path/'camera.avi').exists()
    data=load_session(path)
    assert len(data.records)==4
    assert data.records[2]['timestamp_s']==.1
    assert data.manifest['provenance']=='synthetic'
    assert data.manifest['physical_accuracy_verified'] is False
    assert list_sessions(tmp_path)[0]['path']==str(path)
    replay=SessionReplay(data)
    assert replay.current()['timestamp_s']==10
    assert replay.seek(.21)['timestamp_s']==.1
    assert replay.current()['replay'] is True
    assert replay.advance(100.)['timestamp_s']==.2
    output=export_csv(data,tmp_path/'export.csv')
    records=list(csv.DictReader(output.open()))
    assert len(records)==4
    assert records[0]['gaze_x_px']=='25.0'
    assert float(records[2]['session_time_s'])>float(records[1]['session_time_s'])
    with pytest.raises(RuntimeError):recorder.append(row(1))


def test_recording_rejects_nonfinite_data_and_does_not_forge_rows(tmp_path):
    with SessionRecorder(tmp_path) as recorder:
        with pytest.raises(ValueError,match='finite'):
            recorder.append(row(float('nan')))
        with pytest.raises(ValueError,match='coordinate'):
            recorder.append(row(1,gaze_screen_px=['bad',4]))
        recorder.append(row(1,valid=False))
    data=load_session(recorder.path)
    assert len(data.records)==1
    assert data.records[0]['valid'] is False


def test_optional_camera_video_is_a_real_file_when_selected(tmp_path):
    import cv2
    clock=Clock()
    with SessionRecorder(tmp_path, {'provenance':'synthetic'}, record_video=True, fps=20,clock=clock) as recorder:
        for i in range(8):
            clock.step(.05)
            image=np.full((120,160,3),i*20,np.uint8)
            recorder.append(row(i*.05),image)
    data=load_session(recorder.path)
    assert data.manifest['video_frames']==8
    path=recorder.path/data.manifest['video_file']
    cap=cv2.VideoCapture(str(path))
    count=0
    while True:
        ok,frame=cap.read()
        if not ok:break
        assert frame.shape==(120,160,3)
        count+=1
    cap.release()
    assert count==8


def test_strict_load_rejects_truncated_and_path_escape_sessions(tmp_path):
    with SessionRecorder(tmp_path) as recorder:
        recorder.append(row(1))
    gaze=recorder.path/'gaze.jsonl'
    gaze.write_text(gaze.read_text()+'{"timestamp_s":')
    with pytest.raises(ValueError,match='line 2'):load_session(recorder.path)
    manifest=recorder.path/'manifest.json'
    content=json.loads(manifest.read_text())
    content['records_file']='../gaze.jsonl'
    manifest.write_text(json.dumps(content))
    with pytest.raises(ValueError,match='immediate'):load_session(recorder.path)


def test_session_metadata_report_html_escapes_content_and_contains_heatmap(tmp_path):
    clock=Clock()
    with SessionRecorder(tmp_path, {'provenance':'<script>bad()</script>','screen_size':[101,101]},clock=clock) as recorder:
        for i in range(12):
            clock.step(.05)
            recorder.append(row(i*.05,xy=(25.+i%2,25.)))
    paths=export_report(recorder.path,tmp_path/'report',aois=[{'name':'<svg>unsafe','rect':(0,0,.5,.5)}])
    report=json.loads(paths['json'].read_text())
    assert report['summary']['frames']==12
    assert len(report['fixations'])==1
    assert report['aois'][0]['dwell_s']==pytest.approx(.55)
    html=paths['html'].read_text()
    assert '&lt;script&gt;bad()&lt;/script&gt;' in html
    assert '<script>bad()' not in html
    assert '<svg xmlns=' in html
    assert 'report.json' in html and paths['csv'].exists()


def test_gap_and_invalid_data_do_not_accrue_dwell():
    times=[0,.05,.1,.15,.2,10.,10.05]
    records=[row(t,valid=(t!=.1)) for t in times]
    report=analyze_session(records,screen_size=(101,101),aois=[AreaOfInterest('A',(0,0,.5,.5))])
    assert report['summary']['valid_screen_dwell_s']==pytest.approx(.15)
    assert report['summary']['timestamp_gap_count']==1
    assert report['summary']['excluded_gap_duration_s']==pytest.approx(9.8)
    assert report['aois'][0]['dwell_s']==pytest.approx(.15)
    assert report['aois'][0]['entries']==3
    assert report['aois'][0]['longest_contiguous_dwell_s']==pytest.approx(.05)


def test_ring_precision_is_not_zero_radial_standard_deviation():
    points=np.array([[10.,0.],[0.,10.],[-10.,0.],[0.,-10.]])
    metrics=precision_metrics(points)
    assert metrics['radial_rms']==pytest.approx(10.)
    assert metrics['std_xy']==pytest.approx([np.sqrt(50),np.sqrt(50)])
    assert metrics['sample_to_sample_rms']==pytest.approx(np.sqrt(200))


def test_stale_coordinate_is_excluded_and_out_of_screen_not_clamped():
    records=[row(0),row(.05,stale=True),row(.1,xy=(150,25)),row(.15,reason='stale_capture')]
    report=analyze_session(records,screen_size=(101,101))
    assert report['summary']['stale_frames']==2
    assert report['summary']['screen_frames']==1
    assert report['summary']['valid_screen_dwell_s']==0


def test_anchor_report_threshold_does_not_modify_model():
    first=np.array([[0,0],[1,0],[-1,0],[0,0]],float)+[100,150]
    repeat=first+[40,0]
    report=compare_anchors(first,repeat,threshold_px=30,target_px=[100,150])
    assert report['drift_px']==pytest.approx(40)
    assert report['outcome']=='recalibration_recommended'
    assert report['automatic_correction_applied'] is False
    assert report['first']['radial_rms']>0


def test_empty_session_and_incompatible_replay_fail_safely():
    report=analyze_session([],screen_size=(101,101))
    assert report['summary']['frames']==0
    assert report['summary']['valid_fraction']==0
    assert report['fixations']==[]
    assert 'No calibrated screen coordinates' in heatmap_svg(report)
    assert SessionReplay([]).current() is None
    with pytest.raises(ValueError,match='ordered'):SessionReplay([row(2),row(1)])
    with pytest.raises(ValueError):AreaOfInterest('outside',(.8,0,.5,.5))
    with pytest.raises(ValueError):analyze_session([row(2),row(1)])


def test_surface_dwell_uses_metric_coordinates_and_independent_aoi(tmp_path):
    records=[row(t,xy=None,surface_name='bench',surface_xy_m=[.25,.25]) for t in [0,.05,.1,.15]]
    report=analyze_session(records,aois=[AreaOfInterest('inspection',(0,0,.5,.5),'bench')],
                           surface_sizes={'bench':(1.,1.)})
    assert report['surfaces']['bench']['dwell_s']==pytest.approx(.15)
    assert report['aois'][0]['dwell_s']==pytest.approx(.15)
    assert report['summary']['screen_frames']==0


def test_csv_neutralizes_user_controlled_formula_text(tmp_path):
    with SessionRecorder(tmp_path) as recorder:
        recorder.append(row(1,reason='=HYPERLINK("bad")'))
    dest=export_csv(recorder.path,tmp_path/'safe.csv')
    data=list(csv.DictReader(dest.open()))
    assert data[0]['reason'].startswith("'=")


def test_replay_blanks_gaze_between_widely_separated_frames():
    replay=SessionReplay([row(0),row(10)])
    assert replay.seek(.1)['valid'] is True
    stale=replay.seek(5.)
    assert stale['valid'] is False
    assert stale['gaze_screen_px'] is None
    assert stale['reason']=='replay_gap_no_fresh_sample'
    assert replay.seek(10.)['valid'] is True
