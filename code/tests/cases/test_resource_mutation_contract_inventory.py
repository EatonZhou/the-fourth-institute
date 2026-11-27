"""The deployed provider inventory exposes the same schemas it enforces.

:module: resources.types
:design: decision_ad3e21094f14
"""
from types import SimpleNamespace

from management_portal.resources import ResourceService, isa
from management_portal.resources.providers import install_mutation_providers


def _field_descriptions(schema):
    """A branch may add a constraint to a field described in its parent."""
    fields, described = set(), set()
    def visit(value):
        if isinstance(value, dict):
            for name, declaration in value.get('properties', {}).items():
                fields.add(name)
                if declaration.get('description'):
                    described.add(name)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(schema)
    return fields - described


def test_all_installed_mutation_providers_publish_actual_contracts(monkeypatch):
    service = ResourceService(
        mutation_provider_loader=install_mutation_providers,
        index_provider=SimpleNamespace(type_counts=lambda: {}, supports_type=lambda _: False),
    )
    monkeypatch.setattr(isa, '_service', lambda: service)
    rows = isa.list_resource_types(None)['types']
    seen = set()
    for row in rows:
        if not row['installed']:
            assert row['contracts'] == {}
            continue
        kind = row['type']
        if kind not in service._mutation_providers.registered_types():
            assert row['contracts'] == {}, kind
            assert not row['registerable'] and not row['updatable'], kind
            continue
        provider = service._mutation_providers.provider_for(kind)
        expected = {name: getattr(provider, name + '_contract')
                    for name in ('register', 'update') if getattr(provider, name) is not None}
        assert row['contracts'] == expected, kind
        detail = isa.get_resource_type(SimpleNamespace(argument={'resource_type': kind}.get))
        assert detail['contracts'] == expected, kind
        for schema in expected.values():
            assert isinstance(schema, dict) and schema.get('description'), kind
            assert not _field_descriptions(schema), (kind, _field_descriptions(schema))
        for capability, operation in [('registerable', 'register'), ('updatable', 'update')]:
            assert not row[capability] or operation in expected, (kind, capability)
        if expected:
            seen.add(kind)
    assert {'decision', 'plan', 'skill', 'knowledge_tags', 'report', 'workbench', 'workspace',
            'device', 'project', 'repo', 'article', 'experiment', 'mailbox_todo'} <= seen
