#!/usr/bin/env python3
"""Deterministic architecture audit; resource reads use the calling actor's ISA.

RunScript writes complete evidence to artifacts and only small routing fields to
its output. A completed audit with deviations exits 0; an incomplete audit exits 2.
No model is called. A publication commit selects immutable Git inputs; Design
versions are recorded individually because knowledge reads are not transactional.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import types
from urllib.parse import urlsplit

import yaml
from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from management_common.format_tags import schema_matches
from management_common.product_environment import read_product_env

SCHEMA = 'tfi.portal-architecture.v1'
AUDIT_SCHEMA = 'tfi.portal-architecture-audit.v1'
ID = re.compile(r'\b(?:decision|plan)_[0-9a-f]{12}\b')
RESOURCE_ADDRESS = re.compile(r'^[a-z][a-z0-9_]*_[0-9a-f]{12}(?:/.*)?$')


def indexed_ids(text, kind):
    """Only catalog rows count; prose, fenced examples and headings do not."""
    result = []
    in_row = False
    for token in MarkdownIt('commonmark').enable('table').parse(text):
        if token.type == 'tr_open':
            in_row = True
        elif token.type == 'tr_close':
            in_row = False
        elif in_row and token.type == 'inline':
            result.extend(x for x in ID.findall(token.content) if x.startswith(kind + '_'))
    return result


FEATURE_DECLARATIONS = 'management_portal/portal_features.py'
# The packages Portal services import; scripts and test cases are entry points, not core.
CORE_PACKAGES = ('management_portal/', 'management_common/', 'knowledge_board_lan/', 'skill_board_lan/')


def feature_modules(repo):
    """``{feature key: module patterns}`` declared in the audited version of portal_features.py."""
    if not repo.exists(FEATURE_DECLARATIONS):
        return {}
    module = types.ModuleType('portal_features_under_audit')
    sys.modules[module.__name__] = module  # dataclasses look their module up
    try:
        exec(compile(repo.read(FEATURE_DECLARATIONS), FEATURE_DECLARATIONS, 'exec'), module.__dict__)
    finally:
        sys.modules.pop(module.__name__, None)
    # Versions before the module lists declare no feature modules.
    return {feature.key: tuple(getattr(feature, 'modules', ())) for feature in module.FEATURES}


def module_name(path):
    return path.removesuffix('.py').replace('/', '.').removesuffix('.__init__')


def top_level_imports(source, path):
    """``(line, module)`` for each import a module runs when it is itself imported.

    Imports inside functions run on first use and are how core reaches an
    optional feature; ``from a import b`` yields both ``a`` and ``a.b``.
    """
    name = module_name(path)
    package = name if path.endswith('__init__.py') else name.rpartition('.')[0]
    found = []

    def visit(nodes):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(node, ast.Import):
                found.extend((node.lineno, alias.name) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ''
                if node.level:
                    parts = package.split('.')[:len(package.split('.')) - node.level + 1]
                    base = '.'.join(filter(None, [*parts, base]))
                found.append((node.lineno, base))
                found.extend((node.lineno, f'{base}.{alias.name}') for alias in node.names)
            else:
                visit(ast.iter_child_nodes(node))

    visit(ast.parse(source).body)
    return found


def feature_imports(repo):
    """Core modules that import a feature's module at the top level (decision_15aab18c0ab3).

    A package that leaves the feature out could not import such a core module.
    Returns ``(path, line, feature, module)`` rows.
    """
    features = feature_modules(repo)
    if not any(features.values()):
        return []

    def owner(name):
        return next((key for key, patterns in features.items() if any(fnmatchcase(name, p) for p in patterns)), None)

    files = repo.files if repo.commit else {
        path for path in repo.git('ls-files').splitlines() if (repo.root / path).is_file()}
    found = {}
    for path in sorted(files):
        if not path.endswith('.py') or not path.startswith(CORE_PACKAGES) or owner(module_name(path)):
            continue
        for line, name in top_level_imports(repo.read(path), path):
            if (feature := owner(name)) and len(name) < len(found.get((path, line, feature), name + '.')):
                found[(path, line, feature)] = name
    return [(path, line, feature, name) for (path, line, feature), name in sorted(found.items())]


def feature_mapping(repo, mapping, by_id):
    """``(code, feature, details)`` for the ``features`` section of modules.yaml.

    Each feature portal_features.py declares is listed once with the modules
    holding its code, and every module file of the feature lies in the code
    paths of a listed module.
    """
    declared = feature_modules(repo)
    if not any(declared.values()):
        return []
    found, listed = [], {}
    for entry in mapping.get('features') or []:
        if entry.get('id') in listed:
            found.append(('feature.duplicate', entry.get('id'), {}))
        listed[entry.get('id')] = entry
    files = sorted(repo.files if repo.commit else repo.git('ls-files').splitlines())
    for key in sorted(set(declared) | set(listed), key=str):
        if key not in listed or key not in declared:
            found.append(('feature.unmapped' if key in declared else 'feature.unknown', key, {}))
            continue
        modules = listed[key].get('modules') or []
        found.extend(('feature.unknown_module', key, {'module': mid}) for mid in modules if mid not in by_id)
        paths = [pattern for mid in modules if mid in by_id for pattern in by_id[mid].get('code_paths', [])]
        found.extend(('feature.code_outside_modules', key, {'path': path}) for path in files
                     if path.endswith('.py') and any(fnmatchcase(module_name(path), p) for p in declared[key])
                     and not any(repo.matches(path, pattern) for pattern in paths))
    return found


def metadata(text):
    lines = text.splitlines()
    if not lines or lines[0].strip() != '---':
        raise ValueError('missing YAML frontmatter')
    end = next(i for i in range(1, len(lines)) if lines[i].strip() == '---')
    value = yaml.safe_load('\n'.join(lines[1:end]))
    if not isinstance(value, dict):
        raise ValueError('frontmatter must be a mapping')
    return value


class Repository:
    def __init__(self, root, commit=''):
        self.root = Path(root)
        self.commit = commit
        if commit and not re.fullmatch(r'[0-9a-f]{40}', commit):
            raise ValueError('commit must be a full SHA-1')
        self.files = set(self.git('ls-tree', '-r', '--name-only', commit).splitlines()) if commit else set()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], text=True, stderr=subprocess.PIPE)

    def read(self, path):
        return self.git('show', f'{self.commit}:{path}') if self.commit else (self.root / path).read_text()

    def matches(self, path, pattern):
        return path == pattern or path.startswith(pattern.rstrip('/') + '/') or fnmatchcase(path, pattern)

    def exists(self, pattern):
        if PurePosixPath(pattern).is_absolute() or '..' in PurePosixPath(pattern).parts:
            return False
        if self.commit:
            return any(self.matches(p, pattern) for p in self.files)
        return (self.root / pattern).exists() or any(self.root.glob(pattern))


def source_path(value, root):
    value = str(value).split('#', 1)[0]
    value = re.sub(r':\d+(?:-\d+)?$', '', value)
    prefix = str(root).rstrip('/') + '/'
    if value.startswith(prefix):
        value = value[len(prefix):]
    return value


def source_reference(value, root):
    """Classify before stripping path anchors: addresses and URLs are not code."""
    if not isinstance(value, str) or not value.strip():
        return 'invalid', str(value)
    value = value.strip()
    if RESOURCE_ADDRESS.fullmatch(value):
        return 'resource', value
    if '://' in value or re.match(r'^(?:https?|ftps?|mailto|file):', value, re.I):
        return 'url', value
    path = os.path.normpath(source_path(value, root))
    if Path(path).is_absolute():
        return 'external_path', path
    if path == '..' or path.startswith('../'):
        return 'external_path', os.path.normpath(str(root / path))
    if Path(path).suffix.lower() in {'.md', '.rst'} or Path(path).name in {'README', 'LICENSE', 'COPYING', 'NOTICE'}:
        return 'repository_document', path
    return 'implementation', path


def readable_path(path):
    """Check access without collecting contents or following special devices."""
    mode = path.stat().st_mode
    if stat.S_ISDIR(mode):
        with os.scandir(path) as entries:
            next(entries, None)
    elif stat.S_ISREG(mode):
        with path.open('rb'):
            pass
    else:
        raise ValueError('unsupported reference file type')


def check_reference(repo, kind, target, resolve_resource):
    check = {'url': 'url_format', 'resource': 'resource_resolution',
             'repository_document': 'repository_readability'}.get(kind, 'path_readability')
    try:
        if kind == 'url':
            parsed = urlsplit(target)
            if (not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9+.-]*', parsed.scheme)
                    or re.search(r'\s|[\x00-\x1f\x7f]|%(?![0-9a-fA-F]{2})', target)
                    or (parsed.scheme.lower() in {'http', 'https', 'ftp', 'ftps'} and not parsed.hostname)
                    or (not parsed.netloc and not parsed.path)):
                raise ValueError('invalid URL format')
            # urllib validates bracketed hosts and ports without making requests.
            parsed.port
        elif kind == 'resource':
            if resolve_resource is None:
                raise RuntimeError('resource resolver unavailable')
            result = resolve_resource(target)
            if (result.get('target', {}).get('exists') is False
                    or result.get('resource', {}).get('capabilities', {}).get('read') is False
                    or 'read' not in result.get('operations', [])):
                raise PermissionError('reference is unavailable or unreadable')
        elif kind == 'repository_document':
            if repo.commit:
                if not repo.exists(target):
                    raise FileNotFoundError(target)
                repo.git('cat-file', '-e', f'{repo.commit}:{target}')
            else:
                readable_path(repo.root / target)
        elif kind == 'external_path':
            readable_path(Path(target))
        else:
            raise ValueError('invalid reference')
        return {'check': check, 'status': 'valid_format' if kind == 'url' else 'readable'}
    except Exception as exc:
        code = error_code(exc)
        # Missing/unreadable references are audit findings; unavailable checking
        # infrastructure makes the audit incomplete as well.
        expected = code in {'ValueError', 'FileNotFoundError', 'PermissionError', 'NotADirectoryError',
                            'instruction.not_found', 'instruction.forbidden', 'instruction.invalid_argument',
                            'resource.not_found', 'resource.forbidden'}
        return {'check': check, 'status': 'invalid' if expected else 'check_failed', 'error': code}


def error_code(error):
    """ISA clients may wrap an error object; preserve its code, never its body."""
    code = getattr(error, 'code', '')
    if isinstance(code, str) and re.fullmatch(r'[a-z][a-z0-9_.]{1,100}', code):
        return code
    try:
        value = json.loads(str(error))
        code = value.get('code', '') if isinstance(value, dict) else ''
        if re.fullmatch(r'[a-z][a-z0-9_.]{1,100}', code):
            return code
    except (ValueError, TypeError):
        pass
    return type(error).__name__


def audit(repo, mapping, spec, improve, evolution, documents, instructions, read_errors=None,
          resolve_resource=None):
    findings = []
    external_references, reference_cache = [], {}

    def note(code, subject, **details):
        findings.append(dict(code=code, subject=subject, **details))

    if not schema_matches(mapping.get('schema'), SCHEMA):
        raise ValueError('unsupported module map schema')
    model_version = mapping.get('model_version', 1)
    if model_version not in (1, 2):
        raise ValueError('unsupported module model version')
    runtime_model = model_version == 2
    modules = mapping['modules']
    protocols = mapping.get('protocols', [])
    ids = [m['id'] for m in modules]
    by_id = {m['id']: m for m in modules}
    for mid, count in Counter(ids).items():
        if count != 1:
            note('module.duplicate', mid, count=count)
    owners, designs = defaultdict(list), defaultdict(list)
    protocol_by_id = {p['id']: p for p in protocols}
    # Resolve complete chains without a depth limit. Invalid chains never grant
    # composition or protocol scope; a module still owns its own files.
    ancestors, reported_cycles = {}, set()
    for mid in sorted(by_id):
        chain, positions = [], {}
        current = mid
        while current in by_id and current not in positions:
            positions[current] = len(chain)
            chain.append(current)
            current = by_id[current].get('parent')
        if current in positions:
            cycle = chain[positions[current]:]
            first = cycle.index(min(cycle))
            cycle = tuple(cycle[first:] + cycle[:first])
            if cycle not in reported_cycles:
                note('module.invalid_parent', cycle[0], parent=by_id[cycle[0]]['parent'],
                     reason='cycle', cycle=list(cycle) + [cycle[0]])
                reported_cycles.add(cycle)
        ancestors[mid] = set(chain[1:]) if not current else set()

    def contains(endpoint, module):
        return module in by_id and (endpoint == module or endpoint in ancestors[module])

    def evidence(edge):
        return edge.get('designs', []) + (edge.get('related_designs', []) if runtime_model else [])

    def has_protocol(design, owner, other):
        return any(design in evidence(edge)
                   and any(contains(endpoint, owner) for endpoint in edge.get('modules', []))
                   and any(contains(endpoint, other) for endpoint in edge.get('modules', []))
                   for edge in protocols)
    for m in modules:
        mid = m['id']
        if m.get('parent') and m['parent'] not in by_id:
            note('module.invalid_parent', mid, parent=m['parent'], reason='missing')
        for owner in m.get('instruction_owners', []):
            owners[owner].append(mid)
        for design in m.get('designs', []):
            designs[design].append(('module', mid))
        if runtime_model:
            if m.get('layer') not in {'frontend', 'entry', 'service', 'storage', 'external'}:
                note('module.invalid_layer', mid, layer=m.get('layer'))
            if m.get('level') not in {'runtime', 'component', 'external'}:
                note('module.invalid_level', mid, level=m.get('level'))
            elif ((m['level'] == 'component') != bool(m.get('parent'))
                  or (m.get('parent') in by_id
                      and by_id[m['parent']].get('level') not in {'runtime', 'component'})):
                note('module.invalid_level', mid, level=m['level'])
            if not m.get('title'):
                note('module.title_missing', mid)
        external = runtime_model and m.get('level') == 'external' and m.get('implementation') == 'external'
        if not m.get('code_paths') and not external:
            note('module.paths_empty', mid)
        for pattern in m.get('code_paths', []):
            if not repo.exists(pattern):
                note('module.path_missing', mid, path=pattern)
    if runtime_model:
        # The historical singular system.design was descriptive, not a primary
        # assignment. Only the explicit plural list participates in ownership.
        system = mapping.get('system') or {}
        for design in system.get('designs', []):
            designs[design].append(('system', system.get('id', '')))
        if system.get('designs') and not system.get('id'):
            note('system.id_missing', 'system')
        for pid, count in Counter(p['id'] for p in protocols).items():
            if count != 1:
                note('protocol.duplicate', pid, count=count)
        for edge in protocols:
            for design in edge.get('designs', []):
                designs[design].append(('protocol', edge['id']))
    for design in sorted(set(spec) | set(designs)):
        if design not in spec:
            note('design.not_indexed', design, owners=designs[design])
        elif len(designs[design]) != 1:
            note('design.ownership', design, owners=designs[design])
    for design in sorted(set(spec)):
        if design not in documents or len(designs[design]) != 1:
            continue
        owner_kind, owner_id = designs[design][0]
        owner_modules = ([owner_id] if owner_kind == 'module' else
                         protocol_by_id[owner_id].get('modules', []) if owner_kind == 'protocol' else [])
        links = documents[design].get('links') or {}
        sources = links.get('source') or []
        if not isinstance(sources, list):
            note('design.invalid_sources', design)
            continue
        for source in sources:
            kind, path = source_reference(source, repo.root)
            if kind != 'implementation':
                key = (kind, path)
                if key not in reference_cache:
                    reference_cache[key] = check_reference(repo, kind, path, resolve_resource)
                result = reference_cache[key]
                external_references.append({'design': design, 'reference': source, 'target': path,
                                            'kind': kind, **result})
                if result['status'] in {'invalid', 'check_failed'}:
                    note('design.source_invalid_reference', design, reference=source, kind=kind,
                         error=result['error'])
                continue
            if path.startswith('tests/'):
                continue
            candidates = [other['id'] for other in modules
                          if any(repo.matches(path, p) for p in other.get('code_paths', []))]
            if runtime_model:
                # A parent directory may contain several registered components.
                # A file assigned to a descendant is not the ancestor's own
                # assembly code and cannot grant access to sibling components.
                candidates = [mid for mid in candidates
                              if not any(mid in ancestors[other] for other in candidates)]
            if owner_kind == 'system':
                covered = bool(candidates)
            elif owner_kind == 'module':
                covered = any(contains(owner_id, other) or contains(other, owner_id) for other in candidates)
            else:
                covered = any(contains(endpoint, other) for endpoint in owner_modules for other in candidates)
            if not covered:
                if not candidates:
                    note('design.source_unmapped', design, **{owner_kind: owner_id}, path=path)
                elif not any(has_protocol(design, owner, other) for owner in owner_modules for other in candidates):
                    note('design.source_without_protocol', design, **{owner_kind: owner_id}, path=path,
                         candidate_modules=candidates)
            if (covered or runtime_model) and not repo.exists(path):
                note('design.source_missing', design, **{owner_kind: owner_id}, path=path)
    used_owners = sorted({i['owner'] for i in instructions})
    for owner in sorted(set(used_owners) | set(owners)):
        if owner not in used_owners or len(owners[owner]) != 1:
            note('instruction.owner_mapping', owner, modules=owners[owner])
    for edge in protocols:
        if len(edge.get('modules', [])) < 2 or not edge.get('description') or not edge.get('example'):
            note('protocol.incomplete', edge['id'])
        if runtime_model and (not edge.get('title') or len(set(edge.get('modules', []))) < 2):
            note('protocol.invalid_contract', edge['id'])
        for mid in edge.get('modules', []):
            if mid not in by_id:
                note('protocol.unknown_module', edge['id'], module=mid)
        for design in evidence(edge):
            if design not in spec:
                note('protocol.unknown_design', edge['id'], design=design)
    if evolution is None:
        note('evolution.missing', 'architecture/evolution.yaml')
        plans = []
    else:
        plans = evolution['plans']
    plan_ids = [p['id'] for p in plans]
    for pid, count in Counter(plan_ids).items():
        if count != 1:
            note('plan.duplicate', pid, count=count)
    graph = {p['id']: p.get('depends_on', []) for p in plans}
    for plan in plans:
        pid = plan['id']
        if pid not in improve:
            note('plan.not_indexed', pid)
        if pid not in documents:
            note('plan.unreadable', pid)
        for mid in plan.get('modules', []):
            if mid not in by_id:
                note('plan.unknown_module', pid, module=mid)
        for dep in graph[pid]:
            if dep not in graph:
                note('plan.dependency_missing', pid, dependency=dep)
    active, done = [], set()

    def visit(pid):
        if pid in active:
            note('plan.cycle', pid, cycle=active[active.index(pid):] + [pid])
            return
        if pid in done:
            return
        active.append(pid)
        for dep in sorted(graph.get(pid, [])):
            visit(dep)
        active.pop()
        done.add(pid)

    for pid in sorted(graph):
        visit(pid)
    for path, line, feature, name in feature_imports(repo):
        note('feature.core_import', path, line=line, feature=feature, module=name)
    for code, feature, details in feature_mapping(repo, mapping, by_id):
        note(code, feature, **details)
    for address, error in sorted((read_errors or {}).items()):
        note('resource.read_failed', address, error=error)
    findings.sort(key=lambda f: json.dumps(f, sort_keys=True, ensure_ascii=False))
    return {'schema': AUDIT_SCHEMA, 'commit': repo.commit,
            'model_version': model_version,
            'design_owners': {d: {'kind': items[0][0], 'id': items[0][1]}
                              for d, items in sorted(designs.items()) if len(items) == 1},
            'system_designs': sorted(d for d, items in designs.items()
                                     if len(items) == 1 and items[0][0] == 'system'),
            'counts': {'modules': len(modules), 'designs': len(set(spec)), 'instructions': len(instructions),
                       'owners': len(used_owners), 'plans': len(plans), 'deviations': len(findings),
                       'external_references': len(external_references)},
            'by_code': dict(sorted(Counter(f['code'] for f in findings).items())),
            'audit_failed': bool(read_errors) or any(r['status'] == 'check_failed' for r in external_references),
            'findings': findings, 'external_references': external_references}


def isa_client(resident_id):
    if os.environ.get('PORTAL_ISA_SOCKET'):
        from portal_script import isa
        return lambda name, arguments: isa(name, **arguments)
    if not resident_id:
        raise ValueError('--resident-id is required outside RunScript')
    from scripts.resident_hatg_instruction import execute, SCHEMA as ISA_SCHEMA
    return lambda name, arguments: execute(resident_id, {
        'schema': ISA_SCHEMA, 'instruction': name, 'arguments': arguments})['result']


def run(repo, call):
    inputs = {name: repo.read(name) for name in ('SPEC.md', 'IMPROVE.md', 'architecture/modules.yaml')}
    mapping = yaml.safe_load(inputs['architecture/modules.yaml'])
    evolution = None
    if repo.exists('architecture/evolution.yaml'):
        inputs['architecture/evolution.yaml'] = repo.read('architecture/evolution.yaml')
        evolution = yaml.safe_load(inputs['architecture/evolution.yaml'])
    spec = indexed_ids(inputs['SPEC.md'], 'decision')
    improve = indexed_ids(inputs['IMPROVE.md'], 'plan')
    addresses = sorted(set(spec) | {p['id'] for p in (evolution or {}).get('plans', [])})
    docs, versions, errors = {}, {}, {}

    def read(address):
        try:
            result = call('ReadResource', {'address': address})
            text = result['text']
            meta = metadata(text)
            if meta.get('kb_id') != address:
                raise ValueError('resource identity mismatch')
            content_version = (meta.get('version') or {}).get('number')
            return address, meta, {'version': str(content_version if content_version is not None else result['version']),
                                   'response_version': result['version'],
                                   'sha256': hashlib.sha256(text.encode()).hexdigest()}, ''
        except Exception as exc:
            # Do not echo response bodies or credentials into operational artifacts.
            return address, None, None, error_code(exc)

    with ThreadPoolExecutor(max_workers=6) as pool:
        for address, meta, version, error in pool.map(read, addresses):
            if error:
                errors[address] = error
            else:
                docs[address], versions[address] = meta, version
    instructions = call('ListInstructions', {})['instructions']
    report = audit(repo, mapping, spec, improve, evolution, docs, instructions, errors,
                   resolve_resource=lambda address: call('ResolveResource', {'address': address}))
    report['sources'] = {'files': {name: hashlib.sha256(text.encode()).hexdigest() for name, text in inputs.items()},
                         'resources': versions, 'instruction_catalog_sha256': hashlib.sha256(
                             json.dumps(instructions, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
    if not repo.commit and any(repo.read(name) != text for name, text in inputs.items()):
        report['audit_failed'] = True
        report['findings'].append({'code': 'input.changed_during_audit', 'subject': 'working_tree'})
        report['counts']['deviations'] += 1
        report['by_code']['input.changed_during_audit'] = 1
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=ROOT)
    parser.add_argument('--commit', default='')
    parser.add_argument('--resident-id', default=read_product_env('PORTAL_RESIDENT_ID', ''))
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args(argv)
    input_fields = {}
    if os.environ.get('PORTAL_SCRIPT_INPUT'):
        from portal_script import inputs
        input_fields = inputs().get('arguments', {})
    commit = args.commit or input_fields.get('commit', '')
    artifacts = os.environ.get('PORTAL_SCRIPT_ARTIFACTS')
    destination = args.output_dir or (
        Path(artifacts) if artifacts else args.repo_root / 'test-results' / 'architecture-audit'
    )
    destination.mkdir(parents=True, exist_ok=True)
    try:
        report = run(Repository(args.repo_root, commit), isa_client(args.resident_id))
    except Exception as exc:
        report = {'schema': AUDIT_SCHEMA, 'commit': commit,
                  'audit_failed': True, 'counts': {'deviations': 1},
                  'by_code': {'audit.failed': 1},
                  'findings': [{'code': 'audit.failed', 'subject': type(exc).__name__}]}
    (destination / 'architecture-audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    output = {'has_deviations': bool(report['counts']['deviations']), 'audit_failed': report['audit_failed'],
              'commit': commit, 'counts': report['counts'], 'by_code': report['by_code'],
              'report': 'architecture-audit.json'}
    if os.environ.get('PORTAL_SCRIPT_OUTPUT'):
        from portal_script import set_output
        set_output(output)
    print(json.dumps(output, ensure_ascii=False))
    return 2 if report['audit_failed'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
