"""Process identity fields and serialized formats have exact canonical names."""
import pytest
from management_common.product_environment import read_product_env, mirror_product_env
from management_common.format_tags import schema_matches


def test_identity_fields_are_independent():
    assert read_product_env('PORTAL_RESIDENT_ID', environ={'PORTAL_LINEAGE_ID': 'lineage'}) is None
    assert read_product_env('PORTAL_RESIDENT_ID', environ={'PORTAL_RESIDENT_ID': 'resident'}) == 'resident'
    assert read_product_env('PORTAL_RESIDENT_ID', 'default', environ={'PORTAL_RESIDENT_ID': ''}) == ''
    assert read_product_env('PORTAL_RESIDENT_ID', 'default', environ={}) == 'default'


def test_injection_emits_only_selected_field():
    values = {'PORTAL_LINEAGE_ID': 'lineage'}
    mirror_product_env(values, 'PORTAL_RESIDENT_ID', 'resident')
    assert values == {'PORTAL_LINEAGE_ID': 'lineage', 'PORTAL_RESIDENT_ID': 'resident'}
    with pytest.raises(TypeError):
        mirror_product_env(values, 'PORTAL_RESIDENT_ID', 3)
    with pytest.raises(ValueError):
        read_product_env('UNKNOWN_ID', environ={})


@pytest.mark.parametrize('actual', ['tfi.record.v1', 'other.record.v2', None, 2, {}])
def test_format_requires_exact_namespace_and_version(actual):
    assert not schema_matches(actual, 'tfi.record.v2')
    assert schema_matches('tfi.record.v2', 'tfi.record.v2')
