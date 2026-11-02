"""Upload writer/consumer hooks and reconciliation of directly removed files."""
from contextlib import nullcontext
from datetime import datetime
import json
import os
import stat

from .directory_retention import RetentionStore
from .directory_paths import lexical_path
from .resource_lifecycle import record_allocation
from .secure_paths import directory_unchanged, open_child, open_directory, stat_child


def created(directory, *, upload_id, created_at, connection=None):
    stamp = datetime.fromisoformat(created_at.replace('Z', '+00:00')) if isinstance(created_at, str) else created_at
    directory = lexical_path(directory)
    retention = RetentionStore(connection=connection)
    with nullcontext(connection) if connection is not None else retention.database.transaction() as conn:
        retention.declare(directory, category='unused_upload', owner_id=upload_id,
                          created_at=stamp, connection=conn)
        # Registration and the empty directory become visible together to
        # reconciliation. Missing metadata then means a writer is unfinished.
        with open_directory(directory.anchor, directory.parts[1:], create=True):
            pass
    record_allocation(upload_id)


def consumed(directory, *, owner_id, connection=None):
    RetentionStore(connection=connection).used(directory, owner_id=owner_id, connection=connection)


def deleted(directory, *, connection=None):
    removed = RetentionStore(connection=connection).release_path(directory, connection=connection)
    if not removed:
        raise ValueError('Upload retention registration is missing; complete the offline migration first')
    return True


def _version(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _absent(directory, name):
    try:
        stat_child(directory, name)
    except FileNotFoundError:
        return True
    return False


def _missing_reason(root, name):
    """Only stable absence of a directory or a ready payload proves deletion."""
    if _absent(root, name):
        return 'directory' if _absent(root, name) else None
    descriptor = open_child(root, name, os.O_RDONLY, is_directory=True)
    try:
        directory = os.fstat(descriptor)
        try:
            metadata = open_child(descriptor, 'meta.json', os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
        except FileNotFoundError:
            return None  # Includes the initial metadata write and an unfinished upload.
        with os.fdopen(metadata, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > 65536:
                raise ValueError('Upload metadata must be a small regular file')
            meta = json.loads(stream.read(65537))
            if _version(before) != _version(os.fstat(stream.fileno())):
                return None
        if not isinstance(meta, dict) or meta.get('ready') is not True:
            return None
        filename = meta.get('file_name')
        if (not isinstance(filename, str) or filename in {'', '.', '..'}
                or any(character in filename for character in '/\\\x00')):
            raise ValueError('Upload payload must be one local filename')
        if not _absent(descriptor, filename):
            return None  # Content, size and mtime changes are ordinary edits.
        try:
            # An atomic metadata replacement, a new temporary metadata file or
            # a replaced directory invalidates this observation. Retry next run.
            if (_version(before) != _version(stat_child(descriptor, 'meta.json'))
                    or _version(directory) != _version(stat_child(root, name))):
                return None
            return 'payload' if _absent(descriptor, filename) else None
        except FileNotFoundError:
            return None
    finally:
        os.close(descriptor)


def reconcile_missing(root):
    """Retire registered uploads whose files were removed with system tools.

    The existing cleanup queue removes remaining sidecars. Unreadable roots,
    incomplete metadata and I/O failures never establish payload absence.
    """
    report = {'root_available': False, 'checked': 0, 'released': 0, 'kept': 0,
              'missing_directories': 0, 'missing_payloads': 0, 'errors': []}
    try:
        root = lexical_path(root)
        with open_directory(root) as descriptor:
            report['root_available'] = True
            retention = RetentionStore()
            paths = retention.registered_children(root, categories=('unused_upload', 'object_data'))
            for directory in paths:
                if (directory.parent != root or not directory.name.startswith('upload_')
                        or not directory.name[7:].isalnum()):
                    continue
                report['checked'] += 1
                try:
                    with retention.database.transaction() as connection:
                        # Serialize with registration, consumption, moves and
                        # queue workers. Reservations also protect in-flight moves.
                        retention.queue.assert_available(directory, connection=connection)
                        if not directory_unchanged(descriptor, root):
                            report['root_available'] = False
                            report['errors'].append('Upload root changed during reconciliation')
                            break
                        reason = _missing_reason(descriptor, directory.name)
                        if not directory_unchanged(descriptor, root):
                            report['root_available'] = False
                            report['errors'].append('Upload root changed during reconciliation')
                            break
                        if reason and retention.release_path(directory, connection=connection):
                            report['released'] += 1
                            report['missing_directories' if reason == 'directory' else 'missing_payloads'] += 1
                        else:
                            report['kept'] += 1
                except FileExistsError:
                    report['kept'] += 1  # Another lifecycle operation owns the path.
                except (OSError, ValueError) as error:
                    report['errors'].append(f'{directory.name}: {error}')
    except FileNotFoundError:
        pass  # An absent upload root is not evidence about any individual upload.
    except (OSError, ValueError) as error:
        report['errors'].append(f'upload root: {error}')
    return report
