"""Full instance shutdown releases agents that survive ordinary Worker handoff."""
from types import SimpleNamespace

import pytest

from management_portal import native_agent_lifecycle as lifecycle


@pytest.mark.parametrize('provider', ['dsh', 'opencode'])
def test_native_stop_closes_only_selected_instance_sessions(tmp_path, monkeypatch, provider):
    state = tmp_path / 'selected'
    session = provider + '-' + 'a' * 32
    (state / provider / session).mkdir(parents=True)
    (state / provider / 'unrelated').mkdir()
    (tmp_path / 'other' / provider / session).mkdir(parents=True)
    history = state / provider / session / 'history.jsonl'
    history.write_text('preserve native history')
    monkeypatch.setattr(lifecycle, 'portal_path', lambda name: state)
    closed = []
    client = SimpleNamespace(close_session=lambda key, sid: closed.append((key, sid)))
    monkeypatch.setattr(lifecycle.importlib, 'import_module', lambda name: SimpleNamespace(shared_client=lambda: client))
    result = lifecycle.close_all()
    assert closed == [('', session)]
    assert result == {'closed': [{'provider': provider, 'session_id': session}]}
    assert history.read_text() == 'preserve native history'
    assert (tmp_path / 'other' / provider / session).is_dir()


def test_close_error_is_not_successful_shutdown(tmp_path, monkeypatch):
    (tmp_path / 'dsh' / ('dsh-' + 'a' * 32)).mkdir(parents=True)
    monkeypatch.setattr(lifecycle, 'portal_path', lambda name: tmp_path)
    def fail(*args):
        raise RuntimeError('Native release has not completed')
    monkeypatch.setattr(lifecycle.importlib, 'import_module', lambda name: SimpleNamespace(
        shared_client=lambda: SimpleNamespace(close_session=fail)))
    with pytest.raises(RuntimeError, match='not completed'):
        lifecycle.close_all()


def test_redirected_session_does_not_close_foreign_agent(tmp_path, monkeypatch):
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    state = tmp_path / 'selected'
    (state / 'dsh').mkdir(parents=True)
    (state / 'dsh' / ('dsh-' + 'a' * 32)).symlink_to(foreign, target_is_directory=True)
    monkeypatch.setattr(lifecycle, 'portal_path', lambda name: state)
    monkeypatch.setattr(lifecycle.importlib, 'import_module', lambda name: pytest.fail('Foreign agent accessed'))
    with pytest.raises(ValueError, match='redirected'):
        lifecycle.close_all()
