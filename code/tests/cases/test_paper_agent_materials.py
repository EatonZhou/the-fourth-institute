from types import SimpleNamespace
import pytest
from management_portal import resources,paper_store
from management_portal.paper_agent_materials import prepare_snapshot,path_instruction,validate_path_result,READ_FAILURE

@pytest.fixture
def env(monkeypatch,tmp_path):
    seen=[]
    monkeypatch.setattr(paper_store,'DATA_ROOT',tmp_path)
    monkeypatch.setattr(resources,'get_resource_service',lambda:SimpleNamespace(get=lambda rid,**kw:seen.append((rid,kw['actor']))))
    return tmp_path,seen

def prepare(**kw):
    return prepare_snapshot(actor='reader',paper_id='paper-1',job_id='deep-read-1',text='section 2 paper text',metadata={'title':'title'},**kw)

def test_snapshot_is_job_retained_and_reused(env):
    a=prepare();b=prepare_snapshot(actor='reader',paper_id='paper-1',job_id='deep-read-1',text='new text',metadata={})
    assert a==b and len(env[1])==2
    from pathlib import Path
    assert Path(a.text_path).read_text()=='section 2 paper text'
    assert Path(a.text_path).stat().st_mode & 0o222 == 0

def test_authorization_precedes_file_writes(env,monkeypatch):
    def denied(*a,**kw):raise PermissionError('denied')
    monkeypatch.setattr(resources,'get_resource_service',lambda:SimpleNamespace(get=denied))
    with pytest.raises(PermissionError):prepare()
    assert not (env[0]/'job_materials').exists()

def test_identity_and_integrity_fail_closed(env):
    from pathlib import Path
    a=prepare();path=Path(a.text_path);path.chmod(0o600);path.write_text('tamper')
    with pytest.raises(ValueError,match='integrity'):prepare()

def test_symlink_and_traversal_rejected(env):
    root=env[0]/'job_materials';root.mkdir();(root/'deep-read-1').symlink_to(env[0],target_is_directory=True)
    with pytest.raises(PermissionError):prepare()
    with pytest.raises(ValueError):prepare_snapshot(actor='reader',paper_id='paper-1',job_id='../x',text='text',metadata={})

def test_short_prompt_failure_marker_and_evidence(env):
    a=prepare();p=path_instruction(a,title='title',task='深读',output_protocol='## 方法\n## 证据')
    assert a.text_path in p and 'section 2 paper text' not in p
    with pytest.raises(ValueError,match='无法读取'):validate_path_result(READ_FAILURE)
    with pytest.raises(ValueError,match='chapter/page'):validate_path_result('## Evidence\nNo source location')
    validate_path_result('Evidence from Section 2 and 第3页')
