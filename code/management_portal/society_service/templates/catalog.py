"""Presentation relationships in the shared catalog, never content rewrites."""
from copy import deepcopy


def project(templates):
    rows = deepcopy(templates)
    builtin = {row.get('builtin_key'): row for row in rows if row.get('builtin_key')}
    legacy, main = builtin.get('family'), builtin.get('family_roles')
    if legacy and main and not legacy['body'].get('role_templates'):
        main['catalog_entry'] = {'key': 'family', 'title': 'Family', 'compatibility': [
            {'template_id': legacy['template_id'], 'label': '旧配方（自定义）'}]}
        legacy['catalog_entry'] = {'compatibility_of': main['template_id']}
    return rows
