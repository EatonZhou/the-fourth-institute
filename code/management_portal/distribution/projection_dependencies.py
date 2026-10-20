"""Freeze declared projection evidence without selecting an entire Workspace."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re

SCHEMA = 'tfi.projection-release-dependencies.v1'
DECLARATION = 'release-dependencies.json'


def json_root_files(source, roots):
    """Freeze selected JSON references at packaging time, within each owner.

    This is invoked only by distribution. Live Portal readers use the original
    files; they never build or maintain a distribution copy.
    """
    if (not isinstance(roots, list) or any(not isinstance(root, str) for root in roots)
            or len(roots) != len(set(roots))):
        raise ValueError('Projection JSON roots must be unique addresses')
    files = {}
    for root in roots:
        match = re.fullmatch(r'(workspace_[0-9a-f]{12})/file/(.+\.json)', root)
        if not match:
            raise ValueError('Projection JSON root must be an exact Workspace JSON file')
        owner = match[1]
        rows = source.rows('workspace', 'workspaces', predicates={'workspace_id': {owner}})
        if len(rows) != 1 or rows[0].get('deleted_at') or rows[0]['workspace_id'] != owner:
            raise ValueError('Projection JSON root Workspace is unavailable')
        prefix = owner + '/file/'
        pending, observed = [root], {}

        def references(value):
            if isinstance(value, dict):
                for child in value.values():
                    yield from references(child)
            elif isinstance(value, list):
                for child in value:
                    yield from references(child)
            elif isinstance(value, str) and re.fullmatch(re.escape(prefix) + r'[^\s#]+(?:#L\d+(?:-L\d+)?)?', value):
                yield value.split('#', 1)[0]

        while pending:
            address = pending.pop()
            if address in observed:
                continue
            path = regular_file(rows[0]['path'], address.removeprefix(prefix))
            raw = path.read_bytes()
            observed[address] = (path, raw)
            descriptor = {'address': address, 'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
            if address in files and files[address] != descriptor:
                raise ValueError('Projection JSON roots changed while being frozen')
            files[address] = descriptor
            if path.suffix == '.json':
                pending.extend(references(json.loads(raw)))
        if any(path.read_bytes() != raw for path, raw in observed.values()):
            raise ValueError('Projection JSON root changed while being frozen')
    return list(files.values())


def regular_file(root, relative):
    """An exact file under the registered root; every path segment is checked."""
    path = PurePosixPath(relative)
    from .sanitize import private_path
    if not relative or path.is_absolute() or '..' in path.parts or '\\' in relative or path.as_posix() != relative:
        raise ValueError('Projection dependency path must be a canonical relative file')
    if any(private_path(part) for part in path.parts):
        raise ValueError('Projection dependency cannot include private files')
    candidate = Path(root)
    if candidate.is_symlink() or not candidate.is_dir():
        raise ValueError('Projection dependency root is unavailable or linked')
    for part in path.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError('Projection dependency cannot follow links')
    if not candidate.is_file():
        raise ValueError('Projection dependency file is unavailable: ' + relative)
    return candidate


def declarations(source, projections):
    """Read only manifests owned by the exact published application directory."""
    from .content import registered_content_path
    selected = {p['id']: p['revision'] for p in projections}
    result = []
    for row in source.rows('workbenches', 'application_packages', predicates={'app_id': set(selected)}):
        if row['revision'] != selected.get(row['app_id']):
            continue
        app = json.loads(row['application']) if isinstance(row['application'], str) else row['application']
        repo_id = app['repo_id']
        record = source.read_index(resource_ids={repo_id}).get(repo_id)
        if not record:
            raise ValueError('Published application repository is unavailable: ' + repo_id)
        root = registered_content_path(record)
        directory = PurePosixPath(app['directory'])
        if directory.is_absolute() or '..' in directory.parts:
            raise ValueError('Published application directory is outside its repository')
        declaration = root / directory / DECLARATION
        if not declaration.exists():
            continue
        declaration = regular_file(root, (directory / DECLARATION).as_posix())
        raw = declaration.read_bytes()
        value = json.loads(raw)
        if value.get('schema') != SCHEMA or value.get('code_repository') != repo_id or not isinstance(value.get('files'), list):
            raise ValueError('Projection release dependencies do not match the application repository')
        files, seen = [], set()
        for item in [*value['files'], *json_root_files(source, value.get('json_roots', []))]:
            address = item.get('address', '')
            match = re.fullmatch(r'(workspace_[0-9a-f]{12})/file/(.+)', address)
            if not match or address in seen or not re.fullmatch(r'[0-9a-f]{64}', str(item.get('sha256', ''))) or type(item.get('size')) is not int or item['size'] < 0:
                raise ValueError('Projection evidence must have unique exact Workspace addresses, SHA256 and size')
            seen.add(address)
            files.append({'address': address, 'sha256': item['sha256'], 'size': item['size']})
        item = {'id': row['app_id'], 'revision': row['revision'], 'code_repository': repo_id,
                'declaration_sha256': hashlib.sha256(raw).hexdigest(), 'files': sorted(files, key=lambda v: v['address'])}
        if 'stage_progress' in value:
            from .stage_progress import freeze
            item['stage_progress'] = freeze(source, value['stage_progress'])
            owner = item['stage_progress']['workspace_id'] + '/file/'
            if any(file['address'].startswith(owner) for file in files):
                raise ValueError('Stage progress initialization cannot export source Workspace evidence')
        result.append(item)
    return sorted(result, key=lambda v: v['id'])


def resolve_files(source, dependencies):
    """Return minimal copy scopes and bindings; never crawl evidence links."""
    from .content import registered_content_path
    files, mappings, workspaces, repositories = [], [], {}, {}
    generated, resets = {}, set()
    seen = {}
    for declaration in dependencies:
        repo_id = declaration['code_repository']
        record = source.read_index(resource_ids={repo_id}).get(repo_id)
        if not record:
            raise ValueError('Declared projection repository is unavailable')
        repositories[repo_id] = str(registered_content_path(record).resolve())
        if declaration.get('stage_progress'):
            from .stage_progress import initialize
            row, seeds = initialize(source, declaration['stage_progress'])
            if any(key in generated and generated[key] != text for key, text in seeds.items()):
                raise ValueError('Conflicting stage progress initializations')
            generated.update(seeds)
            resets.add(row['path'])
            workspaces[row['workspace_id']] = row
        for descriptor in declaration['files']:
            address = descriptor['address']
            workspace_id, relative = address.split('/file/', 1)
            if workspace_id not in workspaces:
                rows = source.rows('workspace', 'workspaces', predicates={'workspace_id': {workspace_id}})
                if len(rows) != 1 or rows[0].get('deleted_at'):
                    raise ValueError('Declared projection Workspace is unavailable: ' + workspace_id)
                workspaces[workspace_id] = dict(rows[0])
            original = workspaces[workspace_id]['path']
            path = regular_file(original, relative)
            if any(path == Path(root) or path.is_relative_to(root) for root in resets):
                raise ValueError('Stage progress source history cannot be exported as evidence')
            raw = path.read_bytes()
            if len(raw) != descriptor['size'] or hashlib.sha256(raw).hexdigest() != descriptor['sha256']:
                raise ValueError('Declared projection evidence changed: ' + address)
            if address in seen:
                if seen[address] != descriptor:
                    raise ValueError('Projection evidence has conflicting declarations')
                continue
            seen[address] = descriptor
            files.append({'source': str(path), 'target': f'data/share_root/ProjectionEvidence/{workspace_id}/{relative}',
                          'immutable_file': dict(descriptor)})
    for identity, row in workspaces.items():
        target = '${share_root}/ProjectionEvidence/' + identity
        mappings.append((row['path'], target))
        row['path'] = target
    mappings.extend((root, '${code_root}') for root in repositories.values())
    if any(Path(item['source']).is_relative_to(root) for item in files for root in resets):
        raise ValueError('Stage progress source history conflicts with projection evidence')
    return {'files': files, 'path_mappings': mappings, 'workspaces': workspaces, 'repositories': repositories,
            'generated_files': generated, 'reset_roots': sorted(resets)}
