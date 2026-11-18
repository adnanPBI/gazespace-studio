"""Native interaction checks with explicitly generated camera fixtures."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import time
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication
from gazespace.capture import SyntheticEyeSource
from gazespace.vision import GazeTracker
from gazespace.types import Frame
from gazespace.ui.main_window import MainWindow
from gazespace.ui.calibration_dialogs import TargetSessionDialog
from gazespace.demo import build_demo_profile,SyntheticDemoEngine


def pump(app,condition,timeout=10):
    started=time.monotonic()
    while time.monotonic()-started<timeout:
        app.processEvents()
        if condition():return
        time.sleep(.01)
    raise AssertionError('Native application did not reach expected state')


@pytest.fixture
def desktop(tmp_path):
    app=QApplication.instance() or QApplication([])
    w=MainWindow(workspace=tmp_path,interactive_errors=False)
    errors=[];w.error_reported.connect(errors.append)
    w.show();app.processEvents()
    yield app,w,errors
    w.stop_tracking();pump(app,lambda:not w._jobs);w.close();app.processEvents()


def test_navigation_and_surface_editor_validation(desktop):
    app,w,errors=desktop
    assert w.pages.count()==7
    for i,button in enumerate(w.nav_buttons):
        button.click();app.processEvents()
        assert w.pages.currentIndex()==i
        assert button.fontMetrics().horizontalAdvance(button.text())<button.width()-18
    w.add_surface()
    assert tuple(w.surface_data()[0]['pixel_size'])==(1920,1080)
    w.surfaces_table.item(0,3).setText('1,0,0')
    with pytest.raises(ValueError,match='orthogonal|parallel'):
        w.surface_data()
    assert errors==[]


def test_fullscreen_collector_uses_current_targets():
    app=QApplication.instance() or QApplication([])
    source=SyntheticEyeSource(realtime=False)
    tracker=GazeTracker(mode='ir-roi',roi_normalized=source.roi_normalized)
    clock=[1.0];targets=[('first',(.2,.25)),('second',(.8,.75))];holder={};payloads=[]
    def snapshot():
        dlg=holder['dialog'];xy=targets[min(dlg.index,1)][1];clock[0]+=.05
        image=source.render_target(*xy);ob=tracker.process(image,clock[0])
        return {'state':'running','observation':ob,'frame':Frame(image,clock[0])}
    dialog=TargetSessionDialog(snapshot,targets,screen_size=(1000,700),
                              collector_options={'min_samples':4,'settle_seconds':.5,'min_duration_s':.1})
    holder['dialog']=dialog;dialog.samples_ready.connect(payloads.append)
    dialog.show();dialog.collect();pump(app,lambda:dialog.index==1)
    assert len(dialog.records)>=4
    first=np.mean([r['features'][:4] for r in dialog.records],axis=0)
    dialog.collect();pump(app,lambda:bool(payloads))
    records=payloads[0]['records'];second=[r for r in records if r['target_id']=='second']
    assert len(second)>=4 and second[0]['target']==pytest.approx([.8,.75])
    assert np.linalg.norm(np.mean([r['features'][:4] for r in second],axis=0)-first)>.2
    tracker.close();source.close()


def test_collector_disconnect_does_not_stick():
    app=QApplication.instance() or QApplication([])
    dialog=TargetSessionDialog(lambda:{'state':'fault','observation':None},[('center',(.5,.5))],screen_size=(800,600))
    dialog.show();dialog.collect();dialog.wall_started=time.monotonic()-20;dialog.tick()
    assert not dialog._active and 'retry' in dialog.status_text
    dialog.close();app.processEvents()


def test_synthetic_3d_engine_rejects_real_frame(tmp_path):
    path=tmp_path/'synthetic.json';profile=build_demo_profile(path)
    assert profile['synthetic']
    source=SyntheticEyeSource(realtime=False);engine=SyntheticDemoEngine.from_profile(path)
    try:
        frame=source.read();result=engine.process_frame(frame)
        assert result.valid and result.screen_valid and result.spatial_valid
        assert result.surface_name=='Virtual display'
        assert result.eye_origin_source=='synthetic_fixture'
        assert result.surface_xy_m is not None
        assert np.linalg.norm(result.ray_direction_world)==pytest.approx(1)
        with pytest.raises(ValueError,match='real camera frame'):
            engine.process_frame(Frame(frame.image,frame.timestamp_s+1))
    finally:source.close();engine.close()
