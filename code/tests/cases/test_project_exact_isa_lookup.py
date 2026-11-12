"""Live Project identity is independent of asynchronous discovery refresh."""
from types import SimpleNamespace
import pytest
from management_portal import project_registry
from management_portal.resources import service_index, isa
from management_portal.hatg.contracts import InstructionError


def service(monkeypatch, project, allow=True):
    monkeypatch.setattr(project_registry, 'project', lambda identity: project)
    def parse(identity): raise ValueError('named identity')
    def authorize(record, actor):
        if not allow: raise PermissionError('denied')
    return SimpleNamespace(_identity=SimpleNamespace(parse=parse, id_exception_types=('project',)),
        _access=SimpleNamespace(authorize_read=authorize),
        _index=lambda: SimpleNamespace(get_resource=lambda *a, **k: {'resource_type':'project'}))


def test_project_lookup_uses_live_registry_after_rename(monkeypatch):
    live=service(monkeypatch, {'id':'项目-New','description':'new summary','status':'active'})
    monkeypatch.setattr(isa,'_service',lambda:SimpleNamespace(get=lambda identity,actor:service_index.get(live,identity,actor=actor)))
    record=isa._project_record('项目-New','reader')
    assert record['resource_id']=='项目-New' and record['summary']=='new summary'


def test_deleted_project_is_not_restored_from_stale_discovery(monkeypatch):
    live=service(monkeypatch,None)
    with pytest.raises(KeyError):service_index.get(live,'old',actor='reader')


def test_live_project_still_requires_read_permission(monkeypatch):
    live=service(monkeypatch,{'id':'New'},allow=False)
    with pytest.raises(PermissionError):service_index.get(live,'New',actor='reader')


@pytest.mark.parametrize('dry_run', [False, True])
def test_project_rename_waits_for_affected_resource_projection(monkeypatch, dry_run):
    from management_portal.society_service import get_society_service
    import management_portal.society_service as society
    calls=[]
    monkeypatch.setattr(isa, '_project_record',lambda *a:{'resource_id':'Old'})
    monkeypatch.setattr(society,'get_society_service',lambda:SimpleNamespace(project=SimpleNamespace(
        rename_project=lambda *a,**k:{'project_id':'New','path':'C:/projects/New'})))
    def notify(**k):
        from management_portal.resources.changes import ResourceChange
        ResourceChange(kind=k['kind'],path=k['path'],resource_type=k['resource_type'])
        calls.append(k)
        return {'finished':True}
    monkeypatch.setattr(isa,'_service',lambda:SimpleNamespace(notify_source_changed=notify))
    values={'project_id':'Old','new_project_id':'New','dry_run':dry_run}
    result=isa.rename_project(SimpleNamespace(argument=values.get,caller='reader',context=SimpleNamespace(entry='test')))
    assert result['project_id']=='New'
    assert calls == ([] if dry_run else [{'path':'C:/projects/New','resource_type':'project','kind':'moved','wait':True}])
