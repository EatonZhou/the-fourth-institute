"""Explicit, one-time avatar store merge; never used by runtime reads.

Plan against operator-supplied roots and exact source schemas, then apply the
frozen plan under the same subject locks as AvatarStore. Existing explicit
choices win. Only the unambiguous Resident first-draw record can be replaced.
No path, historical namespace, account or environment alias is inferred.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import uuid

from PIL import Image

from management_common import secure_paths as fs
from management_common.file_lock import try_lock_descriptor, unlock_descriptor
from management_portal.avatar_store import MAX_IMAGE_BYTES, ScopeAvatarStore
from management_portal.resident_avatar import ResidentAvatarStore
from management_portal.user_avatar import UserAvatarStore

STORES = {'resident': ResidentAvatarStore, 'user': UserAvatarStore, 'scope': ScopeAvatarStore}
PLAN_SCHEMA = 'tfi.avatar-migration-plan.v1'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')


def _sha(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def _read(directory, name, limit=MAX_IMAGE_BYTES):
    try:
        descriptor = fs.open_child(directory, name, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('Avatar input must be a bounded regular file')
        content = stream.read(limit + 1)
        if len(content) > limit:
            raise ValueError('Avatar input exceeds size limit')
        return content


def _read_path(root, name):
    try:
        with fs.open_directory(root) as directory:
            return _read(directory, name)
    except FileNotFoundError:
        return None


def _atomic_write(directory, name, content):
    temporary = '.avatar-migrate-' + uuid.uuid4().hex
    descriptor = fs.open_child(directory, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            fs.prepare_child_permissions(directory, temporary, name, 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        fs.replace_child(directory, temporary, name)
        if os.name != 'nt':
            os.fsync(directory)
    finally:
        try:
            fs.unlink_child(directory, temporary)
        except FileNotFoundError:
            pass


def _save_evidence(path, content):
    path = Path(os.path.abspath(path))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with fs.open_directory(path.parent) as directory:
        existing = _read(directory, path.name, 64 * 1024 * 1024)
        if existing is not None:
            if existing != content:
                raise ValueError('Existing backup differs; use a different backup directory')
            return
        _atomic_write(directory, path.name, content)


def _roots(config):
    seen = set()
    stores = config.get('stores')
    if not isinstance(stores, list) or not stores:
        raise ValueError('Explicit stores are required')
    result = []
    for item in stores:
        kind = item['kind']
        if kind not in STORES or kind in seen:
            raise ValueError('Each supported avatar kind may appear only once')
        seen.add(kind)
        source, target = (Path(os.path.abspath(Path(item[key]).expanduser())) for key in ('source_root', 'target_root'))
        if source == target or source in target.parents or target in source.parents:
            raise ValueError('Avatar roots must be separate, non-nested directories')
        schemas = item['source_schemas']
        if not isinstance(schemas, list) or not schemas or not all(isinstance(x, str) and x for x in schemas):
            raise ValueError('Exact source schemas are required')
        result.append({'kind': kind, 'source_root': str(source), 'target_root': str(target),
                       'source_schemas': schemas, 'restore_automatic': item.get('restore_automatic') is True})
    return result


def _validate(store, filename, raw, schemas):
    record = json.loads(raw)
    if not isinstance(record, dict) or record.get('schema') not in schemas:
        raise ValueError('Source schema is not explicitly selected')
    subject = record.get(store.identity_field)
    if store.stem(subject) + '.json' != filename:
        raise ValueError('Avatar filename and identity disagree')
    allowed = {'schema', store.identity_field, 'kind', 'sprite_index', 'sprite_rarity', 'version'}
    if set(record) - allowed:
        raise ValueError('Unrecognized avatar fields need operator review')
    canonical = {**record, 'schema': store.schema}
    avatar = store._avatar(subject, canonical)
    desired = {'schema': store.schema, store.identity_field: subject,
               **{k: v for k, v in avatar.items() if k != 'url'}}
    return subject, record, desired


def _image(store, subject, record, reader):
    if record.get('kind') != 'upload':
        return None, None
    name = store._image_path(subject, record['version']).name
    data = reader(name)
    if data is None or _sha(data) != record['version']:
        raise ValueError('Source image is absent or differs from its version')
    with Image.open(io.BytesIO(data)) as image:
        if image.format != 'PNG' or not all(1 <= side <= 256 for side in image.size) or getattr(image, 'n_frames', 1) != 1:
            raise ValueError('Source image is not a normalized avatar PNG')
        image.verify()
    return name, data


def build_plan(config):
    """Read both stores, without creating roots or drawing avatars."""
    roots = _roots(config)
    entries = []
    for item in roots:
        source, target = Path(item['source_root']), Path(item['target_root'])
        store = STORES[item['kind']](source)
        if not source.exists():
            continue
        with fs.open_directory(source) as directory:
            with fs.scandir(directory) as listing:
                names = sorted(entry.name for entry in listing if entry.name.endswith('.json'))
            for name in names:
                entry = {'kind': item['kind'], 'file': name}
                try:
                    raw = _read(directory, name)
                    subject, record, desired = _validate(store, name, raw, item['source_schemas'])
                    image_name, image = _image(store, subject, record, lambda n: _read(directory, n))
                    current = _read_path(target, name)
                    action = 'copy_missing'
                    if current is not None:
                        _, target_record, target_value = _validate(store, name, current, [store.schema])
                        if target_value == desired:
                            action = 'preserve'
                            if image_name and _read_path(target, image_name) is None:
                                action = 'repair_image'
                            elif image_name and _sha(_read_path(target, image_name)) != record['version']:
                                raise ValueError('Current image has conflicting bytes')
                        elif (item['kind'] == 'resident' and item['restore_automatic']
                              and set(target_record) == {'schema', 'resident_id', 'sprite_index'}):
                            action = 'restore_automatic'
                        else:
                            action = 'preserve'
                    entry.update(subject=subject, action=action, source_sha256=_sha(raw),
                                 target_sha256=_sha(current), record=desired,
                                 image=image_name, image_sha256=_sha(image),
                                 target_image_sha256=_sha(_read_path(target, image_name)) if image_name else None)
                    if action == 'repair_image':
                        # Repair image bytes only; keep the restored record byte for byte.
                        entry['record'] = json.loads(current)
                    if action == 'restore_automatic':
                        old_time = fs.stat_child(directory, name).st_mtime_ns
                        if old_time > (target / name).stat().st_mtime_ns:
                            entry['action'] = 'preserve'
                            entry['reason'] = 'Source is newer than the automatic target; ambiguous chronology'
                except (ValueError, OSError, TypeError, KeyError) as exc:
                    entry.update(action='invalid', reason=str(exc))
                entries.append(entry)
    plan = {'schema': PLAN_SCHEMA, 'stores': roots, 'entries': entries}
    plan['sha256'] = _sha(_json(plan))
    return plan


@contextmanager
def _target_directory(root):
    root = Path(root)
    # Require the deployment parent to exist; never invent a deployment path.
    with fs.open_directory(root.parent) as parent:
        try:
            if os.name == 'nt':
                (fs.directory_path(parent) / root.name).mkdir(mode=0o700)
            else:
                os.mkdir(root.name, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
        with fs.open_directory(root) as directory:
            yield directory


def _apply_entry(item, entry, backup):
    if entry['action'] == 'invalid':
        return 'invalid'
    if entry['action'] == 'preserve':
        return 'preserved'
    target, source = Path(item['target_root']), Path(item['source_root'])
    store = STORES[item['kind']](target)
    subject, _, _ = _validate(store, entry['file'], _json(entry['record']), [store.schema])
    desired = _json(entry['record'])
    if entry['action'] not in {'copy_missing', 'restore_automatic', 'repair_image'}:
        raise ValueError('Unrecognized migration action')
    with _target_directory(target) as directory:
        lock = fs.open_child(directory, store.stem(subject) + '.lock', os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if not try_lock_descriptor(lock):
                return 'busy'
            try:
                current = _read(directory, entry['file'])
                image = _read(directory, entry['image']) if entry['image'] else None
                image_ready = not entry['image'] or _sha(image) == entry['image_sha256']
                if (current == desired or (entry['action'] == 'repair_image' and _sha(current) == entry['target_sha256'])) and image_ready:
                    return 'already_applied'
                if _sha(current) != entry['target_sha256']:
                    return 'conflict'
                if image is not None and not image_ready:
                    return 'conflict'
                with fs.open_directory(source) as origin:
                    raw = _read(origin, entry['file'])
                    if _sha(raw) != entry['source_sha256']:
                        return 'source_changed'
                    _, original, converted = _validate(store, entry['file'], raw, item['source_schemas'])
                    image_name, data = _image(store, subject, original, lambda name: _read(origin, name))
                    if image_name != entry['image'] or _sha(data) != entry['image_sha256']:
                        return 'source_changed'
                    if entry['action'] != 'repair_image' and converted != entry['record']:
                        raise ValueError('Plan does not match source avatar')
                folder = backup / item['kind'] / store.stem(subject)
                _save_evidence(folder / 'source.json', raw)
                _save_evidence(folder / 'before.json', _json({'target_sha256': entry['target_sha256'],
                    'image_sha256': entry['target_image_sha256']}))
                if current is not None:
                    _save_evidence(folder / 'target.json', current)
                if data is not None:
                    _save_evidence(folder / 'source.png', data)
                if not fs.directory_unchanged(directory, target):
                    return 'conflict'
                if data is not None and not image_ready:
                    _atomic_write(directory, image_name, data)
                if entry['action'] != 'repair_image':
                    _atomic_write(directory, entry['file'], desired)
                return 'applied'
            finally:
                unlock_descriptor(lock)
        finally:
            os.close(lock)


def apply_plan(plan, backup_root):
    """Busy/changed entries remain untouched; partial runs can use the same plan."""
    body = {k: v for k, v in plan.items() if k != 'sha256'}
    if plan.get('schema') != PLAN_SCHEMA or _sha(_json(body)) != plan.get('sha256'):
        raise ValueError('Invalid plan digest or schema')
    stores = {item['kind']: item for item in _roots(plan)}
    backup = Path(os.path.abspath(backup_root)) / plan['sha256']
    for item in stores.values():
        for key in ('source_root', 'target_root'):
            root = Path(item[key])
            if backup == root or root in backup.parents or backup in root.parents:
                raise ValueError('Backups must be outside avatar roots')
    _save_evidence(backup / 'plan.json', _json(plan))
    result = {'schema': 'tfi.avatar-migration-result.v1', 'plan_sha256': plan['sha256'],
              'backup': str(backup), 'entries': []}
    for entry in plan['entries']:
        outcome = {'kind': entry['kind'], 'file': entry['file'], 'action': entry['action']}
        try:
            outcome['status'] = _apply_entry(stores[entry['kind']], entry, backup)
        except (ValueError, OSError, TypeError, KeyError) as exc:
            outcome.update(status='error', error=str(exc))
        result['entries'].append(outcome)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    plan = sub.add_parser('plan')
    plan.add_argument('--config', type=Path, required=True)
    plan.add_argument('--output', type=Path, required=True)
    apply = sub.add_parser('apply')
    apply.add_argument('--plan', type=Path, required=True)
    apply.add_argument('--backup', type=Path, required=True)
    apply.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'plan':
        result = build_plan(json.loads(args.config.read_text()))
    else:
        result = apply_plan(json.loads(args.plan.read_text()), args.backup)
    _save_evidence(args.output, _json(result))
    from collections import Counter
    print(json.dumps(dict(Counter(e.get('status', e['action']) for e in result['entries']))))
    if any(e.get('status', e['action']) in {'error', 'conflict', 'invalid', 'busy', 'source_changed'} for e in result['entries']):
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
