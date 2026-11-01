"""Instance identity, configuration and file locations.

Only instance.json defines an instance. Callers select it explicitly, by root or
PORTAL_INSTANCE_ROOT. This module does not discover an instance from source, cwd,
HOME, service environment overrides or historical configuration files.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import ipaddress
import json
import os
from pathlib import Path
import re
from stat import S_ISREG
from typing import Any

SCHEMA = "tfi.instance.v1"
AREAS = ("projects", "lineages", "skills", "papers", "uploads", "features", "provider-state")
FIXED_DIRECTORIES = ("secrets", "database", "cache", "runtime")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")


def absolute_path(value: str | Path) -> Path:
    """Require a canonical absolute location, including for not-yet-created files."""
    if not isinstance(value, (str, Path)) or not str(value) or '\0' in str(value):
        raise ValueError("Expected a canonical absolute path")
    path = Path(value)
    if not path.is_absolute() or str(path) != str(value) or path.resolve() != path:
        raise ValueError("Path must be absolute and canonical, without symbolic links")
    if os.name == 'nt' and path.parts[1:]:
        from .secure_paths import relative_parts
        relative_parts('/'.join(path.parts[1:]))
    return path


def overlaps(first: Path, second: Path) -> bool:
    return first.is_relative_to(second) or second.is_relative_to(first)


def _object(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError(f"{label} must be nonempty text")
    return value


@dataclass(frozen=True)
class InstanceManifest:
    root: Path
    document: dict

    @property
    def name(self) -> str:
        return self.document["name"]

    @property
    def instance_id(self) -> str:
        return self.document["instance_id"]

    @property
    def deployments(self) -> dict:
        return self.document["deployments"]

    @property
    def service_prefix(self) -> str:
        return self.document["services"]["prefix"]

    def service_name(self, component: str) -> str:
        if component not in self.document["services"]["components"]:
            raise ValueError("Service component is not registered in this instance")
        return f"{self.service_prefix}-{component}"

    def directory(self, name: str) -> Path:
        if name not in FIXED_DIRECTORIES:
            raise KeyError(name)
        return self.root / name

    def area(self, name: str) -> Path:
        if name not in AREAS:
            raise KeyError(name)
        return Path(self.document.get("areas", {}).get(name, self.root / "data" / name))

    def code_root(self, deployment: str | None = None) -> Path:
        # Shared services run the Dev source when this instance has one.
        selected = deployment or ("dev" if "dev" in self.deployments else "release")
        return Path(self.deployments[selected]["code_root"])

    def service_code(self, component: str) -> Path:
        self.service_name(component)
        deployment = {"web": "release", "dev-web": "dev"}.get(component)
        return self.code_root(deployment)

    def service_program(self, component: str) -> dict | None:
        self.service_name(component)
        return self.document['services'].get('programs', {}).get(component)

    def service_environment(self, component: str) -> dict[str, str]:
        self.service_name(component)
        result = {"PORTAL_INSTANCE_ROOT": str(self.root)}
        if component in {"web", "dev-web"}:
            deployment = "dev" if component == "dev-web" else "release"
            if deployment not in self.deployments:
                raise ValueError("Web deployment is not registered in this instance")
            result["PORTAL_DEPLOYMENT"] = deployment
        return result

    def internal_web_origin(self) -> str:
        """Resident ISA clients use the Web matching the shared backend code."""
        selected = 'dev' if 'dev' in self.deployments else 'release'
        bind = ipaddress.ip_address('127.0.0.1' if len(self.deployments) > 1 else self.document['network']['bind'])
        host = ('::1' if bind.version == 6 else '127.0.0.1') if bind.is_unspecified else str(bind)
        if ':' in host:
            host = '[' + host + ']'
        return f"http://{host}:{self.deployments[selected]['web_port']}"

    def listener(self, component: str) -> dict:
        self.service_name(component)
        try:
            return self.document['network']['listeners'][component]
        except KeyError:
            raise ValueError('Configure the registered service listener in the instance manifest') from None

    def service_origin(self, component: str) -> str:
        listener = self.listener(component)
        bind = ipaddress.ip_address(listener['bind'])
        host = ('::1' if bind.version == 6 else '127.0.0.1') if bind.is_unspecified else str(bind)
        if ':' in host:
            host = '[' + host + ']'
        return f"http://{host}:{listener['port']}"

    def deployment_runtime(self, deployment: str) -> Path:
        if deployment not in self.deployments:
            raise ValueError("Deployment is not registered in this instance")
        return self.directory("runtime") / "deployments" / deployment

    def resolve(self, reference: str) -> Path:
        """Resolve an area reference or a canonical host path; reject escapes."""
        if not isinstance(reference, str):
            raise ValueError("File reference must be text")
        area, separator, relative = reference.partition(":")
        if separator and area in AREAS:
            from .secure_paths import relative_parts
            parts = relative_parts(relative) if relative else ()
            root = self.area(area)
            result = root.joinpath(*parts).resolve()
            if not result.is_relative_to(root):
                raise ValueError("Area reference escapes through a symbolic link")
            return result
        return absolute_path(reference)

    def reference(self, path: str | Path) -> str:
        """Encode an existing or prospective file, without changing its ownership."""
        canonical = absolute_path(path)
        for area in AREAS:
            root = self.area(area)
            if canonical.is_relative_to(root):
                relative = canonical.relative_to(root).as_posix()
                return area + ":" + ("" if relative == "." else relative)
        if canonical.is_relative_to(self.root):
            raise ValueError("Only area files may be registered as instance content")
        # Registered locations use the same separator on every platform, as do
        # area references and the database's descendant-prefix comparisons.
        return canonical.as_posix()

    def site_settings(self) -> dict:
        path = self.root / "site.json"
        if path.is_symlink():
            raise ValueError("Site settings must not be a symbolic link")
        return _object(json.loads(path.read_text(encoding="utf-8")), "site.json")


def validate(root: str | Path, document: dict) -> InstanceManifest:
    root = absolute_path(root)
    doc = _object(document, "instance.json")
    required = {"schema", "instance_id", "name", "created_at", "seed_version", "deployments", "network", "services", "dependencies"}
    if not required <= doc.keys() or doc.keys() - required - {"areas"}:
        raise ValueError("Instance manifest has missing or unknown fields")
    if doc["schema"] != SCHEMA:
        raise ValueError("Unsupported instance manifest schema")
    for field in ("instance_id", "name", "created_at", "seed_version"):
        _text(doc[field], field)
    if not _NAME.fullmatch(doc["name"]):
        raise ValueError("Invalid instance name")
    deployments = _object(doc["deployments"], "deployments")
    if "release" not in deployments or deployments.keys() - {"release", "dev"}:
        raise ValueError("An instance requires Release and optionally Dev")
    ports = []
    code_roots = []
    for name, deployment in deployments.items():
        entry = _object(deployment, "deployment")
        if set(entry) != {"kind", "code_root", "web_port"} or entry["kind"] != name:
            raise ValueError("Invalid deployment definition")
        code = absolute_path(entry["code_root"])
        if overlaps(root, code.parent):
            raise ValueError("Instance root must be outside the code directory")
        code_roots.append(code.parent)
        ports.append(entry["web_port"])
    network = _object(doc["network"], "network")
    required_network = {"bind", "entry_port", "postgres_port", "nats_port"}
    if not required_network <= network.keys() or network.keys() - required_network - {'listeners'}:
        raise ValueError("Invalid network definition")
    ipaddress.ip_address(network["bind"])
    ports += [network["postgres_port"], network["nats_port"]]
    listeners = _object(network.get('listeners', {}), 'service listeners')
    for listener in listeners.values():
        if not isinstance(listener, dict) or set(listener) != {'bind', 'port'}:
            raise ValueError('Service listener requires bind and port')
        ipaddress.ip_address(listener['bind'])
        ports.append(listener['port'])
    # A single deployment may itself serve the public entry; a gateway uses a
    # separate port and must be a managed component.
    entry_port = network["entry_port"]
    if len(deployments) > 1 or entry_port != deployments["release"]["web_port"]:
        ports.append(entry_port)
    if any(type(p) is not int or not 1024 <= p <= 65535 for p in ports) or len(set(ports)) != len(ports):
        raise ValueError("Instance ports must be distinct nonprivileged port numbers")
    services = _object(doc["services"], "services")
    if not {'prefix', 'components'} <= services.keys() or services.keys() - {'prefix', 'components', 'programs'} or not re.fullmatch(r"tfi-[a-z0-9][a-z0-9-]{2,39}", services["prefix"]):
        raise ValueError("Invalid service prefix")
    components = services["components"]
    if (not isinstance(components, list) or not components
            or any(not isinstance(c, str) or not re.fullmatch(r"[a-z][a-z0-9-]*(?:@[A-Za-z0-9_-]+)?", c) for c in components)
            or len(set(components)) != len(components)):
        raise ValueError("Invalid service component list")
    if len(deployments) > 1 and "gateway" not in components:
        raise ValueError("Paired deployments require a managed gateway")
    if listeners.keys() - set(components):
        raise ValueError('Service listeners must belong to registered components')
    dependencies = _object(doc["dependencies"], "dependencies")
    if dependencies.keys() - {"postgres", "backup"} or "postgres" not in dependencies:
        raise ValueError("Only data-bound dependencies belong in the instance manifest")
    postgres = _object(dependencies["postgres"], "PostgreSQL dependency")
    if set(postgres) != {"bin", "lib", "share"}:
        raise ValueError("PostgreSQL dependency requires bin, lib and share")
    for value in postgres.values():
        absolute_path(value)
    if "backup" in dependencies:
        absolute_path(dependencies["backup"])
    areas = _object(doc.get("areas", {}), "areas")
    if areas.keys() - set(AREAS):
        raise ValueError("Unknown data area")
    for value in areas.values():
        absolute_path(value)
    instance = InstanceManifest(root, doc)
    programs = _object(services.get('programs', {}), 'service programs')
    for component, program in programs.items():
        instance.service_name(component)
        program = _object(program, 'service program')
        if set(program) != {'entrypoint', 'arguments', 'working_directory'}:
            raise ValueError('Service program requires entrypoint, arguments and working_directory')
        for key in ('entrypoint', 'working_directory'):
            reference = program[key]
            if instance.reference(instance.resolve(reference)) != reference:
                raise ValueError('Program locations inside areas must use area references')
        if not isinstance(program['arguments'], list) or any(
                not isinstance(arg, str) or '\0' in arg for arg in program['arguments']):
            raise ValueError('Service program arguments must be a list of strings')
    for name in FIXED_DIRECTORIES:
        absolute_path(instance.directory(name))
    area_paths = [instance.area(area) for area in AREAS]
    for index, area in enumerate(area_paths):
        absolute_path(area)
        if any(overlaps(area, other) for other in area_paths[:index]):
            raise ValueError("Instance areas must not overlap")
        if area == root or root.is_relative_to(area):
            raise ValueError("Area cannot contain the instance root")
        if any(overlaps(area, reserved) for reserved in [*code_roots, *(root / name for name in FIXED_DIRECTORIES)]):
            raise ValueError("Area overlaps code or a reserved instance directory")
    return instance


def load(root: str | Path | None = None) -> InstanceManifest:
    value = root if root is not None else os.environ.get("PORTAL_INSTANCE_ROOT")
    if not value:
        raise ValueError("Select an instance with PORTAL_INSTANCE_ROOT or --instance")
    path = absolute_path(value)
    source = path / "instance.json"
    if source.is_symlink():
        raise ValueError("Instance manifest must not be a symbolic link")
    return validate(path, json.loads(source.read_text(encoding="utf-8")))


@lru_cache(maxsize=4)
def _current_manifest(root: str, signature: tuple[int, ...]) -> InstanceManifest:
    # The signature identifies the manifest revision, including atomic replace.
    # Only validated configuration is reused, never resource/permission facts.
    return load(root)


def current_instance() -> InstanceManifest:
    """Reuse the selected manifest until its file revision changes.

    Runtime path projections share this cache. Administrative validation still
    uses load(); live file traversal and authorization retain their own checks.
    """
    root = os.environ.get("PORTAL_INSTANCE_ROOT") or ""
    if not root:
        return load()
    source = Path(root) / "instance.json"
    info = source.lstat()
    if not S_ISREG(info.st_mode):
        raise ValueError("Instance manifest must be a regular file, not a symbolic link")
    signature = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size)
    return _current_manifest(root, signature)


def instance_runtime_root() -> Path:
    return current_instance().directory("runtime")


def instance_secrets_root() -> Path:
    return current_instance().directory("secrets")
