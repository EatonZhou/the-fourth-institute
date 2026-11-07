"""The OS user's instance registry and cross-instance allocation checks."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import tempfile

from .file_lock import lock_stream, try_lock_descriptor, try_named_lock
from .instance_manifest import AREAS, InstanceManifest, absolute_path, load, overlaps


def user_home() -> Path:
    if os.name == "nt":
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 40, None, 0, buffer):
            raise OSError("Cannot resolve the operating system user profile")
        return Path(buffer.value).resolve()
    import pwd
    return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()


def registry_path() -> Path:
    return user_home() / ".config/tfi/instances.json"


@contextmanager
def operation_lock(root: Path, name: str):
    """Hold an instance operation across a root move, without leaving host files."""
    if name not in {".configuration.lock", ".portal-control.lock"}:
        raise ValueError("Unknown instance operation lock")
    if os.name == "nt":
        identity = str(user_home()) + ":" + load(root).instance_id + ":" + name
        with try_named_lock(identity) as acquired:
            yield acquired
    else:
        path = Path(root) / name
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "r+b") as stream:
            yield try_lock_descriptor(stream.fileno())


def default_root(name: str) -> Path:
    from .instance_manifest import _NAME
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("Invalid instance name")
    home = user_home()
    if sys.platform == "darwin":
        base = home / "Library/Application Support/TFI"
    elif os.name == "nt":
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 28, None, 0, buffer):
            raise OSError("Cannot resolve local application data")
        base = Path(buffer.value) / "TFI"
    else:
        base = home / ".local/share/tfi"
    return base / "instances" / name


def read_registry() -> dict[str, str]:
    from .instance_manifest import _NAME
    source = registry_path()
    if source.is_symlink():
        raise ValueError("Instance registry cannot be a symbolic link")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise ValueError("Instance registry must map names to roots")
    for name, root in value.items():
        if not _NAME.fullmatch(name):
            raise ValueError("Invalid registered instance name")
        absolute_path(root)
    if len(set(value.values())) != len(value):
        raise ValueError("An instance root cannot have multiple registered names")
    return value


def find(name: str) -> InstanceManifest:
    try:
        root = read_registry()[name]
    except KeyError:
        raise ValueError("Instance is not registered on this host") from None
    instance = load(root)
    if instance.name != name:
        raise ValueError("Instance manifest name disagrees with the host registry")
    return instance


def ports(instance: InstanceManifest) -> set[int]:
    network = instance.document["network"]
    return {network[key] for key in ("entry_port", "postgres_port", "nats_port")} | {
        entry["web_port"] for entry in instance.deployments.values()} | {
        entry['port'] for entry in network.get('listeners', {}).values()}


def validate_allocation(instance: InstanceManifest, registry: dict[str, str] | None = None) -> None:
    registry = read_registry() if registry is None else registry
    owned = [instance.root, *(instance.area(area) for area in AREAS)]
    for name, root in registry.items():
        if name == instance.name and root == str(instance.root):
            continue
        if name == instance.name:
            raise ValueError("Instance name is already registered")
        other = load(root)  # Fail closed on unreadable registrations.
        if other.instance_id == instance.instance_id:
            raise ValueError("This instance identity is already registered at another location")
        if other.service_prefix == instance.service_prefix or ports(other) & ports(instance):
            raise ValueError("Instance service prefix or port is already allocated")
        other_owned = [other.root, *(other.area(area) for area in AREAS)]
        if any(overlaps(a, b) for a in owned for b in other_owned):
            raise ValueError("Instance directories overlap another instance")


def registered_reference(instance: InstanceManifest, value: str | Path) -> str:
    """Storage boundary for Workspace, repository and other directory registration."""
    path = absolute_path(value)
    for name, root in read_registry().items():
        if name == instance.name and root == str(instance.root):
            continue
        other = load(root)
        if any(overlaps(path, parent) for parent in [other.root, *(other.area(a) for a in AREAS)]):
            raise ValueError("Cannot register a directory belonging to another instance")
    return instance.reference(path)


@contextmanager
def _locked_registry():
    source = registry_path()
    source.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = source.with_suffix(".lock")
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "r+b") as stream, lock_stream(stream):
        yield source


def _write(source: Path, registry: dict[str, str]) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".instances-", dir=source.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(registry, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, source)
        if os.name != "nt":
            directory = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def register(instance: InstanceManifest) -> None:
    from .instance_manifest import validate
    instance = validate(instance.root, instance.document)
    with _locked_registry() as source:
        registry = read_registry()
        validate_allocation(instance, registry)
        registry[instance.name] = str(instance.root)
        _write(source, registry)


def unregister(instance: InstanceManifest) -> None:
    with _locked_registry() as source:
        registry = read_registry()
        if registry.get(instance.name) != str(instance.root):
            raise ValueError("Instance registration changed; refusing to unregister")
        del registry[instance.name]
        _write(source, registry)


def relocate_registration(previous: InstanceManifest, updated: InstanceManifest) -> None:
    """Replace one root after a stopped instance has moved, preserving identity."""
    if (previous.name, previous.instance_id, previous.service_prefix) != (
            updated.name, updated.instance_id, updated.service_prefix):
        raise ValueError("Relocation must preserve the instance identity and service prefix")
    if load(updated.root).document != updated.document:
        raise ValueError("Relocated manifest changed before registration")
    with _locked_registry() as source:
        registry = read_registry()
        if registry.get(previous.name) != str(previous.root):
            raise ValueError("Instance registration changed before relocation")
        other = {name: root for name, root in registry.items() if name != previous.name}
        validate_allocation(updated, other)
        registry[updated.name] = str(updated.root)
        _write(source, registry)


def replace_manifest(instance: InstanceManifest, *, expected: dict) -> None:
    """Commit an edited manifest while holding the host's allocation lock."""
    from .configuration_files import atomic_json
    from .instance_manifest import validate
    instance = validate(instance.root, instance.document)
    descriptor = os.open(instance.root / '.configuration.lock', os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(descriptor, 'r+b') as stream, lock_stream(stream), _locked_registry():
        current = load(instance.root)
        if current.document != expected:
            raise ValueError('Instance configuration changed; refresh before applying')
        if current.name != instance.name or current.instance_id != instance.instance_id:
            raise ValueError('Configuration edits cannot replace an instance identity')
        validate_allocation(instance)
        atomic_json(instance.root / 'instance.json', instance.document)


def add_service_component(root: Path, component: str, *, program: dict | None = None,
                          listener: dict | None = None,
                          listener_bind: str | None = None) -> InstanceManifest:
    """Commit a component, its program and listener in one validated transaction.

    Built-in account daemons need no program. Functional services supply their
    complete program here rather than editing a live programs map separately.
    """
    from copy import deepcopy
    from .configuration_files import atomic_json
    from .instance_manifest import validate
    if listener is not None and listener_bind is not None:
        raise ValueError('Choose an explicit listener or an allocated listener bind')
    root = Path(root)
    descriptor = os.open(root / '.configuration.lock', os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(descriptor, 'r+b') as stream, lock_stream(stream), _locked_registry():
        current = load(root)
        document = deepcopy(current.document)
        if component not in document['services']['components']:
            document['services']['components'].append(component)
        if program is not None:
            document['services'].setdefault('programs', {})[component] = deepcopy(program)
        if listener is not None:
            document['network'].setdefault('listeners', {})[component] = deepcopy(listener)
        if listener_bind is not None and component not in document['network'].get('listeners', {}):
            import ipaddress
            import socket
            bind = str(ipaddress.ip_address(listener_bind))
            allocated = ports(current)
            for other_root in read_registry().values():
                allocated.update(ports(load(other_root)))
            for _ in range(64):
                with socket.socket(socket.AF_INET6 if ':' in bind else socket.AF_INET) as probe:
                    probe.bind((bind, 0))
                    port = probe.getsockname()[1]
                if port not in allocated:
                    break
            else:
                raise RuntimeError('No available service listener port')
            document['network'].setdefault('listeners', {})[component] = {'bind': bind, 'port': port}
        updated = validate(root, document)
        validate_allocation(updated)
        if updated.document != current.document:
            atomic_json(root / 'instance.json', updated.document)
        return updated


def remove_service_component(root: Path, component: str) -> None:
    """Forget a service only after its lifecycle owner has removed its unit."""
    from copy import deepcopy
    current = load(root)
    if component not in current.document['services']['components']:
        return
    document = deepcopy(current.document)
    document['services']['components'].remove(component)
    document['services'].get('programs', {}).pop(component, None)
    document['network'].get('listeners', {}).pop(component, None)
    replace_manifest(InstanceManifest(current.root, document), expected=current.document)
