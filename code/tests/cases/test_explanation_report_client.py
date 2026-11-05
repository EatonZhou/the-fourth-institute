import pytest

from scripts.register_explanation_report import register


def test_registration_only_sends_fixed_source_and_uses_server_reference():
    calls = []
    response = {'reference': {'view': 'report', 'target': {
        'kind': 'report', 'id': 'report_0123456789ab', 'revision': 2}}}
    result = register('workspace_0123456789ab/file/lesson/manifest.json', 'a'*64,
        'resident_0123456789ab', call=lambda *args: calls.append(args) or response)
    assert result is response
    assert calls == [('resident_0123456789ab', {
        'address': 'workspace_0123456789ab/file/lesson/manifest.json', 'sha256': 'a'*64})]


@pytest.mark.parametrize('target', [{}, {'kind': 'report', 'id': 'workspace_0123456789ab', 'revision': 1},
    {'kind': 'report', 'id': 'report_0123456789ab', 'revision': True},
    {'kind': 'report', 'id': 'report_0123456789ab', 'revision': 0}])
def test_registration_never_substitutes_workspace_or_invents_revision(target):
    with pytest.raises(ValueError, match='report_ID'):
        register('source', 'sha', 'actor', call=lambda *args: {'reference': {'view': 'report', 'target': target}})
