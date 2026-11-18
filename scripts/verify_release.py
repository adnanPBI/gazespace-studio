"""Reproduce the software release gate in the active installed environment."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='release_evidence')
    parser.add_argument('--basler-emulator',action='store_true')
    args=parser.parse_args()
    project=Path(__file__).resolve().parents[1]
    output=Path(args.output).resolve();output.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy();env.update(QT_QPA_PLATFORM='offscreen',QT_API='pyside6',MPLBACKEND='Agg')
    if args.basler_emulator:env['GAZESPACE_TEST_PYLON_EMULATOR']='1'
    steps=[('dependencies',['-m','pip','check'],{}),
        ('pytest',['-m','pytest','-q','--junitxml='+str(output/'pytest.xml')],{}),
        ('doctor',['-m','gazespace','doctor'],{}),
        ('face_inference',['scripts/verify_face_inference.py','--output',str(output/'mediapipe_inference_photo.json')],{}),
        ('simulation',['-m','gazespace','simulate','--output',str(output/'simulation'),'--seconds','8','--record-video'],{}),
        ('studio_smoke',['scripts/gazespace_studio_entry.py','--smoke-test',str(output/'studio_smoke.json')],{})]
    if args.basler_emulator:
        steps.append(('pypylon_sdk_emulator',['scripts/verify_pypylon_emulator.py','--output',str(output/'pypylon_sdk_emulator.json')],{'PYLON_CAMEMU':'1'}))
    report={'version':importlib.metadata.version('gazespace'),'verified_at_utc':datetime.now(timezone.utc).isoformat(),
        'environment':{'python':platform.python_version(),'platform':platform.platform(),
                       'isolated_environment':sys.prefix!=sys.base_prefix,
                       'packages':{name:importlib.metadata.version(name) for name in ('numpy','opencv-contrib-python','scipy','mediapipe','PySide6','fastapi','pytest')}},
        'status':'running','steps':[],
        'real_hardware_validated':False,'human_gaze_accuracy_validated':False,
        'windows_runtime_validated':False,'client_application_integration_validated':False}
    for name,command,overrides in steps:
        started=time.monotonic()
        completed=subprocess.run([sys.executable,*command],cwd=project,env={**env,**overrides},
                     capture_output=True,text=True,errors='replace',timeout=300)
        (output/(name+'.log')).write_text(completed.stdout+completed.stderr,encoding='utf-8')
        result={'name':name,'status':'passed' if completed.returncode==0 else 'failed',
                'exit_code':completed.returncode,'duration_s':round(time.monotonic()-started,3),
                'command':['python',*command],'log':name+'.log'}
        report['steps'].append(result)
        print(json.dumps(result),flush=True)
        if completed.returncode:
            report['status']='failed'
            (output/'release_verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
            return completed.returncode
    root=ET.parse(output/'pytest.xml').getroot()
    suites=root.findall('testsuite') if root.tag=='testsuites' else [root]
    report['tests']={key:sum(int(s.attrib.get(key,0)) for s in suites) for key in ('tests','failures','errors','skipped')}
    report['tests']['passed']=report['tests']['tests']-sum(report['tests'][k] for k in ('failures','errors','skipped'))
    report['status']='passed'
    report['windows_runtime_validated']=platform.system()=='Windows'
    (output/'release_verification.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'tests':report['tests']},indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
