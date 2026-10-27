"""The deployment configuration owner for installed Portal instances.

config/target.json is the only deployment input. installation.json contains
status plus the digest of the applied configuration; target exists only in the
in-memory view returned to installer operations. Legacy v1 records are import
inputs, never an alternative authority when a target file exists.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import os
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar

from management_common.file_lock import lock_stream

STATE_SCHEMA = "tfi.release-installation.v2"
LEGACY_SCHEMA = "tfi.release-installation.v1"
TARGET_FILE = "config/target.json"
STATE_FILE = "installation.json"
LOCK_FILE = '.configuration.lock'
_HELD_LOCKS = ContextVar('instance_configuration_locks', default=frozenset())


@contextmanager
def configuration_lock(root, *, create=False):
    """Keep target and state reads coherent across a configuration commit."""
    root = Path(root).resolve()
    held = _HELD_LOCKS.get()
    if root in held:
        yield
        return
    path = root / LOCK_FILE
    if path.is_symlink():
        raise ValueError('Configuration lock must not be a symlink')
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            descriptor = os.open(path, os.O_RDWR)
        else:
            if os.name != 'nt' and os.geteuid() == 0:
                owner = (root / TARGET_FILE).stat() if (root / TARGET_FILE).exists() else root.stat()
                os.fchown(descriptor, owner.st_uid, owner.st_gid)
        stream = os.fdopen(descriptor, 'r+b')
    elif path.exists():
        stream = path.open('r+b' if os.name == 'nt' else 'rb')
    else:
        yield
        return
    with stream, lock_stream(stream):
        token = _HELD_LOCKS.set(held | {root})
        try:
            yield
        finally:
            _HELD_LOCKS.reset(token)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def configuration_digest(target):
    return hashlib.sha256(json.dumps(target, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"Configuration must not be a symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            from management_common.portal_paths import _preserve_config_permissions
            _preserve_config_permissions(temporary, path)
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def read_target(root):
    root = Path(root)
    target = read_json(root / TARGET_FILE)
    if not isinstance(target, dict) or not target.get("install_root"):
        raise ValueError("Invalid instance deployment configuration")
    return target


def _read_installation(root):
    root = Path(root)
    state = read_json(root / STATE_FILE)
    schema = state.get("schema")
    if schema not in {STATE_SCHEMA, LEGACY_SCHEMA}:
        raise ValueError("Unsupported installation state schema")
    target_path = root / TARGET_FILE
    if schema == LEGACY_SCHEMA:
        # Explicitly bounded import contract for existing v1 installations and
        # their backups. A disagreement is an error, never precedence guessing.
        target = read_target(root) if target_path.is_file() else state["target"]
        if target != state["target"]:
            raise ValueError("Legacy deployment configuration disagrees with installation; import requires reconciliation")
    else:
        if "target" in state:
            raise ValueError("Installation v2 must not duplicate deployment configuration")
        target = read_target(root)
        if state.get("configuration_sha256") != configuration_digest(target):
            raise ValueError("Unapplied instance configuration; use tfi_instance_config plan/apply")
    return {**state, "target": target}


def read_installation(root):
    root = Path(root)
    lock = root / LOCK_FILE
    if not lock.exists():
        try:
            state = _read_installation(root)
        except (ValueError, FileNotFoundError):
            if not lock.exists():
                raise
        else:
            if not lock.exists():
                return state
        # The first adoption may have created its lock during this read.
    with configuration_lock(root):
        return _read_installation(root)


def write_installation(root, state):
    with configuration_lock(root, create=True):
        return _write_installation(root, state)


def _write_installation(root, state):
    root = Path(root)
    target = state["target"]
    if (state.get('schema') == LEGACY_SCHEMA and 'development' not in target
            and (root / 'config/development.json').is_file()):
        raise ValueError('Import this paired instance with tfi_instance_config adopt before updating its state')
    path = root / TARGET_FILE
    if path.exists():
        if read_target(root) != target:
            raise ValueError("Deployment changes must use tfi_instance_config apply")
    else:
        atomic_json(path, target)
    record = {key: value for key, value in state.items() if key != "target"}
    record.update(schema=STATE_SCHEMA, configuration_sha256=configuration_digest(target))
    atomic_json(root / STATE_FILE, record)
    # Keep installer comparisons consistent with the persisted view.
    state.update(schema=STATE_SCHEMA, configuration_sha256=record["configuration_sha256"])


def installed_root(paths_config):
    """Identify an installed instance from a supported configuration locator."""
    config = Path(paths_config)
    root = config.parent.parent
    # paths.json is only accepted as the locator inherited by already running
    # services. No installed reader needs that file to exist or reads its data.
    locators = {root / TARGET_FILE, root / 'config/site.json', root / 'config/paths.json'}
    return root if config in locators and (root / STATE_FILE).is_file() else None


def source_roots(target):
    source = target.get('shared_code_root', str(Path(target['install_root']) / 'code'))
    development = target.get('development') or {}
    return {'source_root': development.get('dev_code', source),
            'release_root': development.get('release_code', source)}


def installed_source_roots(config):
    root = installed_root(config)
    if root is None:
        return {}
    state = read_installation(root)
    target = state['target']
    if ('development' not in target and state['schema'] == LEGACY_SCHEMA
            and (root / 'config/development.json').is_file()):
        target = {**target, 'development': read_json(root / 'config/development.json')}
    return source_roots(target)


def read_development(root):
    root = Path(root)
    state = read_installation(root)
    target = state["target"]
    if "development" in target:
        return target["development"]
    if state["schema"] == LEGACY_SCHEMA:
        return read_json(root / "config/development.json")
    raise ValueError("This instance has no paired development deployment")


def has_development(root):
    root = Path(root)
    if not (root / STATE_FILE).is_file():
        return False
    state = read_installation(root)
    return ('development' in state['target'] or
            (state['schema'] == LEGACY_SCHEMA and (root / 'config/development.json').is_file()))
