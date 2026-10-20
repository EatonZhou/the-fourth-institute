"""The authorized projection bridge exposes no historical format prefixes."""
import json

import pytest

from management_portal.workbench_projections.resource_access import dispatch


def test_projection_configuration_ignores_site_mapping_and_keeps_authorization(tmp_path, monkeypatch):
    path = tmp_path / 'site.json'
    path.write_text(json.dumps({'deployment': {'compatibility': {
        'format_namespaces': ['archive.'],
        'environment_aliases': {'PORTAL_TEST_ONLY': ['ARCHIVE_TEST_ONLY']},
        'domain_values': {'private_test_value': 'not-public'},
    }}}))
    monkeypatch.setenv('PORTAL_SITE_CONFIG', str(path))
    calls = []
    assert dispatch(None, 'configuration', {}, 'fixture-user', check_workbench=lambda: calls.append(True)) == {
        'format_namespaces': [],
    }
    assert calls == [True]
    with pytest.raises(Exception, match='不接受参数'):
        dispatch(None, 'configuration', {'key': 'domain_values'}, 'fixture-user', check_workbench=lambda: None)
    with pytest.raises(PermissionError):
        dispatch(None, 'configuration', {'actor': 'another-user'}, 'fixture-user', check_workbench=lambda: None)
    def revoked():
        raise PermissionError('revoked')
    with pytest.raises(PermissionError, match='revoked'):
        dispatch(None, 'configuration', {}, 'fixture-user', check_workbench=revoked)


def test_projection_configuration_is_empty_even_with_malformed_site_mapping(tmp_path, monkeypatch):
    path = tmp_path / 'site.json'
    monkeypatch.setenv('PORTAL_SITE_CONFIG', str(path))
    path.write_text('{}')
    assert dispatch(None, 'configuration', {}, 'fixture-user', check_workbench=lambda: None) == {'format_namespaces': []}
    for value in [['archive.'], ['tfi.'], 'archive.', None, {'private': 'not-public'}]:
        path.write_text(json.dumps({'deployment': {'compatibility': {'format_namespaces': value}}}))
        assert dispatch(None, 'configuration', {}, 'fixture-user', check_workbench=lambda: None) == {'format_namespaces': []}
