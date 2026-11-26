from copy import deepcopy
from management_portal.society_service.templates.catalog import project


def test_family_compatibility_is_declared_without_rewriting_or_hiding_resources():
    rows = [{'template_id': 'old', 'builtin_key': 'family', 'body': {'goal': 'custom'}},
            {'template_id': 'new', 'builtin_key': 'family_roles', 'body': {'role_templates': {'router': 'r'}}},
            {'template_id': 'copy', 'builtin_key': None, 'title': 'Family', 'body': {}}]
    before = deepcopy(rows)
    result = project(rows)
    assert rows == before
    assert result[1]['catalog_entry']['compatibility'][0]['template_id'] == 'old'
    assert result[0]['catalog_entry']['compatibility_of'] == 'new'
    assert 'catalog_entry' not in result[2]
    assert [r['template_id'] for r in result] == ['old', 'new', 'copy']


def test_absent_or_current_family_never_gets_a_fake_compatibility_entry():
    assert project([{'template_id': 'old', 'builtin_key': 'family', 'body': {}}]) == [{'template_id': 'old', 'builtin_key': 'family', 'body': {}}]
    rows = [{'template_id': 'old', 'builtin_key': 'family', 'body': {'role_templates': {'router': 'r'}}},
            {'template_id': 'new', 'builtin_key': 'family_roles', 'body': {}}]
    assert project(rows) == rows
