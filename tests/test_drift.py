import numpy as np
import pytest
from gazespace.workflows import evaluate_drift

class MockModel:
    def predict(self, x):
        return np.atleast_2d(x)[:,:2]

def test_detects_anchored_drift():
    data=[]
    for name,pos,t in (("anchor_initial",(.5,.5),1),("anchor_repeat",(.55,.5),123)):
        for k in range(10):
            data.append({"features":[*pos]+[0]*8,"target_id":name,"time_s":t+k*.01})
    r=evaluate_drift(data,MockModel(),(1000,600))
    assert r["drift_px"]==pytest.approx(49.95,abs=.001)
    assert r["elapsed_s"]>120
    assert r["first_anchor_error_px"]==0

def test_drift_require_two_phases():
    with pytest.raises(ValueError):evaluate_drift([{"features":[.5,.5]+[0]*8,"target_id":"anchor_initial"}],MockModel(),(1000,600))


def test_zero_drift_at_wrong_baseline_cannot_claim_selected_threshold_or_acceptance():
    data=[]
    for name, start in (("anchor_initial",1.),("anchor_repeat",10.)):
        for k in range(4):
            data.append({"features":[.8,.8]+[0]*8,"target_id":name,
                         "time_s":start+k*.04,"context":{"backend":"synthetic"}})
    result=evaluate_drift(data,MockModel(),(1001,601))
    assert result['drift_px']==0
    assert result['first_anchor_error_px']>300
    assert result['threshold_px'] is None
    assert result['outcome']=='measurement_only_no_agreed_criteria'
    assert result['acceptance_passed'] is None and result['acceptance_criteria'] is None
    assert result['acceptance_eligible'] is False
    assert result['acceptance_status']=='synthetic_software_only'
    assert result['physical_accuracy_verified'] is False


def test_reversed_drift_phase_timestamps_are_rejected():
    data=[]
    for name, start in (("anchor_initial",10.),("anchor_repeat",1.)):
        for k in range(4):
            data.append({"features":[.5,.5]+[0]*8,"target_id":name,"time_s":start+k*.04})
    with pytest.raises(ValueError,match='increasing'):
        evaluate_drift(data,MockModel(),(1000,600))
