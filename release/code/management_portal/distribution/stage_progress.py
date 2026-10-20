"""Initialize an exported development projection without source achievements."""
from copy import deepcopy
import hashlib
import json
from pathlib import PurePosixPath
import re


def workspace(source, identity):
    rows = source.rows('workspace', 'workspaces', predicates={'workspace_id': {identity}})
    if len(rows) != 1 or rows[0].get('deleted_at') or rows[0]['workspace_id'] != identity:
        raise ValueError('Stage progress Workspace is unavailable')
    return dict(rows[0])


def freeze(source, declaration):
    """Freeze the format contract, never the growing source report index."""
    from .projection_dependencies import regular_file
    from .sanitize import private_path
    if not isinstance(declaration, dict) or set(declaration) != {'index', 'content', 'schema', 'new_report_directory'}:
        raise ValueError('Stage progress initialization requires exact index, content and schema addresses')
    owners, addresses = set(), []
    for key in ('index', 'content', 'schema'):
        address = declaration[key]
        match = re.fullmatch(r'(workspace_[0-9a-f]{12})/file/(.+\.json)', address) if isinstance(address, str) else None
        if not match:
            raise ValueError('Stage progress initialization requires Workspace JSON addresses')
        relative = PurePosixPath(match[2])
        if (relative.is_absolute() or '..' in relative.parts or '\\' in match[2] or relative.as_posix() != match[2]
                or any(private_path(part) for part in relative.parts)):
            raise ValueError('Stage progress path must be canonical and relative')
        owners.add(match[1])
        addresses.append(address)
    directory = declaration['new_report_directory']
    if (not isinstance(directory, str) or not directory or '\\' in directory
            or PurePosixPath(directory).is_absolute() or '..' in PurePosixPath(directory).parts
            or PurePosixPath(directory).as_posix() != directory.rstrip('/')
            or any(private_path(part) for part in PurePosixPath(directory).parts)):
        raise ValueError('Stage progress report directory must be relative')
    if len(owners) != 1 or len(set(addresses)) != 3:
        raise ValueError('Stage progress files must be distinct and belong to one Workspace')
    identity = owners.pop()
    row = workspace(source, identity)
    raw = regular_file(row['path'], declaration['schema'].split('/file/', 1)[1]).read_bytes()
    _empty_contract(json.loads(raw))
    return {**declaration, 'workspace_id': identity,
            'schema_sha256': hashlib.sha256(raw).hexdigest(), 'schema_size': len(raw)}


def _empty_contract(schema):
    """The release contract also permits a never-started index/supplement.

    Nonempty reports retain the author's original validation. No fake dates or
    latest-report references are needed to satisfy an empty initial state.
    """
    result = deepcopy(schema)
    for name, collection, optional in (
            ('stageIndex', 'report_refs', ['generated_at', 'coverage_start_at', 'latest_report_ref']),
            ('stageContent', 'report_sources', ['generated_at'])):
        original = result.get('$defs', {}).get(name)
        if not isinstance(original, dict) or collection not in original.get('properties', {}):
            raise ValueError('Stage progress schema is missing its index/content contract')
        original['required'] = [key for key in original.get('required', []) if key not in optional]
        condition = {
            'if': {'required': [collection], 'properties': {collection: {'minItems': 1}}},
            'then': {'required': optional}}
        # Idempotent when this installation later publishes its own release.
        constraints = original.setdefault('allOf', [])
        if condition not in constraints:
            constraints.append(condition)
    return result


def initialize(source, declaration):
    from .projection_dependencies import regular_file
    row = workspace(source, declaration['workspace_id'])
    raw = regular_file(row['path'], declaration['schema'].split('/file/', 1)[1]).read_bytes()
    if len(raw) != declaration['schema_size'] or hashlib.sha256(raw).hexdigest() != declaration['schema_sha256']:
        raise ValueError('Stage progress schema changed after dependency snapshot')
    documents = {
        declaration['index']: {
            'schema': 'tfi.portal-stage-index.v1', 'schema_ref': declaration['schema'],
            'content_ref': declaration['content'], 'report_refs': [],
            'new_report_directory': declaration['new_report_directory']},
        declaration['content']: {
            'schema': 'tfi.portal-stage-content.v1', 'report_sources': [],
            'item_links': [], 'improvement_details': [], 'experience_targets': [], 'module_attributions': []},
        declaration['schema']: _empty_contract(json.loads(raw)),
    }
    prefix = 'data/share_root/ProjectionEvidence/' + row['workspace_id'] + '/'
    files = {prefix + address.split('/file/', 1)[1]: json.dumps(value, ensure_ascii=False, indent=2) + '\n'
             for address, value in documents.items()}
    return row, files
