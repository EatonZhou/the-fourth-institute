"""Canonical process names are independent of historical site aliases."""
import pytest

from management_common.product_environment import read_product_env, mirror_product_env


@pytest.mark.parametrize('suffix', [
    'RESIDENT_ID', 'LINEAGE_ID', 'EXECUTION_ID', 'RESIDENT_EXECUTION_ID',
    'EXECUTION_CONTEXT_FILE', 'URL', 'ROOT', 'PYTHON', 'GIT_CREDENTIAL_FILE',
])
def test_only_canonical_environment_is_read(suffix):
    name, old = 'PORTAL_' + suffix, 'ARCHIVE_' + suffix
    assert read_product_env(name, 'absent', environ={old: 'old'}) == 'absent'
    assert read_product_env(name, environ={name: 'new', old: 'other'}) == 'new'
    assert read_product_env(name, environ={name: '', old: 'old'}) == ''


def test_owner_emits_only_canonical_name():
    values = {'UNRELATED': 'keep'}
    mirror_product_env(values, 'PORTAL_RESIDENT_ID', 'resident_example')
    assert values == {'UNRELATED': 'keep', 'PORTAL_RESIDENT_ID': 'resident_example'}


def test_unknown_name_and_non_string_injection_are_rejected():
    with pytest.raises(ValueError):
        read_product_env('PORTAL_DATABASE_URL', environ={})
    with pytest.raises(TypeError):
        mirror_product_env({}, 'PORTAL_RESIDENT_ID', None)
