"""Instance lifecycle: create from the example instance, configure, retire."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from management_common import host_instances
from management_common.instance_manifest import load
from scripts import instance_services, tfi_release_install as installer
from scripts import tfi_release_native_dependencies as native
from tests.cases.instance_test_support import write_instance


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def engineering(tmp_path, *, files=None, mappings=()):
    """A minimal prepare_install bundle: code, SQL and data/<area>/ content."""
    bundle = tmp_path / "bundle"
    contents = {
        "code/scripts/tfi_release_install.py": b"# installer\n",
        "database/distribution.sql": b"SELECT '${code_root}';\n",
        "data/projects/Demo/knowledge_base/plan/a.md": b"plan in ${code_root}\n",
        "data/features/workbench/app.js": b"console.log(1)\n",
        **(files or {}),
    }
    for relative, data in contents.items():
        path = bundle / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    inventory = sorted(({"path": p, "sha256": _digest(d), "size": len(d)} for p, d in contents.items()),
                       key=lambda row: row["path"])
    code_rows = [{**row, "path": row["path"][5:]} for row in inventory if row["path"].startswith("code/")]
    canonical = json.dumps(code_rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    manifest = {"schema": "tfi.release-engineering.v1", "run_id": "seed-run", "supervisor_resident_id": "",
                "unresolved": [], "inventory": inventory,
                "database": {"path": "database/distribution.sql", "format": "postgresql-sql",
                             "sha256": _digest(contents["database/distribution.sql"])},
                "files": {"projects": "data/projects", "features": "data/features"},
                "directory_mappings": list(mappings),
                "code": {"path": "code", "sha256": hashlib.sha256(canonical.encode()).hexdigest()},
                "installer": {"entrypoint": "code/scripts/tfi_release_install.py"}}
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return bundle / "manifest.json"


CODE_MAPPING = {"placeholder": "${code_root}", "root": "code_root", "path": "."}


@pytest.fixture
def host(monkeypatch):
    # A short home keeps the default root's PostgreSQL socket path valid.
    home = Path(tempfile.mkdtemp(prefix="h")).resolve()
    monkeypatch.setattr(host_instances, "user_home", lambda: home)
    monkeypatch.setattr(installer, "platform_preflight", lambda manifest: {"ok": True})
    monkeypatch.setattr(installer, "port_free", lambda port, bind: True)
    yield home
    shutil.rmtree(home)


def test_engineering_inventory_and_area_layout_are_enforced(tmp_path):
    path = engineering(tmp_path, mappings=[CODE_MAPPING])
    manifest, bundle = installer.validate_engineering(path)
    assert manifest["files"] == {"projects": "data/projects", "features": "data/features"}
    (bundle / "data/projects/unlisted.txt").write_text("x")
    with pytest.raises(ValueError, match="inventory"):
        installer.validate_engineering(path)
    (bundle / "data/projects/unlisted.txt").unlink()
    (bundle / "data/features/workbench/app.js").write_text("tampered")
    with pytest.raises(ValueError, match="checksum"):
        installer.validate_engineering(path)
    # Skipping verification avoids reading content but still checks the layout.
    assert installer.validate_engineering(path, skip_file_verification=True)[0]["run_id"] == "seed-run"
    for index, (change, error) in enumerate((
            ({"files": {"share_root": "data/share_root"}}, "area"),
            ({"files": {"projects": "data/elsewhere"}}, "data/<area>"),
            ({"directory_mappings": [{"placeholder": "${share_root}", "root": "share_root", "path": "."}]},
             "placeholder"))):
        candidate = engineering(tmp_path / str(index))
        value = json.loads(candidate.read_text())
        value.update(change)
        candidate.write_text(json.dumps(value))
        with pytest.raises(ValueError, match=error):
            installer.validate_engineering(candidate, skip_file_verification=True)


def test_default_root_is_outside_code_and_registered_ports_are_avoided(host, tmp_path):
    root = installer.instance_root("accept_tmp")
    assert root == host / ".local/share/tfi/instances/accept_tmp"
    with pytest.raises(ValueError, match="code directory"):
        installer.instance_root("x", installer.SOURCE_ROOT.parent / "instance/x")
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "file").write_text("x")
    with pytest.raises(ValueError, match="empty"):
        installer.instance_root("x", occupied)
    other = write_instance(tmp_path / "other")
    host_instances.register(load(other))
    ports = installer.allocate_ports({}, "127.0.0.1")
    assert not set(ports.values()) & host_instances.ports(load(other))
    with pytest.raises(ValueError, match="another registered instance"):
        installer.allocate_ports({"web": 28086}, "127.0.0.1")
    assert installer.service_prefix("Accept_Tmp-01") == "tfi-accept-tmp-01"
    with pytest.raises(ValueError, match="three"):
        installer.service_prefix("ab")


def test_plan_writes_nothing_and_manifest_has_no_python(host, tmp_path):
    path = engineering(tmp_path)
    planned = installer.plan(path, name="accept_tmp", postgres=tmp_path / "pg", skip_file_verification=True)
    instance = planned["instance"]
    assert not instance.root.exists() and not host_instances.registry_path().exists()
    document = instance.document
    assert document["deployments"] == {"release": {"kind": "release", "code_root": str(installer.SOURCE_ROOT),
                                                   "web_port": document["network"]["entry_port"]}}
    assert document["services"] == {"prefix": "tfi-accept-tmp", "components": list(installer.CORE_COMPONENTS)}
    assert document["dependencies"] == {"postgres": {k: str(tmp_path / "pg" / k) for k in ("bin", "lib", "share")}}
    assert "python" not in json.dumps(document).lower() and document["seed_version"] == "seed-run"
    clash = write_instance(tmp_path / "clash")
    value = json.loads((clash / "instance.json").read_text())
    value["services"]["prefix"] = "tfi-accept-tmp"
    (clash / "instance.json").write_text(json.dumps(value))
    host_instances.register(load(clash))
    with pytest.raises(ValueError, match="prefix"):
        installer.plan(path, name="accept_tmp", postgres=tmp_path / "pg", skip_file_verification=True)


class FakeControl:
    def __init__(self):
        self.calls = []

    def start(self, name):
        self.calls.append(("start", name))

    def stop(self, name):
        self.calls.append(("stop", name))

    def status(self, name):
        return {"active": "inactive", "pid": 0, "load": "not-found"}


@pytest.fixture
def creation(host, tmp_path, monkeypatch):
    calls = []
    control = FakeControl()
    monkeypatch.setattr(installer, "prepare_code", lambda code, **kw: calls.append(("prepare", code, kw)))
    monkeypatch.setattr(native, "postgres_dependency", lambda directory: None)
    monkeypatch.setattr(installer, "binary_architectures", lambda path: {"x86_64", "aarch64"})
    monkeypatch.setattr(installer, "initialize_database", lambda instance: calls.append(("initdb",)))
    monkeypatch.setattr(installer, "import_database",
                        lambda instance, sql: calls.append(("import", sql.read_text())))
    monkeypatch.setattr(installer, "initialize_features",
                        lambda instance, path, manifest, evidence=None: {"work_dispatched": False})
    monkeypatch.setattr(instance_services, "install_definitions", lambda instance: calls.append(("define",)))
    monkeypatch.setattr(instance_services, "controller", lambda instance: control)
    monkeypatch.setattr(instance_services, "wait_database", lambda instance: None)
    monkeypatch.setattr(instance_services, "lifecycle",
                        lambda action, root, **kw: calls.append(("lifecycle", action, kw)) or {"status": action})
    monkeypatch.setattr(installer, "wait_for_ready",
                        lambda instance, **kw: {"status": "running", "url": "http://127.0.0.1:1"})
    return SimpleNamespace(calls=calls, control=control, engineering=engineering(tmp_path, mappings=[CODE_MAPPING]),
                           postgres=tmp_path / "pg")


def test_create_writes_manifest_site_secrets_and_areas_then_registers(creation):
    result = installer.create(creation.engineering, name="accept_tmp", postgres=creation.postgres,
                              skip_file_verification=True)
    instance = host_instances.find("accept_tmp")
    root = instance.root
    assert result["status"] == "running" and result["root"] == str(root)
    assert sorted(p.name for p in root.iterdir()) == ["cache", "data", "database", "instance.json",
                                                       "runtime", "secrets", "site.json"]
    assert json.loads((root / "site.json").read_text())["deployment"]["auth"] == {
        "session_cookie_name": "db_session_tfi-accept-tmp"}
    url = (root / "secrets/postgres.env").read_text().strip().partition("=")[2]
    parsed = urlsplit(url)
    assert parsed.username == installer.DATABASE_ROLE and parsed.path == "/" + installer.DATABASE_NAME
    assert parse_qs(parsed.query) == {"host": [str(root / "runtime/s")],
                                      "port": [str(instance.document["network"]["postgres_port"])]}
    # data/<area>/ content is copied with the code placeholder materialized.
    assert (instance.area("projects") / "Demo/knowledge_base/plan/a.md").read_text() == \
        f"plan in {installer.SOURCE_ROOT}\n"
    assert (instance.area("features") / "workbench/app.js").is_file()
    assert ("import", f"SELECT '{installer.SOURCE_ROOT}';\n") in creation.calls
    assert not (root / "runtime/distribution.sql").exists()
    assert ("start", "tfi-accept-tmp-postgres.service" if installer.os.name != "nt" else "tfi-accept-tmp-postgres") \
        in creation.control.calls
    assert ("lifecycle", "start", {"creating": True}) in creation.calls
    assert instance_services.creation_record(instance) is None
    assert instance_services.start_blocker(instance) == ""


def test_create_without_start_stops_initialization_services(creation):
    result = installer.create(creation.engineering, name="accept_tmp", postgres=creation.postgres,
                              skip_file_verification=True, start=False)
    assert result["status"] == "created"
    assert ("lifecycle", "stop", {}) in creation.calls
    assert not any(call[:2] == ("lifecycle", "start") for call in creation.calls)
    assert instance_services.creation_record(host_instances.find("accept_tmp")) is None


def test_create_without_start_records_shutdown_failure(creation, monkeypatch):
    def broken(action, root, **kwargs):
        raise RuntimeError("initialization service did not stop")
    monkeypatch.setattr(instance_services, "lifecycle", broken)
    with pytest.raises(RuntimeError, match="did not stop"):
        installer.create(creation.engineering, name="accept_tmp", postgres=creation.postgres,
                         skip_file_verification=True, start=False)
    record = instance_services.creation_record(host_instances.find("accept_tmp"))
    assert record["status"] == "failed" and record["phase"] == "stop"


def test_failed_creation_keeps_a_blocking_record_until_retired(creation, monkeypatch):
    def broken(instance, sql):
        raise RuntimeError("psql exited 3")
    monkeypatch.setattr(installer, "import_database", broken)
    with pytest.raises(RuntimeError, match="psql"):
        installer.create(creation.engineering, name="accept_tmp", postgres=creation.postgres,
                         skip_file_verification=True)
    instance = host_instances.find("accept_tmp")
    record = instance_services.creation_record(instance)
    assert record["phase"] == "database" and record["status"] == "failed" and "psql" in record["error"]
    assert "creation has not completed" in instance_services.start_blocker(instance)
    assert ("lifecycle", "stop", {}) in creation.calls


def test_retire_stops_removes_unregisters_and_keeps_external_areas(creation, tmp_path, monkeypatch):
    installer.create(creation.engineering, name="accept_tmp", postgres=creation.postgres,
                     skip_file_verification=True)
    instance = host_instances.find("accept_tmp")
    external = tmp_path / "big-disk/papers"
    external.mkdir(parents=True)
    document = json.loads((instance.root / "instance.json").read_text())
    document["areas"] = {"papers": str(external)}
    (instance.root / "instance.json").write_text(json.dumps(document))
    removed = []
    monkeypatch.setattr(instance_services, "remove_definitions", lambda inst: removed.append(inst.name) or ["x"])
    result = installer.retire(host_instances.find("accept_tmp"))
    assert result["external_areas"] == [str(external)] and result["external_areas_deleted"] is False
    assert removed == ["accept_tmp"] and ("lifecycle", "stop", {}) in creation.calls
    assert not instance.root.exists() and external.is_dir()
    assert "accept_tmp" not in host_instances.read_registry()
    with pytest.raises(ValueError, match="registered"):
        installer.retire(load(write_instance(tmp_path / "unregistered")))


def test_ports_change_only_while_stopped_and_follow_into_database_url(host, tmp_path, monkeypatch):
    root = write_instance(tmp_path / "site")
    instance = load(root)
    host_instances.register(instance)
    installer.write_secret(root / "secrets/postgres.env",
                           "PORTAL_DATABASE_URL=" + installer.database_url(instance, "tfi", "portal_db") + "\n")
    running = {"active": "active", "pid": 7}
    monkeypatch.setattr(instance_services, "status", lambda inst: {"postgres": running})
    with pytest.raises(RuntimeError, match="Stop the instance"):
        installer.configure_ports(instance, {"postgres": 25999})
    monkeypatch.setattr(instance_services, "status", lambda inst: {"postgres": {"active": "inactive", "pid": 0}})
    result = installer.configure_ports(instance, {"postgres": 25999, "release": 28999})
    assert result["network"]["postgres_port"] == 25999
    # A single Release Web is also the entry.
    assert load(root).document["network"]["entry_port"] == 28999
    url = urlsplit((root / "secrets/postgres.env").read_text().strip().partition("=")[2])
    assert parse_qs(url.query)["port"] == ["25999"] and url.path == "/portal_db"
    other = write_instance(tmp_path / "other")
    value = json.loads((other / "instance.json").read_text())
    value.update(name="other", instance_id="other-id")
    value["services"]["prefix"] = "tfi-other"
    value["network"].update(postgres_port=26111, nats_port=26112, entry_port=26113)
    value["deployments"]["release"]["web_port"] = 26113
    (other / "instance.json").write_text(json.dumps(value))
    host_instances.register(load(other))
    with pytest.raises(ValueError, match="port"):
        installer.configure_ports(load(root), {"nats": 26112})


def test_first_admin_keeps_invitation_in_secrets(host, tmp_path, monkeypatch):
    root = write_instance(tmp_path / "site")
    commands = []

    def run(command, **kwargs):
        commands.append((command, kwargs["env"]))
        return SimpleNamespace(returncode=0, stdout="https://portal/join#invite=secret\n", stderr="")
    monkeypatch.setattr(installer, "run_utf8_capture", run)
    monkeypatch.setattr(installer, "runtime_python", lambda code: Path("/venv/bin/python"))
    monkeypatch.setattr("management_portal.service_runtime.clean_environment",
                        lambda instance, **kw: {"PORTAL_INSTANCE_ROOT": str(instance.root)})
    result = installer.first_admin(load(root), "admin")
    assert "invite=" not in json.dumps(result)
    assert Path(result["setup_file"]) == root / "secrets" / installer.FIRST_ADMIN_FILE
    assert "invite=secret" in Path(result["setup_file"]).read_text()
    assert commands[0][1] == {"PORTAL_INSTANCE_ROOT": str(root)}
    assert commands[0][0][-2:] == ["--url", "http://127.0.0.1:28086"]


def test_health_requires_this_instance_and_release_deployment(tmp_path, monkeypatch):
    root = write_instance(tmp_path / "site")
    instance = load(root)
    monkeypatch.setattr(instance_services, "status",
                        lambda inst: {c: {"active": "active", "pid": 1} for c in instance_services.components(inst)})
    payloads = {"/api/health": {"ok": True, "instance": "someone-else", "deployment": "release",
                                "execution_layer": {"ok": True}, "society_service": {"ok": True}}}

    class Response:
        def __init__(self, route):
            self.status, self.route = 200, route

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(payloads.get(self.route, {})).encode()
    monkeypatch.setattr(installer._LOCAL_HTTP, "open", lambda url, timeout: Response(urlsplit(url).path))
    (root / "runtime").mkdir(exist_ok=True)
    (root / "runtime/worker-health.json").write_text(json.dumps(
        {"ok": True, "instance": "fixture", "updated_at": installer.time.time()}))
    assert installer.observed_status(instance)["status"] == "unhealthy"
    payloads["/api/health"]["instance"] = "fixture"
    assert installer.observed_status(instance)["status"] == "running"


def test_cli_reports_failures_as_json_without_traceback_detail(host, capsys):
    assert installer.main(["list"]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "listed", "instances": {}}
    assert installer.main(["start", "--instance", "missing"]) == 1
    output = capsys.readouterr()
    assert json.loads(output.out.splitlines()[-1])["error"] == "Instance is not registered on this host"


def test_health_failure_names_unready_layers_and_failed_component_logs(tmp_path, monkeypatch):
    """A 503 health body and a stopped Worker are reported with the Worker's log tail."""
    import io
    from urllib.error import HTTPError
    root = write_instance(tmp_path / "site")
    instance = load(root)
    (root / "runtime").mkdir(exist_ok=True)
    monkeypatch.setattr(instance_services, "status", lambda inst: {
        c: {"active": "inactive" if c == "worker" else "active", "pid": 0} for c in instance_services.components(inst)})
    payload = {"ok": False, "instance": "fixture", "deployment": "release",
               "execution_layer": {"ok": False}, "society_service": {"ok": True}}
    responses = [HTTPError("http://x/api/health", 503, "Unavailable", {}, io.BytesIO(json.dumps(payload).encode())),
                 OSError("not ready")]

    def open_url(url, timeout):
        raise responses.pop(0)
    monkeypatch.setattr(installer._LOCAL_HTTP, "open", open_url)
    (root / "runtime/worker.log").write_text("earlier\n" * 100 + "RuntimeError: fixture startup failure\n")
    (root / "runtime/web.log").write_text("old-start-marker\n" + "old output\n" * 2000 + "ValueError: web failure\n")
    result = installer.observed_status(instance)
    assert result["checks"][0]["payload"] == payload and result["checks"][0]["http_status"] == 503
    failures = installer.health_failures(result)
    assert "worker: inactive" in failures
    assert "/api/health: HTTP 503; 未就绪: execution_layer" in failures
    error = installer.health_failure(instance, result)
    assert "RuntimeError: fixture startup failure" in error and "ValueError: web failure" in error
    assert "old-start-marker" not in error
