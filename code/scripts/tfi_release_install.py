"""Create, run, update and retire Portal instances: the instance lifecycle.

An instance is created from the example instance's release content, an
engineering bundle that prepare_install.py assembles from this code directory.
The installer writes instance.json and site.json, prepares secrets/, the
database and the areas, installs service definitions and records the instance
in the host registry (~/.config/tfi/instances.json). Instance roots live outside
code directories, by default in the OS user's data directory.

A code update replaces code and restarts services; services update the database
themselves. There is no separate upgrade operation. Retiring stops services,
deletes their definitions, unregisters the instance and deletes its root; host
resources (account directories, models, Workspaces outside the root) remain.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from urllib.parse import quote
from urllib.request import build_opener, ProxyHandler

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
sys.dont_write_bytecode = True

from management_common import host_instances  # noqa: E402
from management_common.code_environment import runtime_python, source_inventory  # noqa: E402
from management_common.configuration_files import atomic_json  # noqa: E402
from management_common.installation_progress import report  # noqa: E402
from management_common.instance_manifest import AREAS, FIXED_DIRECTORIES, SCHEMA, load, validate  # noqa: E402
from management_common.native_output import run_native_capture, run_utf8_capture  # noqa: E402
from scripts import instance_services  # noqa: E402

# Probes address this host's loopback only; system proxies must never route them.
_LOCAL_HTTP = build_opener(ProxyHandler({}))
CORE_COMPONENTS = ("postgres", "nats", "resource", "execution", "society", "worker", "web")
DATABASE_ROLE = "tfi"
DATABASE_NAME = "tfi_lineage"
PORT_BASES = {"web": 28200, "postgres": 56500, "nats": 14300}
FIRST_ADMIN_FILE = "first-admin-link.txt"


class PlatformPreflightError(ValueError):
    def __init__(self, result):
        self.report = result
        super().__init__("; ".join(result["reasons"]))


def platform_preflight(manifest):
    # The target catalog is the sole capability authority.
    from management_common.distribution_targets import preflight_manifest
    result = preflight_manifest(manifest)
    if not result["ok"]:
        raise PlatformPreflightError(result)
    return result


def windows():
    return os.name == "nt"


def native_tool(name):
    return name + (".exe" if windows() else "")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


# ---------------------------------------------------------------- engineering

def engineering_path(bundle, value):
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("Engineering paths must be relative and stay inside the bundle")
    result = bundle / relative
    if not result.resolve().is_relative_to(bundle) or any(
            p.is_symlink() for p in (result, *result.parents) if p.is_relative_to(bundle)):
        raise ValueError("Engineering contains a symlink or path outside its bundle")
    return result


def validate_engineering(manifest_path, *, skip_file_verification=False):
    """Check the bundle's layout, and unless skipped its complete inventory."""
    manifest_path = Path(manifest_path).resolve(strict=True)
    bundle = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "tfi.release-engineering.v1" or (bundle / "GENERATION_FAILED").exists():
        raise ValueError("Unsupported or incomplete engineering bundle")
    if manifest.get("unresolved"):
        raise ValueError("Engineering has unresolved selection items")
    if manifest["database"]["format"] != "postgresql-sql":
        raise ValueError("This installer requires the generator's postgresql-sql database")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) - set(AREAS):
        raise ValueError("Engineering files must be keyed by instance area")
    for area, value in files.items():
        if value != "data/" + area:
            raise ValueError("Engineering area content must be at data/<area>")
        if not engineering_path(bundle, value).is_dir():
            raise ValueError("Engineering area directory is missing: " + area)
    code = engineering_path(bundle, manifest["code"]["path"])
    database = engineering_path(bundle, manifest["database"]["path"])
    if not code.is_dir() or not database.is_file():
        raise ValueError("Engineering code or database is missing")
    declaration = manifest.get("installer")
    entrypoint = declaration.get("entrypoint") if isinstance(declaration, dict) else None
    if not isinstance(entrypoint, str) or not engineering_path(bundle, entrypoint).is_relative_to(code) \
            or not engineering_path(bundle, entrypoint).is_file():
        raise ValueError("Engineering manifest requires installer.entrypoint inside its code")
    for entry in manifest.get("directory_mappings", []):
        if entry != {"placeholder": "${code_root}", "root": "code_root", "path": "."}:
            raise ValueError("Unknown engineering directory placeholder")
    if skip_file_verification:
        return manifest, bundle
    inventory = manifest["inventory"]
    expected = {item["path"] for item in inventory}
    if len(expected) != len(inventory):
        raise ValueError("Duplicate engineering inventory entries")
    actual = set()
    for directory, dirs, names in os.walk(bundle, followlinks=False):
        for name in [*dirs, *names]:
            path = Path(directory) / name
            if path.is_symlink() or path.name == ".git":
                raise ValueError("Engineering may not contain symlinks or Git history")
            if path.is_file() and path != manifest_path:
                actual.add(path.relative_to(bundle).as_posix())
    if expected != actual:
        raise ValueError("Engineering inventory does not exactly match its files")
    for item in inventory:
        if digest(engineering_path(bundle, item["path"])) != item["sha256"]:
            raise ValueError(f"Engineering checksum mismatch: {item['path']}")
    if digest(database) != manifest["database"]["sha256"]:
        raise ValueError("Database checksum mismatch")
    prefix = code.relative_to(bundle).as_posix() + "/"
    code_files = [{**item, "path": item["path"][len(prefix):]} for item in inventory if item["path"].startswith(prefix)]
    canonical = json.dumps(code_files, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if hashlib.sha256(canonical.encode()).hexdigest() != manifest["code"]["sha256"]:
        raise ValueError("Code tree checksum mismatch")
    return manifest, bundle


def directory_replacements(manifest, code):
    if not manifest.get("directory_mappings"):
        return {}
    return {"${code_root}": code.as_posix() if windows() else str(code)}


def map_directories(text, replacements):
    # The generator owns where placeholders occur; code paths contain no quoting.
    for token, path in replacements.items():
        text = text.replace(token, path)
    return text


def reject_unsafe_entries(directory, names):
    for name in names:
        mode = (Path(directory) / name).lstat().st_mode
        if name == ".git" or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError(f"Engineering contains an unsafe entry: {Path(directory) / name}")
    return []


def copy_materialized_file(src, dst, replacements=None):
    """Publish complete bytes before exposing the source's (possibly read-only) mode."""
    destination = Path(dst)
    descriptor, name = tempfile.mkstemp(prefix=".tfi-install-", dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            content = Path(src).read_bytes()
            if replacements:
                try:
                    content = map_directories(content.decode("utf-8"), replacements).encode("utf-8")
                except UnicodeDecodeError:
                    pass
            stream.write(content)
        shutil.copystat(src, temporary)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.chmod(temporary.stat().st_mode | stat.S_IWUSR)
            temporary.unlink()
    return dst


def copy_area_content(manifest, bundle, instance, replacements):
    """The example instance's data/<area>/ becomes the new instance's area."""
    from scripts.tfi_install_progress import FileProgress
    sizes = {row["path"]: row["size"] for row in manifest["inventory"]
             if any(row["path"].startswith(value + "/") for value in manifest["files"].values())}
    meter = FileProgress("正在复制实例文件", len(sizes), sum(sizes.values()))

    def copy(src, dst):
        result = copy_materialized_file(src, dst, replacements)
        meter.advance(sizes.get(Path(src).relative_to(bundle).as_posix(), 0))
        return result
    for area, value in manifest["files"].items():
        shutil.copytree(engineering_path(bundle, value), instance.area(area), dirs_exist_ok=True,
                        ignore=reject_unsafe_entries, copy_function=copy)


def binary_architectures(path):
    """Read the native executable header; never execute an unclaimed target ISA."""
    with Path(path).open("rb") as stream:
        data = stream.read(4096)
    machines = {62: "x86_64", 183: "aarch64", 0x8664: "x86_64", 0xAA64: "aarch64",
                0x01000007: "x86_64", 0x0100000C: "aarch64"}
    if data.startswith(b"\x7fELF") and len(data) >= 20:
        endian = "<" if data[5] == 1 else ">" if data[5] == 2 else ""
        return ({machines.get(struct.unpack_from(endian + "H", data, 18)[0], "")} - {""}) if endian else set()
    if data.startswith(b"MZ") and len(data) >= 64:
        offset = struct.unpack_from("<I", data, 0x3C)[0]
        if offset + 6 <= len(data) and data[offset:offset + 4] == b"PE\0\0":
            return {machines.get(struct.unpack_from("<H", data, offset + 4)[0], "")}
    magic = data[:4]
    if magic in {b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"} and len(data) >= 8:
        endian = ">" if magic[0] == 0xCA else "<"
        stride = 32 if magic in {b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"} else 20
        count = struct.unpack_from(endian + "I", data, 4)[0]
        return {machines.get(struct.unpack_from(endian + "I", data, 8 + stride * i)[0], "")
                for i in range(min(count, (len(data) - 8) // stride))} - {""}
    if magic in {b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe"} and len(data) >= 8:
        endian = ">" if magic[0] == 0xFE else "<"
        return {machines.get(struct.unpack_from(endian + "I", data, 4)[0], "")} - {""}
    return set()


# ------------------------------------------------------------ manifest choice

def instance_root(name, root=None):
    """A canonical location outside every code directory, empty or absent."""
    path = Path(root).absolute() if root else host_instances.default_root(name)
    if path.resolve() != path:
        raise ValueError("Instance root must be canonical, without symbolic links")
    if len(path.parts) < 3:
        raise ValueError("Instance root is too broad")
    if path.is_relative_to(SOURCE_ROOT.parent) or SOURCE_ROOT.parent.is_relative_to(path):
        raise ValueError("Instance root cannot be inside or contain the code directory")
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError("Instance root must be a new or empty directory")
    return path


def service_prefix(name):
    prefix = "tfi-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].rstrip("-")
    if not re.fullmatch(r"tfi-[a-z0-9][a-z0-9-]{2,39}", prefix):
        raise ValueError("Use an instance name with at least three letters or digits")
    return prefix


def port_free(port, bind):
    family = socket.AF_INET6 if ":" in bind else socket.AF_INET
    with socket.socket(family) as probe:
        try:
            probe.bind((bind, port))
        except OSError:
            return False
    return True


def allocate_ports(requested, bind):
    """Explicit ports are kept; others are the next free ones on this host."""
    reserved = set()
    for root in host_instances.read_registry().values():
        reserved |= host_instances.ports(load(root))
    result = {}
    for key, first in PORT_BASES.items():
        if requested.get(key) is not None:
            result[key] = requested[key]
            continue
        result[key] = next((port for port in range(first, first + 2000)
                            if port not in reserved and port not in result.values()
                            and port_free(port, "0.0.0.0" if bind != "::" else bind)), None)
        if result[key] is None:
            raise RuntimeError("No free port found for " + key)
    taken = [key for key, port in result.items() if port in reserved]
    if taken:
        raise ValueError("Ports already belong to another registered instance: " + ", ".join(taken))
    return result


def manifest_document(*, name, code, bind, ports, postgres, seed_version):
    return {
        "schema": SCHEMA, "instance_id": uuid.uuid4().hex, "name": name,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seed_version": seed_version,
        "deployments": {"release": {"kind": "release", "code_root": str(code), "web_port": ports["web"]}},
        "network": {"bind": bind, "entry_port": ports["web"], "postgres_port": ports["postgres"],
                    "nats_port": ports["nats"]},
        "services": {"prefix": service_prefix(name), "components": list(CORE_COMPONENTS)},
        "dependencies": {"postgres": postgres},
    }


def site_settings(instance):
    """Feature settings only; sites on one host need distinct session cookies."""
    return {"site_name": "TFI Portal",
            "deployment": {"auth": {"session_cookie_name": "db_session_" + instance.service_prefix}}}


def database_url(instance, role=DATABASE_ROLE, database=DATABASE_NAME):
    port = instance.document["network"]["postgres_port"]
    if windows():
        return f"postgresql://{role}@127.0.0.1:{port}/{database}"
    socket_directory = quote(str(instance.directory("runtime") / "s"), safe="")
    return f"postgresql://{role}@/{database}?host={socket_directory}&port={port}"


def write_secret(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError("Secret files cannot be redirected")
    descriptor, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


# ------------------------------------------------------------------ processes

def postgres_environment(instance):
    from management_portal.service_runtime import clean_environment
    return clean_environment(instance, postgres=True)


def run_checked(command, *, instance, env, cwd=None, timeout=1800):
    try:
        result = run_native_capture(command, env=env, cwd=cwd, timeout=timeout, merge_output=True)
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"{Path(command[0]).name} exceeded {timeout}s; its process tree was stopped") from exc
    if result.returncode:
        # Output stays in the instance for diagnosis, never in stdout or receipts.
        log = instance.directory("runtime") / "command-error.log"
        log.write_text(result.stdout or "", encoding="utf-8")
        log.chmod(0o600)
        raise RuntimeError(f"{Path(command[0]).name} exited {result.returncode}; diagnostic log: {log}")
    return result.stdout


def database_command(instance, tool, *arguments):
    host = "127.0.0.1" if windows() else str(instance.directory("runtime") / "s")
    program = Path(instance.document["dependencies"]["postgres"]["bin"]) / native_tool(tool)
    return [str(program), "-h", host, "-p", str(instance.document["network"]["postgres_port"]),
            "-U", DATABASE_ROLE, *arguments]


def code_child(instance, script, *arguments, timeout=1800):
    """Run an installer helper with the instance's own code environment."""
    from management_portal.service_runtime import clean_environment
    code = instance.code_root()
    command = [str(runtime_python(code)), str(code / "scripts" / script), *arguments]
    result = run_utf8_capture(command, env=clean_environment(instance), cwd=code, timeout=timeout)
    if result.returncode:
        log = instance.directory("runtime") / "command-error.log"
        log.write_text((result.stdout or "") + (result.stderr or ""), encoding="utf-8")
        log.chmod(0o600)
        raise RuntimeError(f"{script} exited {result.returncode}; diagnostic log: {log}")
    return result.stdout


def initialize_database(instance):
    data = instance.directory("database")
    run_checked([str(Path(instance.document["dependencies"]["postgres"]["bin"]) / native_tool("initdb")),
                 "-D", str(data), "-L", instance.document["dependencies"]["postgres"]["share"],
                 "--username=" + DATABASE_ROLE, "--encoding=UTF8",
                 *([] if windows() else ["--locale=" + ("C" if sys.platform == "darwin" else "C.UTF-8")]),
                 "--auth-local=trust", "--auth-host=trust" if windows() else "--auth-host=reject"],
                instance=instance, env=postgres_environment(instance))


def import_database(instance, sql):
    environment = postgres_environment(instance)
    run_checked(database_command(instance, "createdb", DATABASE_NAME), instance=instance, env=environment)
    run_checked(database_command(instance, "psql", "-d", DATABASE_NAME, "-X", "--single-transaction",
                                 "--set", "ON_ERROR_STOP=1", "--file", str(sql)),
                instance=instance, env=environment)


def initialize_features(instance, manifest_path, manifest, freeze_evidence=None):
    """Prove the imported history is frozen, then apply the bundle's feature defaults."""
    if freeze_evidence is not None:
        evidence = json.loads(Path(freeze_evidence).read_text(encoding="utf-8"))
    elif manifest.get("supervisor_resident_id"):
        raise ValueError("A supervised bundle requires --freeze-evidence from its Resident")
    else:
        evidence = {**json.loads(code_child(instance, "tfi_release_initialization.py", "verify-frozen")),
                    "run_id": manifest["run_id"], "supervisor_resident_id": manifest.get("supervisor_resident_id", ""),
                    "manifest_sha256": digest(manifest_path)}
    expected = {"run_id": manifest["run_id"], "supervisor_resident_id": manifest.get("supervisor_resident_id", ""),
                "manifest_sha256": digest(manifest_path)}
    if (evidence.get("passed") is not True or any(evidence.get(k) != v for k, v in expected.items())
            or any(evidence.get("violations", {}).values())):
        raise ValueError("Freeze evidence does not match this bundle or found unfrozen history")
    result = json.loads(code_child(instance, "tfi_release_initialization.py", "initialize", str(manifest_path)))
    if result.get("work_dispatched") is not False:
        raise ValueError("Initialization must not dispatch business work")
    return result


# --------------------------------------------------------------------- health

def observed_status(instance, *, request_timeout=3):
    """Services by the manifest, then Release Web, login page and Worker health."""
    services = instance_services.status(instance)
    url = f"http://127.0.0.1:{instance.deployments['release']['web_port']}"
    checks = []
    for route in ("/api/health", "/login"):
        observation = {"route": route}
        if services["web"]["active"] != "active":
            checks.append({**observation, "ok": False, "error": "Web service is not running"})
            continue
        try:
            from urllib.error import HTTPError
            try:
                response = _LOCAL_HTTP.open(url + route, timeout=request_timeout)
            except HTTPError as exc:
                response = exc
            with response:
                observation["http_status"] = response.status
                body = response.read()
            ok = observation["http_status"] == 200
            if not ok:
                observation["error"] = f"HTTP {observation['http_status']}"
            if route == "/api/health":
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError("Expected a health object")
                ok = (ok and payload.get("ok") is True and payload.get("instance") == instance.name
                      and payload.get("deployment") == "release"
                      and payload.get("execution_layer", {}).get("ok") is True
                      and payload.get("society_service", {}).get("ok") is True)
                checks.append({**observation, "ok": ok, "payload": payload})
            else:
                checks.append({**observation, "ok": ok})
        except Exception as exc:
            checks.append({**observation, "ok": False, "error": str(exc)})
    try:
        worker = json.loads((instance.directory("runtime") / "worker-health.json").read_text(encoding="utf-8"))
        checks.append({"component": "worker", "payload": worker,
                       "ok": worker.get("ok") is True and worker.get("instance") == instance.name
                       and time.time() - float(worker.get("updated_at", 0)) < 60})
    except (OSError, ValueError) as exc:
        checks.append({"component": "worker", "ok": False, "error": str(exc)})
    healthy = all(s["active"] == "active" for s in services.values()) and all(c["ok"] for c in checks)
    return {"status": "running" if healthy else "unhealthy", "name": instance.name, "url": url,
            "listen_address": instance.document["network"]["bind"], "services": services, "checks": checks}


def health_failures(result):
    failures = [component + ": " + state["active"] for component, state in result["services"].items()
                if state["active"] != "active"]
    for check in result["checks"]:
        if check["ok"]:
            continue
        detail = check.get("error", "")
        payload = check.get("payload")
        if isinstance(payload, dict):
            pending = [name for name in ("execution_layer", "society_service")
                       if not isinstance(payload.get(name), dict) or payload[name].get("ok") is not True]
            if pending:
                detail = (detail + "; " if detail else "") + "未就绪: " + ", ".join(pending)
        failures.append((check.get("component") or check.get("route")) + ": " + (detail or "未就绪"))
    return failures


def health_failure(instance, result):
    """Describe the blocking checks with the tails of the affected logs."""
    tails = {}
    for component in instance_services.components(instance):
        if result["services"][component]["active"] == "active" and not any(
                not c["ok"] and (c.get("component") == component or (component == "web" and c.get("route")))
                for c in result["checks"]):
            continue
        for suffix in (".log", ".error.log"):
            log = instance.directory("runtime") / (component + suffix)
            try:
                with log.open("rb") as stream:
                    stream.seek(0, 2)
                    stream.seek(max(0, stream.tell() - 8192))
                    tail = stream.read().decode("utf-8", errors="replace").strip()
            except OSError:
                continue
            if tail:
                tails[component + suffix] = "\n".join(tail.splitlines()[-30:])
    detail = "".join(f"\n\n{name}（末尾）：\n{tail}" for name, tail in tails.items())
    return "服务启动检查失败：" + "; ".join(health_failures(result)) + detail


def wait_for_ready(instance, *, timeout=180, progress=report):
    started = time.monotonic()
    last = None
    while True:
        result = observed_status(instance, request_timeout=3)
        if result["status"] == "running" or time.monotonic() - started >= timeout:
            return result
        pending = "; ".join(health_failures(result))
        if pending != last:
            progress(f"正在检查 Portal 就绪状态（已等待 {int(time.monotonic() - started)} 秒）：{pending}")
            last = pending
        time.sleep(1)


# ------------------------------------------------------------------ lifecycle

def prepare_code(code, *, base_python=None, offline=False, postgres=False):
    """Dependencies of a code directory, shared by every instance running it."""
    from scripts import tfi_release_dependencies, tfi_release_native_dependencies as native
    tfi_release_dependencies.prepare(code, base_python=base_python, offline=offline)
    return native.prepare(code, ("postgres", "nats", "node") if postgres else ("nats", "node"), offline=offline)


def plan(engineering, *, name, root=None, bind="127.0.0.1", ports=None, postgres=None,
         skip_file_verification=False):
    """Validate a creation and return its manifest, without changing the host."""
    from scripts import tfi_release_native_dependencies as native
    manifest_path = Path(engineering).resolve(strict=True)
    manifest, bundle = validate_engineering(manifest_path, skip_file_verification=skip_file_verification)
    preflight = platform_preflight(manifest)
    if not skip_file_verification and source_inventory(engineering_path(bundle, manifest["code"]["path"])) != source_inventory(SOURCE_ROOT):
        raise ValueError("The engineering bundle was assembled from different code")
    if bind not in {"127.0.0.1", "0.0.0.0", "::1", "::"}:
        raise ValueError("bind must be a loopback or wildcard address")
    root = instance_root(name, root)
    directory = Path(postgres) if postgres else Path(native.catalog_entry(SOURCE_ROOT, "postgres")["directory"])
    dependency = {key: str(directory / key) for key in ("bin", "lib", "share")}
    document = manifest_document(name=name, code=SOURCE_ROOT, bind=bind,
                                 ports=allocate_ports(ports or {}, bind), postgres=dependency,
                                 seed_version=manifest["run_id"])
    instance = validate(root, document)
    host_instances.validate_allocation(instance)
    if not windows():
        limit = 104 if sys.platform == "darwin" else 108
        if len(os.fsencode(instance.directory("runtime") / "s" / f".s.PGSQL.{document['network']['postgres_port']}")) >= limit:
            raise ValueError("Instance root is too long for the PostgreSQL socket; choose a shorter location")
    return {"status": "planned", "instance": instance, "manifest": manifest, "bundle": bundle,
            "manifest_path": manifest_path, "platform_preflight": preflight}


def create(engineering, *, name, root=None, bind="127.0.0.1", ports=None, postgres=None, base_python=None,
           offline=False, start=True, skip_file_verification=False, freeze_evidence=None):
    planned = plan(engineering, name=name, root=root, bind=bind, ports=ports, postgres=postgres,
                   skip_file_verification=skip_file_verification)
    instance, manifest, bundle = planned["instance"], planned["manifest"], planned["bundle"]
    report("正在准备运行环境")
    prepare_code(SOURCE_ROOT, base_python=base_python, offline=offline, postgres=postgres is None)
    from scripts.tfi_release_native_dependencies import postgres_dependency
    postgres_dependency(Path(instance.document["dependencies"]["postgres"]["bin"]).parent)
    from management_common.distribution_targets import identify_host
    if identify_host()["architecture"] not in binary_architectures(
            Path(instance.document["dependencies"]["postgres"]["bin"]) / native_tool("postgres")):
        raise ValueError("PostgreSQL does not match this host's architecture")
    root = instance.root
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name_ in FIXED_DIRECTORIES:
        (root / name_).mkdir(mode=0o700, exist_ok=True)
    for name_ in ("s", "tmp"):
        (root / "runtime" / name_).mkdir(mode=0o700, exist_ok=True)
    record = root / instance_services.CREATION_RECORD
    atomic_json(record, {"phase": "configuration", "engineering_sha256": digest(planned["manifest_path"]),
                         "started_at": time.time()})
    atomic_json(root / "instance.json", instance.document)
    atomic_json(root / "site.json", site_settings(instance))
    host_instances.register(instance)
    phase = "configuration"
    try:
        phase = "areas"
        atomic_json(record, {**json.loads(record.read_text()), "phase": phase})
        for area in AREAS:
            instance.area(area).mkdir(parents=True, exist_ok=True, mode=0o700)
        copy_area_content(manifest, bundle, instance, directory_replacements(manifest, SOURCE_ROOT))
        write_secret(instance.directory("secrets") / "postgres.env",
                     "PORTAL_DATABASE_URL=" + database_url(instance) + "\n")
        phase = "database"
        atomic_json(record, {**json.loads(record.read_text()), "phase": phase})
        report("正在初始化数据库")
        initialize_database(instance)
        instance_services.install_definitions(instance)
        instance_services.controller(instance).start(instance_services.unit_name(instance, "postgres"))
        instance_services.wait_database(instance)
        sql = instance.directory("runtime") / "distribution.sql"
        sql.write_text(map_directories(engineering_path(bundle, manifest["database"]["path"]).read_text(encoding="utf-8"),
                                       directory_replacements(manifest, SOURCE_ROOT)), encoding="utf-8")
        report("正在导入示例实例数据")
        import_database(instance, sql)
        sql.unlink()
        phase = "features"
        atomic_json(record, {**json.loads(record.read_text()), "phase": phase})
        features = initialize_features(instance, planned["manifest_path"], manifest, freeze_evidence)
        result = {"status": "created", "name": instance.name, "instance_id": instance.instance_id,
                  "root": str(root), "feature_initialization": features,
                  "external_requirements": manifest.get("external_requirements", [])}
        if start:
            phase = "start"
            atomic_json(record, {**json.loads(record.read_text()), "phase": phase})
            report("正在启动 Portal")
            instance_services.lifecycle("start", root, creating=True)
            observed = wait_for_ready(instance)
            if observed["status"] != "running":
                raise RuntimeError(health_failure(instance, observed))
            result.update(status="running", url=observed["url"])
        record.unlink()
        return result
    except Exception as exc:
        atomic_json(record, {**json.loads(record.read_text()), "phase": phase, "status": "failed",
                             "error": f"{type(exc).__name__}: {exc}"})
        try:
            instance_services.lifecycle("stop", root)
        except Exception as stop_error:  # Recorded; retire still owns the cleanup.
            atomic_json(record, {**json.loads(record.read_text()),
                                 "stop_error": f"{type(stop_error).__name__}: {stop_error}"})
        raise


def select(name=None):
    """--instance through the host registry, or PORTAL_INSTANCE_ROOT."""
    return host_instances.find(name) if name else load()


def lifecycle(action, instance):
    if action == "start":
        result = instance_services.lifecycle("start", instance.root)
        observed = wait_for_ready(instance)
        if observed["status"] != "running":
            raise RuntimeError(health_failure(instance, observed))
        return {**result, "url": observed["url"]}
    return instance_services.lifecycle(action, instance.root)


def remove_tree(path):
    def writable(function, target, _info):
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD | (stat.S_IEXEC if Path(target).is_dir() else 0))
        function(target)
    shutil.rmtree(path, onexc=writable)


def retire(instance, *, delete_external_areas=False):
    """Stop, delete definitions, unregister and delete the root; host resources stay."""
    if host_instances.read_registry().get(instance.name) != str(instance.root):
        raise ValueError("Only a registered instance can be retired")
    if not (instance.root / "instance.json").is_file() or len(instance.root.parts) < 3:
        raise ValueError("Refusing to delete an unrecognized directory")
    instance_services.lifecycle("stop", instance.root)
    removed = instance_services.remove_definitions(instance)
    external = [str(instance.area(area)) for area in AREAS
                if not instance.area(area).is_relative_to(instance.root) and instance.area(area).exists()]
    host_instances.unregister(instance)
    remove_tree(instance.root)
    if delete_external_areas:
        for path in external:
            remove_tree(path)
    return {"status": "retired", "name": instance.name, "root": str(instance.root),
            "removed_services": removed, "external_areas": external,
            "external_areas_deleted": bool(external) and delete_external_areas}


def first_admin(instance, username, *, if_needed=False):
    from management_portal.service_runtime import clean_environment
    if not username:
        raise ValueError("first-admin requires --username")
    code = instance.code_root()
    output = run_utf8_capture([str(runtime_python(code)), str(code / "scripts/portal_setup.py"), "first-admin",
                               "--username", username,
                               "--url", f"http://127.0.0.1:{instance.document['network']['entry_port']}",
                               *(["--if-needed"] if if_needed else [])],
                              env=clean_environment(instance), cwd=code, timeout=120)
    if output.returncode:
        raise RuntimeError("first-admin failed: " + (output.stderr or "").strip()[-500:])
    if if_needed and output.stdout.strip() == '{"status":"already_configured"}':
        return {"status": "already_configured"}
    # The invitation is a secret: keep it out of stdout, logs and receipts.
    path = instance.directory("secrets") / FIRST_ADMIN_FILE
    write_secret(path, output.stdout)
    return {"status": "setup_link_created", "username": username, "setup_file": str(path)}


def configure_ports(instance, changes):
    """Change ports of a stopped instance after the host-wide uniqueness check."""
    from management_common.secret_environment import secret_environment
    from urllib.parse import urlsplit
    keys = {"entry": ("network", "entry_port"), "postgres": ("network", "postgres_port"),
            "nats": ("network", "nats_port"), "release": ("deployments", "release", "web_port"),
            "dev": ("deployments", "dev", "web_port")}
    if not changes or set(changes) - set(keys) or ("dev" in changes and "dev" not in instance.deployments):
        raise ValueError("Specify ports among: " + ", ".join(k for k in keys if k != "dev" or "dev" in instance.deployments))
    states = instance_services.status(instance)
    if any(s["active"] not in {"inactive", "failed"} or s["pid"] for s in states.values()):
        raise RuntimeError("Stop the instance before changing its ports")
    document = deepcopy(instance.document)
    if ("release" in changes and "entry" not in changes and len(document["deployments"]) == 1
            and document["network"]["entry_port"] == document["deployments"]["release"]["web_port"]):
        changes = {**changes, "entry": changes["release"]}  # A single Release Web is the entry.
    for key, port in changes.items():
        *parents, field = keys[key]
        node = document
        for part in parents:
            node = node[part]
        node[field] = port
    updated = validate(instance.root, document)
    # Holds the instance's configuration lock and the host allocation lock.
    host_instances.replace_manifest(updated, expected=instance.document)
    if "postgres" in changes:
        current = urlsplit(secret_environment(instance, "postgres.env")["PORTAL_DATABASE_URL"])
        write_secret(instance.directory("secrets") / "postgres.env", "PORTAL_DATABASE_URL="
                     + database_url(updated, current.username, current.path.lstrip("/")) + "\n")
    return {"status": "configured", "name": instance.name, "network": document["network"],
            "deployments": document["deployments"]}


def update_code(instance, *, base_python=None, offline=False):
    """Code update: prepare the new code's dependencies, refresh definitions, restart."""
    for code in sorted({Path(row["code_root"]) for row in instance.deployments.values()}):
        prepare_code(code, base_python=base_python, offline=offline)
    instance_services.lifecycle("stop", instance.root)
    instance_services.install_definitions(instance)
    return lifecycle("start", instance)


# ------------------------------------------------------------------------ CLI

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "create"):
        sub = commands.add_parser(command)
        sub.add_argument("--engineering", type=Path, required=True, help="manifest.json from prepare_install.py")
        sub.add_argument("--name", required=True)
        sub.add_argument("--root", type=Path, help="Instance root; default is the OS user's data directory")
        sub.add_argument("--bind", default="127.0.0.1")
        for key in PORT_BASES:
            sub.add_argument(f"--{key}-port", type=int)
        sub.add_argument("--postgres", type=Path, help="Existing PostgreSQL directory with bin, lib and share")
        sub.add_argument("--skip-file-verification", action="store_true")
        if command == "create":
            sub.add_argument("--python", type=Path, help="Base Python 3.12 that creates the code environment")
            sub.add_argument("--offline", action="store_true", help="Use only dependencies bundled with the code")
            sub.add_argument("--no-start", action="store_true")
            sub.add_argument("--freeze-evidence", type=Path)
    for command in ("status", "start", "stop", "retire", "first-admin", "configure-ports", "update-code"):
        sub = commands.add_parser(command)
        sub.add_argument("--instance", required=command == "retire",
                         help="Registered instance name; otherwise PORTAL_INSTANCE_ROOT")
        if command == "retire":
            sub.add_argument("--delete-external-areas", action="store_true",
                             help="Also delete areas placed outside the instance root")
        if command == "first-admin":
            sub.add_argument("--username", required=True)
            sub.add_argument("--if-needed", action="store_true")
        if command == "configure-ports":
            for key in ("entry", "postgres", "nats", "release", "dev"):
                sub.add_argument(f"--{key}-port", type=int)
        if command == "update-code":
            sub.add_argument("--python", type=Path)
            sub.add_argument("--offline", action="store_true")
    commands.add_parser("list")
    args = parser.parse_args(argv)
    try:
        if args.command in {"plan", "create"}:
            options = dict(name=args.name, root=args.root, bind=args.bind, postgres=args.postgres,
                           skip_file_verification=args.skip_file_verification,
                           ports={key: getattr(args, key + "_port") for key in PORT_BASES})
            if args.command == "plan":
                planned = plan(args.engineering, **options)
                result = {"status": "planned", "root": str(planned["instance"].root),
                          "manifest": planned["instance"].document, "platform_preflight": planned["platform_preflight"]}
            else:
                result = create(args.engineering, base_python=args.python, offline=args.offline,
                                start=not args.no_start, freeze_evidence=args.freeze_evidence, **options)
        elif args.command == "list":
            result = {"status": "listed", "instances": host_instances.read_registry()}
        else:
            instance = select(args.instance)
            if args.command == "retire":
                result = retire(instance, delete_external_areas=args.delete_external_areas)
            elif args.command == "first-admin":
                result = first_admin(instance, args.username, if_needed=args.if_needed)
            elif args.command == "configure-ports":
                result = configure_ports(instance, {key: value for key in ("entry", "postgres", "nats", "release", "dev")
                                                    if (value := getattr(args, key + "_port")) is not None})
            elif args.command == "update-code":
                result = update_code(instance, base_python=args.python, offline=args.offline)
            else:
                result = lifecycle(args.command, instance)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") not in {"failed", "unhealthy"} else 1
    except Exception as exc:
        # Launchers keep stderr in their logs: call sites only, no locals or arguments.
        print(f"Installer command {args.command} failed ({type(exc).__name__}); call stack:", file=sys.stderr)
        for frame in traceback.extract_tb(exc.__traceback__):
            print(f"  {frame.filename}:{frame.lineno} in {frame.name}", file=sys.stderr)
        result = {"status": "failed", "error": str(exc), "error_type": type(exc).__name__}
        if isinstance(exc, PlatformPreflightError):
            result["platform_preflight"] = exc.report
        print(json.dumps(result, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
