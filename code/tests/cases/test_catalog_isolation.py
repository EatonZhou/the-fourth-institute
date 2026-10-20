import json
from pathlib import Path

import pytest

from management_portal.test_framework.catalog import CatalogRepository
from management_portal.test_framework.catalog_source import derived_case_id
from management_portal.test_framework.models import CatalogError

SOURCE = 'tests/cases/test_sample.py'
HEALTHY = derived_case_id(f'{SOURCE}::test_healthy')


def explicit_case(number, name):
    """A spec_design_cases.json case claiming test ``name`` of the sample source."""
    return {
        'id': f'test_{number:012x}', 'key': f'isolation.{number}', 'title': f'隔离用例 {number}',
        'description': '验证目录隔离。', 'importance': 'P1', 'module': 'operations.test-framework',
        'aspects': ['functional'], 'is_design_point': False, 'design_refs': [], 'lifecycle': 'active',
        'implementations': [{'id': f'implementation-{number}', 'entrypoint': f'{SOURCE}::{name}',
                             'source': SOURCE, 'line': 1, 'runner': 'pytest', 'profiles': []}],
    }


def write_catalog(root: Path, cases) -> None:
    cases_root = root / 'tests/cases'
    cases_root.mkdir(parents=True, exist_ok=True)
    (cases_root / 'test_sample.py').write_text(
        'def test_healthy():\n    pass\n\n\ndef test_first():\n    pass\n\n\ndef test_second():\n    pass\n')
    (cases_root / 'spec_design_cases.json').write_text(
        json.dumps({'schema': 'tfi.portal-spec-design-cases.v1', 'cases': cases}))


def test_catalog_runtime_isolates_invalid_definitions_and_sources(tmp_path, subtests):
    """显式目录不合法或实现源码缺失时只隔离出问题的用例，其他用例仍可读取和查询"""
    store = CatalogRepository(root=tmp_path)
    for failure in ['schema', 'source']:
        with subtests.test(failure=failure):
            good, bad = explicit_case(1, 'test_first'), explicit_case(2, 'test_second')
            if failure == 'schema':
                bad['importance'] = 'invalid'
            else:
                bad['implementations'][0]['entrypoint'] += '_missing'
            write_catalog(tmp_path, [good, bad])
            assert store.get(HEALTHY).id == HEALTHY
            assert [case.id for case in store.query({'case_ids': [HEALTHY]})] == [HEALTHY]
            valid, errors = store.projection()
            assert HEALTHY in {case.id for case in valid} and bad['id'] not in {case.id for case in valid}
            # An invalid explicit file is rejected whole; a missing source only blocks its case.
            assert any(error['resource_id'] == ('explicit' if failure == 'schema' else bad['id']) for error in errors)
            with pytest.raises((CatalogError, KeyError)):
                store.get(bad['id'])
            with pytest.raises(CatalogError):
                store.query({'case_ids': [bad['id']]})
            with pytest.raises(CatalogError):
                store.all()


def test_catalog_conflicts_disable_all_ambiguous_owners(tmp_path, subtests):
    """ID、key、实现或 entrypoint 冲突时，冲突双方的用例都不可用，其余用例照常查询"""
    store = CatalogRepository(root=tmp_path, verify_sources=False)
    for conflict in ['id', 'key', 'implementation', 'entrypoint']:
        with subtests.test(conflict=conflict):
            first, second = explicit_case(1, 'test_first'), explicit_case(2, 'test_second')
            if conflict in ['id', 'key']:
                second[conflict] = first[conflict]
            else:
                field = 'id' if conflict == 'implementation' else 'entrypoint'
                second['implementations'][0][field] = first['implementations'][0][field]
            write_catalog(tmp_path, [first, second])
            ids = {case.id for case in store.query()}
            assert HEALTHY in ids and not ids & {first['id'], second['id']}
            with pytest.raises(CatalogError):
                store.get(first['id'])
