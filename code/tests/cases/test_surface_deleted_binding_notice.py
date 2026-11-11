from management_portal.workbench_projections import native
from management_portal import isa_client
from management_portal.hatg.contracts import InstructionError
import pytest

@pytest.mark.parametrize('code,expected', [('instruction.not_found',False),('instruction.forbidden',None),('',True)])
def test_only_missing_surface_is_ignored(monkeypatch,code,expected):
    monkeypatch.setattr(native,'bound_lineage_graph',lambda *a:{})
    monkeypatch.setattr(native,'members',lambda *a:{})
    def lookup(*a,**kw):
        if code:raise InstructionError(code,'failure')
        return {'resident':{'resident_id':'gone'}}
    monkeypatch.setattr(isa_client,'call',lookup)
    app={'bindings':{'main':{'residents':{'surface-chat:alice:paper:old':'gone','paper:deep-read':'required'}}}}
    if expected is None:
        with pytest.raises(InstructionError):native.with_binding_status(app,'alice')
    else:
        result=native.with_binding_status(app,'alice')
        assert len(result['binding_issues'])==(2 if expected else 1)
        assert any(i['role']=='paper:deep-read' for i in result['binding_issues'])
        assert 'surface-chat:alice:paper:old' in app['bindings']['main']['residents']
