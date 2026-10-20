"""Restrict packaged permission rules to the copied resource vocabulary."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys


def read_candidate(operation, arguments):
    """Use permission-owned read semantics on the closed release surface.

    No ISA permission policy is changed and no write capability is granted.
    Rules are schema, so human-data anonymization must not rewrite them here.
    """
    from management_portal.permission_system import schemes
    from management_portal.permission_system.scheme.repository import Schemes
    from management_portal.permission_system.engine.store import Store
    readers = {'ListPermissionSchemes': schemes.list_schemes,
               'GetPermissionScheme': schemes.get_scheme,
               'ListPermissionEntries': schemes.list_entries}
    context = schemes.Context(Schemes(Store()), enabled=True, collect_facts=None,
                              may=lambda *_: False)
    return readers[operation](context, '', arguments)


def selected_permissions(source, choices, selected):
    """Export each selected scheme's own instance; builtin rules stay in code."""
    from management_portal.permission_system.scheme.repository import Schemes
    from .sanitize import prune_permissions
    templates = {item['id'] for item in choices if item.get('template') or item.get('instance')}
    instances = {item['id'] for item in choices if item.get('instance')}
    repository = Schemes(None)
    documents = {key: repository.get(key)['rules'] for key in repository.builtin_ids()}
    custom = source.rows('permission', 'schemes', predicates={'scheme_id': templates})
    documents.update({row['scheme_id']: row['document'] for row in custom})
    missing = templates - documents.keys()
    if missing:
        raise ValueError('Selected permission schemes no longer exist: ' + ', '.join(sorted(missing)))
    rule_nodes = {(identity, tree.lower(), node)
                  for identity in instances
                  for tree, definition in repository.compile(documents[identity], saved=True).trees.items()
                  for node in definition.nodes}
    tables = {'schemes': custom}
    for key in source.tables:
        if key[0] == 'permission' and key[1] in {'scheme_nodes', 'scheme_entries', 'scheme_objects', 'scheme_instances'}:
            tables[key[1]] = source.rows(*key, predicates={'scheme_id': instances})
    # No source active_scheme, temporary credentials or merge provenance.
    return prune_permissions(tables, selected, rule_nodes=rule_nodes)


def project_scheme(document, object_types):
    """Remove absent object patterns, never turn them into broader classes."""
    result = deepcopy(document)
    removed = {tree: set() for tree in ('P', 'A', 'R')}
    narrowed = []
    for name, tree in result['trees'].items():
        kept = []
        for node in tree['nodes']:
            if 'each' in node:
                kinds = [node['each']] if isinstance(node['each'], str) else node['each']
                remaining = [kind for kind in kinds if kind in object_types[name]]
                if not remaining:
                    removed[name].add(node['id'])
                    continue
                if remaining != kinds:
                    narrowed.append({'tree': name, 'node': node['id'], 'removed_types': [k for k in kinds if k not in remaining]})
                node['each'] = remaining[0] if isinstance(node['each'], str) else remaining
            kept.append(node)
        # Retained child rules cannot silently lose their owner or fallback.
        for node in kept:
            if node.get('parent') in removed[name] or node.get('otherwise') in removed[name]:
                raise ValueError('Retained permission node has an omitted placement dependency: ' + node['id'])
        if tree['root'] in removed[name] or tree['default_parent'] in removed[name]:
            raise ValueError('Cannot remove a permission tree root/default')
        tree['nodes'] = kept
    dropped_entries = [entry['id'] for entry in result.get('entries', [])
                       if any(entry.get(name.lower()) in nodes for name, nodes in removed.items())]
    result['entries'] = [entry for entry in result.get('entries', []) if entry['id'] not in dropped_entries]
    # Instruction classifications refer to classes, not each patterns. Any
    # unexpected edge must be resolved explicitly instead of falling back.
    for element, placement in result.get('instructions', {}).items():
        if any(placement.get(name.lower()) in nodes for name, nodes in removed.items()):
            raise ValueError('Instruction references an omitted permission node: ' + element)
    return result, {'scheme_id': result['scheme_id'], 'removed_nodes': {k: sorted(v) for k, v in removed.items()},
                    'narrowed_patterns': narrowed, 'removed_entries': dropped_entries}


def adapt_builtin_schemes(code):
    """Run in the copied code context; compile all builtins through their owner."""
    code = Path(code).resolve()
    directory = code / 'management_portal/permission_system/scheme/builtin'
    if not directory.exists():
        return {'status': 'not_present', 'schemes': []}
    # The subprocess owns its copied registry/catalog imports. No mutation of
    # the live interpreter's vocabulary, database, or permission settings.
    program = '''
import json
from pathlib import Path
from management_portal.distribution.permissions import project_scheme
from management_portal.permission_system.scheme import vocabulary
from management_portal.permission_system.scheme.repository import Schemes
results = []
for path in sorted(Path('management_portal/permission_system/scheme/builtin').glob('*.json')):
    document, evidence = project_scheme(json.loads(path.read_text()), vocabulary.OBJECT_TYPES)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\\n')
    results.append(evidence)
owner = Schemes(None)
for evidence in results:
    compiled = owner.compiled(evidence['scheme_id'])
    evidence['compiled_digest'] = compiled.digest
print(json.dumps({'status':'passed','object_types':{k:sorted(v) for k,v in vocabulary.OBJECT_TYPES.items()},'schemes':results}))
'''
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(code),
           'PORTAL_RESOURCE_TYPES_DIR': str(code / 'config/resource-types')}
    completed = subprocess.run([sys.executable, '-c', program], cwd=code, env=env,
                               capture_output=True, text=True, timeout=180)
    if completed.returncode:
        raise ValueError('Copied permission scheme compilation failed: ' + completed.stderr[-6000:])
    return json.loads(completed.stdout)
