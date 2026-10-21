from __future__ import annotations

import argparse
import ctypes
import json
import ipaddress
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from management_common import instance_paths
from management_common import portal_network
from management_common.instance_paths import instance_root
from management_common.portal_paths import path as portal_path
from management_common.portal_paths import resolve_directory
from management_common.site_config import read_site_config
from management_common.postgres_configuration import postgres_env_file, postgres_settings


def site_environment(deployment: dict | None = None) -> dict[str, str]:
    """Shared non-secret site paths used by units and one-off host operations."""
    deployment = read_site_config().get("deployment", {}) if deployment is None else deployment
    configured = (json.loads(instance_paths.CONFIG_PATH.read_text(encoding="utf-8")).get("environment", {})
                  if instance_paths.CONFIG_PATH.exists() else {})
    result = {
        "PORTAL_INSTANCES_ROOT": str(instance_root()),
        "PORTAL_ACCOUNTS_ROOT": str(portal_path("accounts_root")),
        "PORTAL_SECRETS_DIR": str(portal_path("secrets_dir")),
        "PORTAL_NETWORK_CONFIG": str(portal_network.config_path()),
        "PORTAL_SITE_CONFIG": str(resolve_directory(os.environ.get("PORTAL_SITE_CONFIG", str(ROOT / "config/site.json")))),
    }
    if os.environ.get("PORTAL_PATHS_CONFIG"):
        result["PORTAL_PATHS_CONFIG"] = str(resolve_directory(os.environ["PORTAL_PATHS_CONFIG"]))
    egress = os.environ.get("PORTAL_PROVIDER_EGRESS_ENV_FILE", deployment.get("providers", {}).get(
        "egress_env_file", configured.get("PORTAL_PROVIDER_EGRESS_ENV_FILE", "")))
    if egress:
        result["PORTAL_PROVIDER_EGRESS_ENV_FILE"] = str(resolve_directory(egress))
    return result


def _deployment_values(deployment: dict) -> dict[str, str]:
    providers = deployment.get("providers", {})
    router = deployment.get("router", {})
    relay = deployment.get("relay", {})
    auth = deployment.get("auth", {})
    if not all(isinstance(section, dict) for section in (providers, router, relay, auth)):
        raise ValueError("Deployment sections must be objects")
    source = resolve_directory(deployment.get("source_root", str(ROOT)))
    runtimes = resolve_directory(providers.get("runtime_root", str(portal_path("staging_root") / "provider-runtimes")))
    group = deployment.get("runtime_group", "")
    if not isinstance(group, str) or (group and not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_.-]*", group)):
        raise ValueError("Invalid deployment runtime_group")

    def addresses(config, key, default, parser):
        values = config.get(key, default)
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise ValueError(f"{key} must be a list of strings")
        return [str(parser(value)) for value in values]

    def port(config, default):
        value = config.get("port", default)
        if type(value) is not int or not 1 <= value <= 65535:
            raise ValueError("Invalid deployment port")
        return str(value)

    users = router.get("dev_users", [])
    if not isinstance(users, list) or not all(isinstance(user, str) and re.fullmatch(r"[a-zA-Z0-9_.@-]+", user) for user in users):
        raise ValueError("Invalid router dev_users")
    networks = addresses(router, "allowed_networks", ["127.0.0.0/8", "::1/128"], ipaddress.ip_network)
    postgres = postgres_settings()
    return {
        "PORTAL_SOURCE_ROOT": str(source),
        "PORTAL_RELEASE_ROOT": str(resolve_directory(deployment.get("release_root", str(source.parent / "tfi-release")))),
        "PORTAL_POSTGRES_ENV_FILE": str(postgres_env_file()),
        "PORTAL_POSTGRES_UNIT": postgres['unit'],
        "PORTAL_POSTGRES_BIN": postgres['bin'],
        "PORTAL_POSTGRES_LIB": postgres['lib'],
        "PORTAL_POSTGRES_DATA": postgres['data'],
        "PORTAL_POSTGRES_SOCKET": postgres['socket'],
        "PORTAL_POSTGRES_PORT": str(postgres['port']),
        "PORTAL_STAGING_ROOT": str(portal_path("staging_root")),
        "PORTAL_PROVIDER_RUNTIME_ROOT": str(runtimes),
        "PORTAL_CLAUDE_SOURCE_ROOT": str(resolve_directory(providers.get("claude_source_root", str(source)))),
        "PORTAL_RENDERED_CONFIG_ROOT": str(instance_root() / "systemd"),
        "PORTAL_SESSION_SIGNING_KEY_PATH": str(resolve_directory(auth.get("session_signing_key_path", str(portal_path("secrets_dir") / "session_signing.key")))),
        "PORTAL_ROUTER_BIND": str(ipaddress.ip_address(router.get("bind", "127.0.0.1"))),
        "PORTAL_ROUTER_PORT": port(router, 18084),
        "PORTAL_ROUTER_NETWORK_ARGS": " ".join(f"--allow-network {network}" for network in networks),
        "PORTAL_ROUTER_DEV_USERS": ",".join(users),
        "PORTAL_RELAY_BIND": str(ipaddress.ip_address(relay.get("bind", "127.0.0.1"))),
        "PORTAL_RELAY_PORT": port(relay, 18484),
        "PORTAL_RELAY_ALLOWED_IPS": ",".join(addresses(relay, "allowed_ips", ["127.0.0.1", "::1"], ipaddress.ip_address)),
        "PORTAL_RUNTIME_GROUP": group,
    }


def render_unit(source: Path) -> str:
    deployment = read_site_config().get("deployment", {})
    if not isinstance(deployment, dict):
        raise ValueError("deployment must be an object")
    values = _deployment_values(deployment)
    environment = site_environment(deployment)
    for name, value in (values | environment).items():
        if name == "PORTAL_ROUTER_NETWORK_ARGS":
            continue  # Constructed exclusively from validated CIDRs above.
        if any(character.isspace() for character in value) or any(character in value for character in "'\"\\%$"):
            raise ValueError("Systemd site path contains unsupported shell characters")
    content = source.read_text(encoding="utf-8")
    egress = environment.get("PORTAL_PROVIDER_EGRESS_ENV_FILE", "")
    content = content.replace("@PORTAL_PROVIDER_EGRESS_DIRECTIVE@", f"EnvironmentFile={egress}" if egress else "")
    if "@PORTAL_CODEX_BINARY@" in content:
        from management_portal.execution.provider_updates import CodexRuntime
        # Rendering can follow a site-config change in the same process.
        # Prefer that site's explicit path over the runtime module's cached
        # deployment, keeping the executable link itself intact.
        configured = deployment.get("providers", {}).get("codex_binary")
        if configured is not None and not isinstance(configured, str):
            raise ValueError("Codex executable must be a path")
        path = Path(configured).expanduser() if configured else None
        binary = str(resolve_directory(str(path.parent)) / path.name) if path else str(CodexRuntime().link())
        if not binary or any(character.isspace() or character in "'\"\\%$" for character in binary):
            raise ValueError("Codex executable is missing or its path cannot be used by systemd")
        content = content.replace("@PORTAL_CODEX_BINARY@", binary)
    for name, value in (values | environment).items():
        content = content.replace(f"@{name}@", value)
    command_prefix = "ExecStart=@PORTAL_GROUP_COMMAND@"
    lines = []
    for line in content.splitlines(keepends=True):
        if line.startswith(command_prefix):
            command = line[len(command_prefix):].rstrip("\n")
            group = values["PORTAL_RUNTIME_GROUP"]
            if group:
                if "'" in command or "\n" in command:
                    raise ValueError("Unsupported grouped service command")
                command = f"/usr/bin/sg {group} -c 'exec {command}'"
            line = "ExecStart=" + command + "\n"
        lines.append(line)
    content = "".join(lines)
    if re.search(r"@PORTAL_[A-Z_]+@", content):
        raise ValueError("Unresolved systemd template placeholder")
    if "[Service]" in content:
        variables = "\n".join(f"Environment={name}={value}" for name, value in environment.items())
        content = content.replace("[Service]", f"[Service]\n{variables}", 1)
        # Later EnvironmentFiles override earlier Provider defaults. The optional
        # file contains no proxy keys until an administrator explicitly saves them.
        start = content.index("[Service]") + len("[Service]")
        end = content.find("\n[", start)
        end = len(content) if end < 0 else end
        directive = f"\nEnvironmentFile=-{environment['PORTAL_NETWORK_CONFIG']}\n"
        content = content[:end].rstrip("\n") + directive + content[end:]
    return content


def install_unit(source: Path, destination: Path) -> None:
    content = render_unit(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o644)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def exchange_paths(source: Path, destination: Path) -> None:
    rename = ctypes.CDLL(None, use_errno=True).renameat2
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 2):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def migrate(source: Path, destination: Path, *, apply: bool = False) -> dict:
    source = source.absolute()
    destination = destination.absolute()
    if source.is_symlink():
        if source.resolve(strict=True) != destination.resolve(strict=True) or not destination.is_dir():
            raise ValueError("Existing source link does not point to the requested directory")
        return {"source": str(source), "destination": str(destination), "status": "already_migrated"}
    if source == destination or source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError("Instance directories must be distinct and non-nested")
    if not source.is_dir() or destination.exists() or destination.is_symlink():
        raise ValueError("Migration requires a source directory and an absent destination")
    if source.stat().st_dev != destination.parent.stat().st_dev:
        raise ValueError("Live instance migration requires the same filesystem")
    identity = source.stat().st_ino
    result = {"source": str(source), "destination": str(destination), "inode": identity, "status": "planned"}
    if not apply:
        return result
    if sys.platform != "linux":
        raise NotImplementedError("Live instance directory exchange requires Linux renameat2; use a stopped-instance migration on this platform")
    destination.symlink_to(destination, target_is_directory=True)
    try:
        exchange_paths(source, destination)
    except BaseException:
        if destination.is_symlink() and os.readlink(destination) == str(destination):
            destination.unlink()
        raise
    if destination.stat().st_ino != identity or source.resolve(strict=True) != destination:
        raise RuntimeError("Instance directory migration verification failed")
    result["status"] = "migrated"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    unit = commands.add_parser("install-unit")
    unit.add_argument("source", type=Path)
    unit.add_argument("destination", type=Path)
    migration = commands.add_parser("migrate")
    migration.add_argument("source", type=Path)
    migration.add_argument("--destination", type=Path)
    migration.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    if arguments.command == "install-unit":
        install_unit(arguments.source, arguments.destination)
    else:
        print(json.dumps(migrate(arguments.source, arguments.destination or instance_root(), apply=arguments.apply)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
