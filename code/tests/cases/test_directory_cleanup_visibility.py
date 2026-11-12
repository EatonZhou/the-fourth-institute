from types import SimpleNamespace
import pytest
from management_portal.resources import directory_isa
from management_portal.hatg.contracts import InstructionError


@pytest.mark.parametrize('caller,readable,expected', [
    ('alice', False, ['own']), ('bob', False, None),
    ('bob', True, ['system', 'own']),
])
def test_mixed_cleanup_rows_preserve_initiator_access(monkeypatch, caller, readable, expected):
    rows = [{'cleanup_id': 'system', 'actor': 'directory-retention'},
            {'cleanup_id': 'own', 'actor': 'alice'}]
    monkeypatch.setattr(directory_isa, 'get_directory_cleanup_store',
                        lambda: SimpleNamespace(status=lambda **_: rows))
    def get(*args, **kwargs):
        if not readable:
            raise KeyError('deleted')
        return {}
    invocation = SimpleNamespace(caller=caller, argument=lambda _: 'paper_fixture')
    if expected is None:
        with pytest.raises(InstructionError):
            directory_isa.cleanup_status(invocation, SimpleNamespace(get=get))
    else:
        result = directory_isa.cleanup_status(invocation, SimpleNamespace(get=get))
        assert [r['cleanup_id'] for r in result['items']] == expected
