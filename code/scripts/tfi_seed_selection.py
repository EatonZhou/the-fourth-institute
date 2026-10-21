"""Select optional cases from an inventoried, exporter-generated seed.

Only the exporter's literal INSERT format is accepted. DDL and retained rows
stay byte-for-byte intact; this never connects to an installed database.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

LENIA_PROJECT = "元胞自动机研究"
INSERT = re.compile(r'^INSERT INTO "([^"]+)"\."([^"]+)" \(([^\n]+?)\) VALUES \(', re.M)
LITERAL = re.compile(r"\s*(?:E'(?P<escaped>(?:''|\\.|[^'\\])*)'|'(?P<string>(?:''|[^'])*)'|(?P<scalar>NULL|true|false|-?\d+(?:\.\d+)?))(?:\s*::[a-zA-Z_][a-zA-Z_0-9 ]*)?\s*", re.S)


def decode_string(value, escaped):
    value = value.replace("''", "'")
    if not escaped:
        return value
    escapes = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "\\": "\\", "'": "'"}
    return re.sub(r"\\(.)", lambda m: escapes.get(m[1], m[1]), value, flags=re.S)


def inserts(text):
    """Parse literal rows while skipping INSERT-looking text inside strings."""
    position = 0
    while match := INSERT.search(text, position):
        if re.search(r'^INSERT\s', text[position:match.start()], re.M):
            raise ValueError('Unsupported seed INSERT format')
        columns = re.findall(r'"([^"]+)"', match[3])
        values, position = [], match.end()
        while True:
            token = LITERAL.match(text, position)
            if token is None:
                raise ValueError(f"Unsupported seed SQL literal at {position}")
            if token['scalar'] is not None:
                value = {"NULL": None, "true": True, "false": False}.get(token['scalar'], token['scalar'])
            else:
                value = decode_string(token['escaped'] if token['escaped'] is not None else token['string'],
                                      token['escaped'] is not None)
                if value.startswith(('{', '[')):
                    try:
                        value = json.loads(value)
                    except ValueError:
                        pass
            values.append(value)
            position = token.end()
            if text[position:position + 2] == ');':
                position += 2
                break
            if text[position:position + 1] != ',':
                raise ValueError(f"Unsupported seed SQL row at {position}")
            position += 1
        if len(columns) != len(values):
            raise ValueError("Seed SQL columns and values differ")
        yield match.start(), position, (match[1], match[2]), dict(zip(columns, values))
    if re.search(r'^INSERT\s', text[position:], re.M):
        raise ValueError('Unsupported seed INSERT format')


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)


def selection(sql, manifest):
    rows = list(inserts(sql))
    if not rows:
        raise ValueError("Seed has no supported export rows")
    removed = {LENIA_PROJECT}
    # Follow ownership from project -> lineage -> node/resident -> workspace,
    # and from project -> published workbench and resource registry document.
    owners = {
        ('lineage', 'lineages'): ('project_id', 'lineage_id'),
        ('lineage', 'lineage_nodes'): ('lineage_id', 'node_id'),
        ('lineage', 'residents'): ('resident_id', 'owned_workspace_id'),
        ('lineage', 'function_blocks'): ('lineage_id', 'function_block_id'),
        ('lineage', 'families'): ('lineage_id', 'family_id'),
        ('lineage', 'lineage_edges'): ('lineage_id', 'edge_id'),
        ('workbenches', 'projects'): ('project_id', 'app_id'),
    }
    while True:
        before = len(removed)
        for _, _, table, row in rows:
            if table in owners:
                owner, identity = owners[table]
                if row.get(owner) in removed and row.get(identity):
                    removed.add(row[identity])
            if table == ('resource_registry', 'registry_documents'):
                document = row['document_json']
                if isinstance(document, dict):
                    projects = document.get('project_ids', []) or [document.get('project') or document.get('project_id')]
                    if projects and set(projects) <= {LENIA_PROJECT}:
                        removed.add(row['document_id'])
        if len(removed) == before:
            break
    discarded, paths = [], set()
    for start, end, table, row in rows:
        # SQL relationships and JSON declarations are authoritative. Prose
        # mentioning a case is not an ownership edge.
        if any(value in removed for value in strings(row)):
            discarded.append((start, end))
            if table == ('workspace', 'workspaces'):
                paths.add(row['path'])
            if table == ('resource_registry', 'registry_documents'):
                document = row['document_json']
                if isinstance(document, dict):
                    paths.update(v for k, v in document.items()
                                 if k in {'path', 'root', 'folder', 'workspace_path'} and isinstance(v, str))
            if table == ('workbenches', 'application_packages'):
                build = row.get('build')
                if isinstance(build, dict) and build.get('artifact_hash'):
                    paths.add('${staging_root}/shared/projection_applications/' + build['artifact_hash'])
    paths.add('${share_root}/Projects/' + LENIA_PROJECT)
    paths.update('${share_root}/Agents/' + x for x in removed if x.startswith('lineage_'))
    paths.update('${share_root}/Codes/' + x for x in removed if x.startswith('repo_'))
    paths.update('${share_root}/Articles/' + x for x in removed if x.startswith('article_'))
    relative = set()
    for path in paths:
        for root, folder in manifest['files'].items():
            prefix = '${' + root + '}/'
            if path.startswith(prefix):
                candidate = Path(folder) / path[len(prefix):]
                if '..' in candidate.parts or candidate.is_absolute():
                    raise ValueError('Unsafe case file path')
                relative.add(candidate.as_posix())
    fragments, previous = [], 0
    for start, end in discarded:
        fragments.append(sql[previous:start])
        previous = end
    fragments.append(sql[previous:])
    return ''.join(fragments), removed, relative


def filter_manifest(value, removed):
    def excluded(item):
        candidates = item.values() if isinstance(item, dict) else [item]
        return any(isinstance(v, str) and (v in removed or any(part in removed for part in v.split('/')))
                   for v in candidates)
    if isinstance(value, list):
        return [filter_manifest(v, removed) for v in value
                if not excluded(v)]
    if isinstance(value, dict):
        return {k: filter_manifest(v, removed) for k, v in value.items() if k not in removed}
    return value


def plan(seed, manifest, *, include_examples):
    if include_examples:
        return manifest, None, set()
    sql_path = seed / manifest['database']['path']
    sql, removed, paths = selection(sql_path.read_text(encoding='utf-8'), manifest)
    result = filter_manifest(manifest, removed)
    result['database']['sha256'] = hashlib.sha256(sql.encode('utf-8')).hexdigest()
    snapshot = json.dumps(result.get('selection_snapshot', {}), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    result['selection_sha256'] = hashlib.sha256(snapshot.encode()).hexdigest()
    result['preset_cases'] = []
    return result, sql, paths
