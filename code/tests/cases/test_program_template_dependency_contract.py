"""Keep review, template publication and budget storage out of their callers."""

import pytest

from tests.cases.test_society_service_layers import _imports


@pytest.mark.parametrize(
    ("source", "dependency"),
    [
        ("lineage_program_components", "effect_script_authoring"),
        ("lineage_state", "effect_script_authoring"),
        ("templates.store", "effect_script_authoring"),
        ("templates.marketplace", "effect_script_authoring"),
        ("templates.marketplace", "templates"),
        ("wire_delivery_budget", "lineage_program_queries"),
    ],
)
def test_owner_does_not_import_its_caller(source, dependency):
    assert dependency not in _imports()[source]
