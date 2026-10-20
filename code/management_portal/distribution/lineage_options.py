"""Preferences do not select Lineages; only resolved membership activates them."""
from copy import deepcopy
import re


def normalize_preferences(value):
    if not isinstance(value, dict):
        raise ValueError('resource_lineage_options must be an object')
    result = deepcopy(value)
    for identity, options in result.items():
        if not isinstance(identity, str) or not re.fullmatch(r'lineage_[0-9a-f]{12}', identity) or not isinstance(options, dict):
            raise ValueError('Invalid resource Lineage preference')
        if 'history' in options and type(options['history']) is not bool:
            raise ValueError('Lineage history must be boolean')
        workspace = options.get('workspace', {})
        if not isinstance(workspace, dict):
            raise ValueError('Lineage workspace preference must be an object')
        if 'own_outputs' in workspace and type(workspace['own_outputs']) is not bool:
            raise ValueError('Workspace own_outputs must be boolean')
        if 'extra_bindings' in workspace and workspace['extra_bindings'] not in ('unset', 'exclude', 'partial', 'all'):
            raise ValueError('Invalid extra Workspace binding scope')
        for field in ('extra_scope', 'exclusions'):
            if field in workspace and not isinstance(workspace[field], str):
                raise ValueError('Workspace scope text must be a string')
    return result


def effective_lineages(snapshot, dependencies):
    preferences = normalize_preferences(snapshot.get('resource_lineage_options', {}))
    result = {}
    for item in dependencies['lineages']:
        identity = item['id']
        options = preferences.get(identity, {})
        result[identity] = {'id': identity, 'history': options.get('history', False),
            'workspace': {'own_outputs': False, 'extra_bindings': 'exclude', **options.get('workspace', {})}}
    # Explicit/System/manual-projection options retain their existing priority.
    result.update({item['id']: deepcopy(item) for item in snapshot.get('lineages', [])})
    return result
