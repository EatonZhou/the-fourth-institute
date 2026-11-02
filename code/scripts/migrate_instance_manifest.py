"""Plan and apply an old installation's one-time offline configuration conversion.

Historical file formats are confined to this installer migration boundary; the
runtime manifest reader never accepts them. Planning does not change the source.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from management_common.instance_manifest import SCHEMA, validate
from scripts import migrate_instance_units


def functional_environments(root, components):
    if not components:
        return {}
    from scripts import tfi_release_platform_linux as adapter
    directory = adapter.unit_directory()
    prefix = _read(root, 'config/target.json')['service_prefix']
    environments = {}
    for component in components:
        path = directory / (prefix + '-' + component + '.service')
        if not path.is_file() or path.is_symlink():
            raise ValueError('Functional program requires its historical service definition')
        environments[component] = migrate_instance_units._environment(path.read_text())
    return environments


def conversion_site(root: Path, *, gateway_network: Path | None = None, programs: dict | None = None,
                    components=()) -> dict:
    """Keep feature settings; path owners are migrated separately before cleanup.

    The original site file remains available until the post-start acceptance.
    Return no credential values in diagnostics or the apply result.
    """
    site = _read(root, "config/site.json")
    deployment = site.get("deployment", {})
    domain = deployment.get('compatibility', {}).get('domain_values', {})
    if domain.keys() - {'test_framework.actor'}:
        raise ValueError('Old domain overrides require explicit owner migration')
    if 'test_framework.actor' in domain:
        deployment.setdefault('test_framework', {})['actor'] = domain['test_framework.actor']
    for key in ("installation_id", "source_root", "release_root", "postgres_env_file",
                "runtime_group", "network_config", "provider_egress_env_file",
                "device_ssh_paths", "compatibility", "postgres"):
        deployment.pop(key, None)
    fields = {
        "auth": ("session_signing_key_path", "two_factor_key_path"),
        "backup": ("script_path",),
        "workspace": ("additional_preview_roots",),
        "development_projection": ("code_directory",),
        "providers": ("runtime_root", "state_root", "claude_source_root", "egress_env_file",
                      "codex_binary", "claude_runtime_current", "claude_app_server_current",
                      "kimi_runtime_current", "dsh_runtime_current", "opencode_runtime_current"),
        "native_shell": ("database_env_file",),
        "native_ui": ("test_results_root",),
        "system_workflow": ("harness_release_tree", "harness_database_env_file", "harness_state_dir"),
        "router": ("bind", "port"),
    }
    for section, names in fields.items():
        settings = deployment.get(section, {})
        if not isinstance(settings, dict):
            raise ValueError(f"Invalid feature settings section: {section}")
        for name in names:
            settings.pop(name, None)
        if not settings:
            deployment.pop(section, None)
    if gateway_network is not None:
        network = _old_listener(gateway_network)
        deployment.setdefault('router', {})['allowed_networks'] = (
            ['127.0.0.0/8', '::1/128'] if network['mode'] == 'local'
            else network['allowed_networks'])
    functional = set(programs or {}) | (set(components) & {'kimi', 'kimi-code', 'ai-socket-relay'})
    if functional:
        fields = {
            'TELEPUS_DATABASE_SCHEMA': 'database_schema',
            'TELEPUS_BREAKGLASS_LEASE_SECONDS': 'breakglass_lease_seconds',
            'TELEPUS_BREAKGLASS_INITIAL_CODEX_SESSION_ID': 'breakglass_initial_codex_session_id',
            'TELEPUS_TELEGRAM_UPLOAD_INTERFACE': 'telegram_upload_interface',
        }
        collected = {}
        for component, environment in functional_environments(root, functional).items():
            if component.startswith('telepus-'):
                for old, new in fields.items():
                    if old in environment:
                        if new in collected and collected[new] != environment[old]:
                            raise ValueError('Historical functional service settings disagree')
                        collected[new] = environment[old]
                token = environment.get('TELEPUS_TELEGRAM_BOT_TOKEN_FILE')
                old_secrets = _read(root, 'config/target.json')['data_roots'].get('secrets_dir')
                if token:
                    import hmac
                    current = Path(token)
                    registered = Path(old_secrets or '') / 'telepus_telegram_bot.key'
                    if (not old_secrets or not current.is_file() or not registered.is_file()
                            or not hmac.compare_digest(current.read_bytes(), registered.read_bytes())):
                        raise ValueError('Telegram bot secret requires explicit owner migration')
            if component in {'kimi', 'kimi-code', 'ai-socket-relay'}:
                section, prefix, keys = (
                    ('kimi', 'PORTAL_KIMI_CODE_', ('DEFAULT_MODEL', 'PROVIDER_PROFILE', 'SWARM_MAX_CONCURRENCY', 'SUBAGENT_TIMEOUT_MS', 'POLL_SECONDS'))
                    if component in {'kimi', 'kimi-code'} else
                    ('ai_relay', 'PORTAL_AI_RELAY_', ('ALLOWED_IPS', 'MAX_BYTES', 'MAX_CONCURRENT')))
                for key in keys:
                    if prefix + key in environment:
                        deployment.setdefault(section, {})[key.lower()] = environment[prefix + key]
        if collected:
            deployment.setdefault('telepus', {}).update(collected)
    return site


def _old_listener(path: Path) -> dict:
    values = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        key, separator, raw = line.partition('=')
        if separator and key.strip() in {'PORTAL_PORT', 'PORTAL_ACCESS_MODE', 'PORTAL_ALLOWED_NETWORKS'}:
            parts = shlex.split(raw, comments=True)
            if len(parts) > 1:
                raise ValueError('Invalid historical listener setting')
            values[key.strip()] = parts[0] if parts else ''
    mode = values.get('PORTAL_ACCESS_MODE')
    if mode not in {'local', 'remote'}:
        raise ValueError('Historical gateway must explicitly select local or remote access')
    return {'mode': mode, 'port': int(values['PORTAL_PORT']),
            'allowed_networks': values.get('PORTAL_ALLOWED_NETWORKS', '0.0.0.0/0,::/0').split(',')}


def require_backup(result: dict) -> None:
    """A skipped/locked operation is not a new completed content backup."""
    if (result.get("status") != "success" or result.get("operation") != "content"
            or result.get("succeeded") is not True or not result.get("run_id")):
        raise ValueError("A successful new content backup is required")
    try:
        finished = datetime.fromisoformat(result["finished_at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - finished).total_seconds()
    except (KeyError, TypeError, ValueError):
        raise ValueError("Backup completion time is invalid") from None
    if not 0 <= age <= 86400:
        raise ValueError("Content backup must have completed within the last day")


def require_stopped(root: Path, manifest: dict) -> None:
    """Observe actual user units immediately before applying; never stop them here."""
    if sys.platform == 'win32':
        from scripts import migrate_instance_windows_services as windows
        return windows.require_stopped(root, _read(root, 'config/target.json'))
    if sys.platform != "linux":
        raise NotImplementedError("Offline conversion service checks are not implemented for this platform")
    prefix = manifest["services"]["prefix"]
    units = {f"{prefix}-{component}.service" for component in manifest["services"]["components"]}
    target = _read(root, "config/target.json")
    gateway = target.get("development", {}).get("gateway_unit")
    if gateway:
        if not isinstance(gateway, str) or not gateway.endswith(".service") or "/" in gateway or any(c.isspace() for c in gateway):
            raise ValueError("Invalid historical gateway service")
        units.add(gateway)
    observed = subprocess.run(
        ["systemctl", "--user", "list-units", "--all", "--plain", "--no-legend", prefix + "-*.service"],
        check=True, capture_output=True, text=True, timeout=30)
    units.update(line.split()[0] for line in observed.stdout.splitlines() if line.strip())
    for unit in sorted(units):
        state = subprocess.run(
            ["systemctl", "--user", "show", unit, "--property=LoadState,ActiveState,MainPID,ControlPID,ControlGroup"],
            check=False, capture_output=True, text=True, timeout=30)
        values = dict(line.split("=", 1) for line in state.stdout.splitlines() if "=" in line)
        if state.returncode and values.get("LoadState") != "not-found":
            raise ValueError(f"Cannot inspect instance service: {unit}")
        if (values.get("ActiveState") not in {"inactive", "failed"}
                or values.get("MainPID") != "0" or values.get("ControlPID") != "0"):
            raise ValueError(f"Instance service must be stopped before conversion: {unit}")
        group = values.get("ControlGroup")
        if group:
            events = Path("/sys/fs/cgroup") / group.lstrip("/") / "cgroup.events"
            if not events.is_file() or "populated 1" in events.read_text():
                raise ValueError(f"Instance service still has processes or cannot be verified: {unit}")


def _write_new(path: Path, value: dict) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def apply_conversion(root: Path, *, proposal: dict, backup: dict, backup_program: Path | None = None,
                     gateway_network: Path | None = None, provider_package: Path | None = None, check_only: bool = False,
                     code_root: Path | None = None) -> dict:
    """Apply the offline configuration stage; activation and old-file cleanup follow.

    Moves are renames, never copies of credentials. Failure restores moved files
    and removes only files created by this call. Existing destinations fail closed.
    """
    from management_common import host_instances
    from management_common.file_lock import lock_stream

    root = Path(root).resolve()
    require_backup(backup)
    descriptor = os.open(root / ".configuration.lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "r+b") as stream, lock_stream(stream), host_instances._locked_registry() as registry_file:
        fresh = conversion_manifest(root, components=proposal["services"]["components"], backup_program=backup_program,
                                    gateway_network=gateway_network, programs=proposal['services'].get('programs'), code_root=code_root)
        if fresh != proposal:
            raise ValueError("Conversion proposal no longer matches the installation")
        instance = validate(root, fresh)
        registry = host_instances.read_registry()
        host_instances.validate_allocation(instance, registry)
        site = conversion_site(root, gateway_network=gateway_network, programs=proposal['services'].get('programs'),
                               components=proposal['services']['components'])
        target = _read(root, "config/target.json")
        moves = []
        old_secrets = target.get("data_roots", {}).get("secrets_dir")
        secrets = instance.directory("secrets")
        if old_secrets and Path(old_secrets).resolve() != secrets:
            source = Path(old_secrets).resolve()
            if not source.is_dir():
                raise ValueError("Old secrets directory is missing")
            moves.append((source, secrets))
        for name in ("postgres.env", "source-database.env"):
            source = root / "config" / name
            if source.exists():
                moves.append((source, secrets / name))
        old_site = _read(root, 'config/site.json').get('deployment', {})
        from scripts.migrate_provider_programs import plan as plan_programs
        programs = plan_programs(instance, old_site, provider_package)
        auth = old_site.get('auth', {})
        for key, name in (('session_signing_key_path', 'session_signing.key'),
                          ('two_factor_key_path', 'two_factor.key')):
            if auth.get(key):
                source = Path(auth[key])
                if not source.is_absolute() or not source.is_file():
                    raise ValueError('Configured authentication key is unavailable')
                if source != secrets / name:
                    moves.append((source, secrets / name))
        database = root / 'data/database'
        if database.exists():
            moves.append((database, instance.directory('database')))
        network = root / 'config/network.env'
        if network.is_file():
            moves.append((network, secrets / 'network.env'))
        host = root / "config/host"
        configured_egress = old_site.get('providers', {}).get('egress_env_file') or old_site.get('provider_egress_env_file')
        egress = Path(configured_egress) if configured_egress else None
        if egress is not None and (not egress.is_absolute() or not egress.is_file()):
            raise ValueError('Configured Provider egress file is unavailable')
        if host.exists():
            moves.extend((source, secrets / "host" / ('provider-egress.env' if source == egress else source.name))
                         for source in sorted(host.glob("*.env")))
        if egress is not None and egress.parent != host:
            moves.append((egress, secrets / 'host/provider-egress.env'))
        generation = root / "config/generation.env"
        if generation.exists():
            moves.append((generation, instance.deployment_runtime("release") / "generation.env"))
        for source, destination in moves:
            if source.is_symlink() or destination.exists() or destination.is_symlink():
                raise ValueError("Conversion move has a symbolic link or occupied destination")
            # A whole secrets directory will become the destination's parent.
            if old_secrets and destination.is_relative_to(secrets) and destination != secrets:
                old_destination = Path(old_secrets).resolve() / destination.relative_to(secrets)
                if old_destination.exists() or old_destination.is_symlink():
                    raise ValueError("Secret file would overwrite an existing secret")
        for name in ("instance.json", "site.json"):
            if (root / name).exists() or (root / name).is_symlink():
                raise ValueError("Conversion cannot overwrite existing instance configuration")
        old_gateway = target.get('development', {}).get('gateway_unit')
        obsolete = [old_gateway, instance.service_prefix + '-router.service'] if old_gateway else []
        units = migrate_instance_units.plan(instance, obsolete_units=obsolete,
                    renamed_units={old_gateway: instance.service_name('gateway') + '.service'} if old_gateway else {})
        if check_only:
            return {'ready': True, 'instance': instance.name, 'configuration_moves': len(moves),
                    'unit_replacements': len(units), 'provider_programs': programs is not None,
                    'services_activated': False}
        require_stopped(root, fresh)
        moved, written, units_applied = [], [], False
        registered = {**registry, instance.name: str(root)}
        try:
            for source, destination in moves:
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                source.rename(destination)
                moved.append((source, destination))
            if programs is not None:
                programs.apply()
            for name, document in (("site.json", site), ("instance.json", fresh)):
                _write_new(root / name, document)
                written.append(root / name)
            migrate_instance_units.apply(units)
            units_applied = True
            host_instances._write(registry_file, registered)
        except BaseException:
            # A directory-fsync error can occur after the registry replacement.
            # Restore that pointer before withdrawing the new instance manifest.
            if host_instances.read_registry() != registry:
                host_instances._write(registry_file, registry)
            if units_applied:
                migrate_instance_units.rollback(units)
            for path in reversed(written):
                path.unlink()
            if programs is not None:
                programs.rollback()
            for source, destination in reversed(moved):
                destination.rename(source)
            raise
    return {"converted": True, "instance": instance.name, "services_activated": False,
            "legacy_configuration_retained": True, "moved_files_or_directories": len(moved),
            "unit_replacements": len(units)}


def _read(root: Path, name: str) -> dict:
    result = json.loads((root / name).read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError(f"Migration source must be an object: {name}")
    return result


def conversion_manifest(root: Path, *, components: list[str], backup_program: Path | None = None,
                        gateway_network: Path | None = None, programs: dict | None = None,
                        code_root: Path | None = None) -> dict:
    """Build and validate identity and locations without reading any secret file."""
    root = Path(root).resolve()
    if (root / "instance.json").exists():
        raise ValueError("Instance already has a manifest; conversion is a one-time operation")
    state = _read(root, "installation.json")
    target = _read(root, "config/target.json")
    if state.get("schema") == "tfi.release-installation.v1":
        if state.get("target") != target:
            raise ValueError("Historical target and installation state disagree")
    elif state.get("schema") == "tfi.release-installation.v2":
        digest = hashlib.sha256(json.dumps(target, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        if digest != state.get("configuration_sha256"):
            raise ValueError("Old installation has unapplied configuration")
    else:
        raise ValueError("Unsupported installation format for conversion")
    if Path(target["install_root"]).resolve() != root:
        raise ValueError("Move the old instance with its installer before converting its location")
    site = _read(root, "config/site.json")
    settings = site.get("deployment", {})
    identity = settings.get("installation_id")
    if not identity:
        raise ValueError("Existing instance identity is required; conversion must not invent a replacement")
    pair = target.get("development", {})
    network_bind, entry_port = target['bind'], target['ports']['web']
    if pair.get('gateway_unit'):
        if gateway_network is None:
            raise ValueError('Specify the actual gateway network file; the internal router is not the public entry')
        listener = _old_listener(gateway_network)
        network_bind = '127.0.0.1' if listener['mode'] == 'local' else settings.get('router', {}).get('bind', target['bind'])
        entry_port = listener['port']
        components = [component for component in components if component != 'router']
        if 'gateway' not in components:
            components = [*components, 'gateway']
    release_code = pair.get("release_code", target["shared_code_root"])
    if code_root is not None:
        if pair:
            raise ValueError('A paired development instance requires its existing code deployments')
        if not code_root.is_absolute() or code_root.resolve() != code_root or not (code_root / 'scripts/tfi_release_install.py').is_file():
            raise ValueError('Conversion code root must be an installed canonical program directory')
        release_code = str(code_root)
    deployments = {"release": {"kind": "release", "code_root": str(Path(release_code).resolve()),
                                "web_port": pair.get("web_ports", {}).get("release", target["ports"]["web"])}}
    if pair:
        deployments["dev"] = {"kind": "dev", "code_root": str(Path(pair["dev_code"]).resolve()), "web_port": pair["web_ports"]["dev"]}
    timestamps = [entry["time"] for entry in state.get("stages", []) if isinstance(entry.get("time"), (int, float))]
    if not timestamps:
        raise ValueError("Installation creation time is missing")
    old = target["data_roots"]
    shared = Path(old["share_root"]).resolve()
    dependencies = {"postgres": {name: str(Path(target["pg_" + name]).resolve()) for name in ("bin", "lib", "share")}}
    if settings.get("backup") and backup_program is None:
        raise ValueError("Specify the backup program; the old Lineage wrapper is not the backup dependency")
    if backup_program is not None:
        if not backup_program.is_absolute() or not backup_program.is_file():
            raise ValueError("Backup program must name an existing absolute file")
        dependencies["backup"] = str(backup_program.resolve())
    document = {
        "schema": SCHEMA, "instance_id": identity, "name": root.name,
        "created_at": datetime.fromtimestamp(min(timestamps), timezone.utc).isoformat().replace("+00:00", "Z"),
        "seed_version": state["manifest_sha256"],
        "deployments": deployments,
        "network": {"bind": network_bind, "entry_port": entry_port,
                    "postgres_port": target["ports"]["postgres"], "nats_port": target["ports"]["nats"]},
        "services": {"prefix": target["service_prefix"], "components": sorted(set(components))},
        "dependencies": dependencies,
        "areas": {name: str(shared / directory) for name, directory in (
            ("projects", "Projects"), ("skills", "Skills"), ("papers", "Papers"), ("lineages", "Agents"))},
    }
    if settings.get('providers', {}).get('state_root'):
        document['areas']['provider-state'] = str(Path(settings['providers']['state_root']).resolve())
    if programs:
        document['services']['programs'] = programs
    listener_prefixes = {'kimi': 'PORTAL_KIMI_CODE_', 'kimi-code': 'PORTAL_KIMI_CODE_',
                         'ai-socket-relay': 'PORTAL_AI_RELAY_', 'knowledge-archive-agent': 'KNOWLEDGE_ARCHIVE_AGENT_'}
    selected = set(components) & listener_prefixes.keys()
    if selected:
        listeners = {}
        for component, environment in functional_environments(root, selected).items():
            prefix = listener_prefixes[component]
            if not environment.get(prefix + 'HOST') or not environment.get(prefix + 'PORT'):
                raise ValueError('Confirm the historical functional service bind and port before conversion')
            listeners[component] = {'bind': environment[prefix + 'HOST'], 'port': int(environment[prefix + 'PORT'])}
        document['network']['listeners'] = listeners
    return validate(root, document).document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--component", action="append",
                        help="Existing instance service component, without its prefix or .service suffix")
    parser.add_argument("--programs", type=Path, help="Reviewed functional component program definitions")
    parser.add_argument("--output", type=Path, help="Reviewable manifest proposal; never the live instance.json")
    parser.add_argument("--apply", type=Path, metavar="PROPOSAL", help="Apply a reviewed proposal while all instance services are stopped")
    parser.add_argument("--backup-result", type=Path, help="Successful fresh content-backup completion record")
    parser.add_argument("--backup-program", type=Path, help="Installed backup application's entry point")
    parser.add_argument("--gateway-network", type=Path, help="Actual external gateway network env file for paired installations")
    parser.add_argument("--provider-package", type=Path, help="New packaged Claude code prepared beside dependencies/providers")
    parser.add_argument("--code-root", type=Path, help="Separately installed program used after conversion; old program is retained")
    parser.add_argument("--check", action="store_true", help="With --apply, verify conversion readiness without stopping or mutating data")
    args = parser.parse_args()
    if args.apply:
        if args.output or args.component or args.programs or not args.backup_result:
            parser.error("--apply requires --backup-result and cannot be combined with planning arguments")
        result = apply_conversion(args.root, proposal=json.loads(args.apply.read_text()),
                                  backup=json.loads(args.backup_result.read_text()), backup_program=args.backup_program,
                                  gateway_network=args.gateway_network, provider_package=args.provider_package, check_only=args.check,
                                  code_root=args.code_root)
        print(json.dumps(result))
        return
    if not args.output or not args.component or args.backup_result or args.check:
        parser.error("Planning requires --output and --component")
    if args.output.resolve() == args.root.resolve() / "instance.json":
        parser.error("Planning cannot write the live instance manifest")
    manifest = conversion_manifest(args.root, components=args.component, backup_program=args.backup_program,
                                   gateway_network=args.gateway_network, programs=json.loads(args.programs.read_text()) if args.programs else None,
                                   code_root=args.code_root)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"planned": True, "instance": manifest["name"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
