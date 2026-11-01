from copy import deepcopy
import pytest
from scripts.migrate_instance_registrations import converted_documents


def test_effective_ssh_override_is_preserved_and_reapplication_is_empty():
    original = {'device_test': {'device_id': 'device_test', 'access_methods': {'ssh': {
        'host': 'host-alias', 'config_path': '/stale/config', 'identity_file': '/stale/key', 'user': 'worker'}}}}
    before = deepcopy(original)
    updates = converted_documents(original, {'device_test': {'config_path': '/host/config'}})
    assert updates['device_test']['access_methods']['ssh'] == {
        'host': 'host-alias', 'config_path': '/host/config', 'identity_file': '', 'user': 'worker'}
    assert original == before
    assert converted_documents(updates, {'device_test': {'config_path': '/host/config'}}) == {}


@pytest.mark.parametrize('overrides', [[], {'missing': {}}, {'device_test': {'config_path': 'relative'}},
                                     {'device_test': {'identity_file': 42}}, {'device_test': {'password': 'not-allowed'}}])
def test_invalid_override_aborts_conversion(overrides):
    with pytest.raises(ValueError):
        converted_documents({'device_test': {'access_methods': {'ssh': {'host': 'example'}}}}, overrides)


def test_only_relocated_secret_directory_references_change():
    from pathlib import Path
    from scripts.migrate_instance_registrations import relocated_secret_path
    old, new = Path('/instance/data-roots/secrets'), Path('/instance/secrets')
    assert relocated_secret_path(str(old / 'key'), old, new) == str(new / 'key')
    assert relocated_secret_path(str(old), old, new) == str(new)
    assert relocated_secret_path('/host/accounts/key', old, new) == '/host/accounts/key'
    assert relocated_secret_path(str(old) + '-other/key', old, new) == str(old) + '-other/key'
    with pytest.raises(ValueError, match='noncanonical'):
        relocated_secret_path(str(old) + '/../other/key', old, new)
