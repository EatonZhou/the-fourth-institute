"""Keep public seed inventory derived from the exact published Git snapshot.

Only changed seed content and changed manifest rows are hashed. Unselected live
Dev edits are never read, staged or overwritten by inventory maintenance.
"""
from pathlib import Path
import hashlib
import json
import subprocess

PREFIX = 'instance/example_instance/seed/'
MANIFEST = PREFIX + 'manifest.json'


def git(root, *arguments, input=None):
    return subprocess.check_output(['git', *arguments], cwd=root, input=input, stderr=subprocess.PIPE)


def blob(root, reference, path):
    return git(root, 'show', (':' if reference == ':' else reference + ':') + path)


def corrected_inventory(root, reference, baseline):
    """Return corrected manifest bytes, or None if no seed closure changed."""
    # Omitting HEAD for a staged diff also supports the repository's first commit.
    args = ['diff', '--name-only', '-z']
    if baseline != 'HEAD' or reference != ':':
        args.append(baseline)
    args += ['--cached'] if reference == ':' else [reference]
    changed = set(filter(None, git(root, *args, '--', PREFIX).decode().split('\0')))
    if not changed:
        return None
    original = blob(root, reference, MANIFEST)
    document = json.loads(original)
    rows = {row['path']: row for row in document['inventory']}
    candidates = {path[len(PREFIX):] for path in changed if path != MANIFEST}
    database = document.get('engineering', {}).get('database', {})
    database_path = database.get('path')
    if MANIFEST in changed:
        try:
            previous = json.loads(blob(root, baseline, MANIFEST))
        except subprocess.CalledProcessError:
            previous = {'inventory': []}
        prior_rows = {row['path']: row for row in previous['inventory']}
        candidates.update(path for path in rows.keys() | prior_rows.keys() if rows.get(path) != prior_rows.get(path))
        if database != previous.get('engineering', {}).get('database', {}) and database_path:
            candidates.add(database_path)
    # The index and committed snapshots include tracked deletions/additions;
    # never use Path.exists() from the concurrently edited worktree.
    if reference == ':':
        entries = git(root, 'ls-files', '--stage', '-z', '--', PREFIX).split(b'\0')
        tracked = {}
        for entry in filter(None, entries):
            metadata, name = entry.split(b'\t', 1)
            mode, _oid, stage = metadata.decode().split()
            if stage != '0':
                raise ValueError('Cannot publish an unmerged seed index')
            tracked[name.decode()] = mode
    else:
        tracked = {entry.split(b'\t', 1)[1].decode(): entry.split(b' ', 1)[0].decode()
                   for entry in filter(None, git(root, 'ls-tree', '-r', '-z', reference, '--', PREFIX).split(b'\0'))}
    for path in sorted(candidates):
        full = PREFIX + path
        if full not in tracked:
            rows.pop(path, None)
            continue
        if tracked[full] not in {'100644', '100755'}:
            raise ValueError('Public seed inventory requires regular files: ' + path)
        content = blob(root, reference, full)
        rows[path] = {'path': path, 'sha256': hashlib.sha256(content).hexdigest(), 'size': len(content)}
    if database_path in candidates:
        if database_path not in rows:
            raise ValueError('Public seed database must remain a tracked regular file: ' + database_path)
        # The engineering declaration and its inventory describe the same SQL
        # bytes. Derive both from the same index/commit, never live Dev content.
        database['sha256'] = rows[database_path]['sha256']
    document['inventory'] = sorted(rows.values(), key=lambda row: row['path'])
    if document == json.loads(original):
        return None
    return (json.dumps(document, ensure_ascii=False, indent=2) + '\n').encode()


def refresh_staged_inventory(root, baseline='HEAD'):
    content = corrected_inventory(root, ':', baseline)
    if content is None:
        return False
    oid = git(root, 'hash-object', '-w', '--stdin', input=content).decode().strip()
    git(root, 'update-index', '--add', '--cacheinfo', '100644', oid, MANIFEST)
    # Leave the live file alone: it may contain later/concurrent edits. The
    # generated staged snapshot is authoritative for this publication only.
    return True


def verify_publication_inventory(root, source, baseline):
    if corrected_inventory(root, source, baseline) is not None:
        raise ValueError('Public seed inventory does not match the committed publication. '
                         'Publish the changed seed paths and manifest together to regenerate their staged inventory; '
                         'Release has not advanced.')
