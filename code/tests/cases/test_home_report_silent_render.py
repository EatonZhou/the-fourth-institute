"""Production silent render through persistent commit; isolated sources, real ffmpeg.

:module: resource.service
:design: decision_e3ced6d30f6b
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest

from management_portal.home_report_persistent_media import PersistentMedia
from management_portal.home_report_media import REQUIRED_FILES, bundle_files
from tests.cases.test_home_report_persistence import reports, RID, TARGET, create


def test_production_silent_render_commits_and_remains_readable_without_audio(reports,monkeypatch):
    from management_portal import home_report_store
    from management_portal.home_report import build_report
    from management_portal.home_report_reasoning import build_reasoning
    from management_portal.media_tools import capabilities
    from tests.cases.test_home_report_reasoning import report as graph
    tools=capabilities()
    assert tools['available'],tools['missing']
    store,_=reports
    snapshot=build_report(TARGET,read=store.read)
    snapshot.update({key:value for key,value in graph().items() if key!='target'})
    snapshot['reasoning']=build_reasoning(snapshot)
    # Keep all production scenes and their real topology; shorten only test
    # narration so this required renderer check does not encode a long talk.
    for step in snapshot['reasoning']['steps']:
        step['speech']='观察。';step['beats']=[{'speech':'观察。'}]
    monkeypatch.setattr(home_report_store,'build_report',lambda *a,**kw:snapshot)
    create(store)
    media=PersistentMedia(store)
    started=time.monotonic()
    media.start(RID,1,'alice',quality='low')
    while time.monotonic()-started < 90:
        result=media.status(RID,1,'alice',quality='low')
        if result['status']!='pending':break
        time.sleep(.05)
    assert result['status']=='ready',result
    assert set(result['assets'])==set(REQUIRED_FILES)
    paths={}
    for name in REQUIRED_FILES:
        path,mime=store.file(RID,1,'low-'+name,'alice')
        assert path.is_file() and path.stat().st_size>0
        assert hashlib.sha256(path.read_bytes()).hexdigest()==result['assets'][name]
        paths[name]=path
    assert not (paths['video.mp4'].parent/'narration.wav').exists()
    with pytest.raises(FileNotFoundError):store.file(RID,1,'low-narration.wav','alice')
    # ffmpeg is the managed dependency; ffprobe is not installed at this site.
    decoded=subprocess.run([tools['ffmpeg'],'-hide_banner','-i',str(paths['video.mp4']),
        '-map','0:v:0','-f','null','-'],check=True,capture_output=True,text=True)
    assert 'Video:' in decoded.stderr and 'Audio:' not in decoded.stderr
    assert result['duration']>0
    reopened=PersistentMedia(store,lambda *a:pytest.fail('reopen must not render'))
    assert reopened.start(RID,1,'alice',quality='low')['assets']==result['assets']
    # Optional audio must not relax required video/caption/graph completeness.
    poster_bytes=paths['poster.png'].read_bytes()
    paths['poster.png'].unlink()
    with pytest.raises(FileNotFoundError,match='poster.png'):bundle_files(paths['video.mp4'].parent)
    paths['poster.png'].write_bytes(poster_bytes)
    out=os.environ.get('REPORT_SILENT_ARTIFACTS')
    if out:
        directory=Path(out);directory.mkdir(parents=True,exist_ok=True)
        for name,path in paths.items():
            if path.is_file():shutil.copyfile(path,directory/name)
        (directory/'result.json').write_text(json.dumps({
            'scope':'production render + persistent ready + formal file adapter; isolated source/ACL; no speech generation',
            'status':result['status'],'assets':result['assets'],'quality':result['quality'],
            'stream_types':['video'],'full_video_decode_exit_code':decoded.returncode,
            'duration':result['duration'],'generation_seconds':result['generation_seconds'],
            'reopen_did_not_render':True,'missing_required_rejected':True,'absent_optional_audio_404':True,
        },ensure_ascii=False,indent=2))
