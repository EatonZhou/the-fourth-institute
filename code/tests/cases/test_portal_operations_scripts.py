from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace

import pytest

from scripts import colab_session, install_provider_runtime, portal_operations, publish_release, tfi_dev


LINEAGE = "lineage_0123456789ab"
DEVICE = "device_0123456789ab"
ACTOR = "resident_abcdef012345"
RUN_ID = "script_run_0123456789abcdef01234567"
CODE = Path(__file__).resolve().parents[2]


@pytest.fixture
def runner(monkeypatch, tmp_path):
    state = SimpleNamespace(caller_id=ACTOR, lineage_id=LINEAGE, arguments={}, outputs=[],
                            operation="install_provider_runtime", claims={}, record={}, lookups=[])
    config = {"deployment": {"portal_operations": {"lineage_id": LINEAGE}}}
    monkeypatch.setattr(portal_operations, "read_site_config", lambda: config)
    monkeypatch.setattr(portal_operations, "load", lambda: SimpleNamespace(
        code_root=lambda: CODE, directory=lambda name: tmp_path / name,
    ))
    monkeypatch.setenv("PORTAL_SCRIPT_INPUT", str(tmp_path / "runtime/script_runs" / RUN_ID / "io/input.json"))

    def get_current_run(**arguments):
        state.lookups.append(arguments)
        return {
            "script_run_id": RUN_ID, "lineage_id": state.lineage_id, "caller_id": state.caller_id,
            "path": str(CODE / f"scripts/{state.operation}.py"), "status": "running",
            "arguments": state.arguments, **state.record,
        }

    client = SimpleNamespace(
        inputs=lambda: {"script_run_id": RUN_ID, "caller_id": state.caller_id,
                        "lineage_id": state.lineage_id, "arguments": state.arguments, **state.claims},
        set_output=state.outputs.append,
        isa=lambda name, **arguments: state.domain_isa(name, **arguments),
        get_current_run=get_current_run,
    )
    monkeypatch.setitem(sys.modules, "portal_script", client)
    state.client = client
    return state


def test_locator_uses_shared_code_root_and_fixed_path(monkeypatch, runner):
    monkeypatch.setattr(portal_operations, "load", lambda: SimpleNamespace(code_root=lambda: CODE))
    inputs = {"provider": "kimi"}
    assert portal_operations.run_arguments("install_provider_runtime", inputs) == {
        "arguments": inputs, "lineage_id": LINEAGE,
        "path": str(CODE / "scripts/install_provider_runtime.py"), "runtime": "python",
    }
    with pytest.raises(ValueError, match="Unknown Portal operation"):
        portal_operations.run_arguments("../install_provider_runtime", inputs)


def test_missing_lineage_fails_closed(monkeypatch):
    monkeypatch.setattr(portal_operations, "read_site_config", lambda: {})
    with pytest.raises(ValueError, match="deployment.portal_operations.lineage_id"):
        portal_operations.lineage_id()


def test_install_binding_preserves_existing_site_configuration(tmp_path):
    path = tmp_path / "site.json"
    initial = {"deployment": {"backup": {"enabled": True}}}
    path.write_text(json.dumps(initial))
    instance = SimpleNamespace(root=tmp_path, site_settings=lambda: json.loads(path.read_text()))

    portal_operations.install_binding(instance, LINEAGE)
    site = instance.site_settings()
    assert site["deployment"] == {**initial["deployment"], "portal_operations": {"lineage_id": LINEAGE}}
    previous = path.stat().st_mtime_ns
    portal_operations.install_binding(instance, LINEAGE)
    assert path.stat().st_mtime_ns == previous
    with pytest.raises(ValueError, match="已绑定"):
        portal_operations.install_binding(instance, "lineage_aaaaaaaaaaaa")
    assert instance.site_settings() == site


def test_other_lineage_cannot_start_the_system_operation(runner):
    runner.lineage_id = "lineage_aaaaaaaaaaaa"
    runner.arguments = {"provider": "kimi", "lineage_id": LINEAGE}
    with pytest.raises(PermissionError, match="固定的 Portal 运维 Lineage"):
        install_provider_runtime.main()


def test_script_context_uses_persisted_arguments_and_actor(runner):
    runner.claims = {"lineage_id": "lineage_aaaaaaaaaaaa", "caller_id": "whoever", "arguments": {"provider": "other"}}
    runner.arguments = {"provider": "kimi"}
    assert portal_operations.script_context(runner.operation) == {
        "lineage_id": LINEAGE, "caller_id": ACTOR, "script_run_id": RUN_ID, "arguments": runner.arguments,
    }
    assert runner.lookups == [{"lineage_id": LINEAGE, "script_run_id": RUN_ID}]


@pytest.mark.parametrize("claimed_id", [
    "script_run_aaaaaaaaaaaaaaaaaaaaaaaa", "script_run_ABCDEF0123456789abcdef0123", "../io/input.json", None,
])
def test_script_context_rejects_input_pointing_at_a_different_run(runner, claimed_id):
    runner.claims["script_run_id"] = claimed_id
    with pytest.raises(PermissionError):
        portal_operations.script_context(runner.operation)
    assert runner.lookups == []


@pytest.mark.parametrize("record", [
    {"script_run_id": "script_run_aaaaaaaaaaaaaaaaaaaaaaaa"}, {"path": "scripts/install_provider_runtime.py"},
    {"path": str(CODE / "scripts/publish_release.py")}, {"status": "succeeded"}, {"status": "cancelled"},
    {"caller_id": "portal"}, {"caller_id": "mailbox"}, {"caller_id": 123}, {"caller_id": " user "},
    {"arguments": []},
])
def test_script_context_requires_a_valid_authenticated_running_record(runner, record):
    runner.record.update(record)
    with pytest.raises(PermissionError):
        portal_operations.script_context(runner.operation)


def test_script_context_fails_when_the_runner_relay_rejects_lookup(runner):
    def denied(*args, **kwargs):
        raise RuntimeError("lease inactive")

    runner.client.get_current_run = denied
    with pytest.raises(RuntimeError, match="lease inactive"):
        portal_operations.script_context(runner.operation)


def test_script_context_rejects_another_active_run_despite_matching_input_hints(monkeypatch, runner):
    from management_portal import agent_token_auth
    from management_portal.society_service.script_runs import runner as runtime_runner, service
    from management_portal.society_service.script_runs.clients import portal_script

    other_run = "script_run_aaaaaaaaaaaaaaaaaaaaaaaa"
    other_actor = "resident_aaaaaaaaaaaa"
    input_path = Path(portal_operations.load().directory("runtime")) / "script_runs"
    records = {}
    token_records = {}
    authorized_reads = []
    monkeypatch.setattr(agent_token_auth, "get_agent_token_store", lambda: SimpleNamespace(
        put=token_records.__setitem__,
        candidates=lambda lookup: [(key, value) for key, value in token_records.items()
                                   if value["token_lookup"] == lookup],
    ))
    tokens = {}
    leases = {}
    for identifier, actor, provider in [(RUN_ID, ACTOR, "kimi"), (other_run, other_actor, "codex")]:
        io = input_path / identifier / "io"
        io.mkdir(parents=True)
        record = {**portal_operations.run_arguments(runner.operation, {"provider": provider}),
                  "script_run_id": identifier, "caller_id": actor, "status": "running"}
        records[identifier] = record
        (io / "input.json").write_text(json.dumps(record))
        lease = input_path / identifier / "private-test-lease.json"
        lease.touch()
        leases[identifier] = lease
        tokens[identifier] = service._issue_token(actor, identifier, lease, None)

    class Portal(BaseHTTPRequestHandler):
        def do_POST(self):
            token = self.headers["Authorization"].removeprefix("Bearer ")
            identity = agent_token_auth.authenticate_bearer_token(token, scope="instructions")
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if identity:
                assert request["instruction"] == "GetScriptRun"
                assert request["arguments"]["lineage_id"] == LINEAGE
                identifier = request["arguments"]["script_run_id"]
                authorized_reads.append((identity["agent_id"], identifier))
                result = {"status": "committed", "result": records[identifier]}
            else:
                result = {"status": "failed", "error": {"message": "lease inactive"}}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        def log_message(self, *_args):
            pass

    monkeypatch.setitem(sys.modules, "portal_script", portal_script)
    with ThreadingHTTPServer(("127.0.0.1", 0), Portal) as server, tempfile.TemporaryDirectory() as directory:
        portal_thread = threading.Thread(target=server.serve_forever, daemon=True)
        portal_thread.start()
        relays = []
        try:
            for identifier in records:
                socket_path = Path(directory) / f"{identifier}.sock"
                config = {"portal_url": f"http://127.0.0.1:{server.server_port}", "token": tokens[identifier],
                          "script_run_id": identifier}
                relay = runtime_runner.Relay(socket_path, runtime_runner.http_forward(config),
                                             script_run_id=config["script_run_id"])
                thread = threading.Thread(target=relay.serve_forever, daemon=True)
                relays.append((relay, thread))
                thread.start()
                monkeypatch.setenv("PORTAL_ISA_SOCKET", str(socket_path))
                monkeypatch.setenv("PORTAL_SCRIPT_INPUT", str(input_path / identifier / "io/input.json"))
                assert portal_operations.script_context(runner.operation)["caller_id"] == records[identifier]["caller_id"]

            monkeypatch.setenv("PORTAL_ISA_SOCKET", str(Path(directory) / f"{RUN_ID}.sock"))
            # The input file and its environment pointer now agree on the other run.
            with pytest.raises(RuntimeError, match="当前脚本运行"):
                portal_operations.script_context(runner.operation)
            assert authorized_reads == [(ACTOR, RUN_ID), (other_actor, other_run)]

            monkeypatch.setenv("PORTAL_SCRIPT_INPUT", str(input_path / RUN_ID / "io/input.json"))
            leases[RUN_ID].unlink()
            with pytest.raises(RuntimeError, match="lease inactive"):
                portal_operations.script_context(runner.operation)
        finally:
            for relay, thread in relays:
                relay.shutdown()
                relay.server_close()
                thread.join(timeout=2)
            server.shutdown()
            portal_thread.join(timeout=2)


@pytest.mark.parametrize(("status", "exit_code"), [
    ("ready", 0), ("pending", 0), ("busy", 0), ("failed", 1), ("unknown", 1),
])
def test_provider_install_uses_the_synchronous_owner(monkeypatch, runner, status, exit_code):
    from management_portal.execution import provider_updates

    runner.arguments = {"provider": "kimi"}
    calls = []
    report = {"status": status, "message": "preparation detail"}
    monkeypatch.setattr(provider_updates, "prepare_runtime", lambda provider, *, background:
                        calls.append((provider, background)) or report)
    assert install_provider_runtime.main() == exit_code
    assert calls == [("kimi", False)]
    assert runner.outputs == [{"preparation": report}]


def test_provider_install_does_not_accept_an_actor_argument(monkeypatch, runner):
    from management_portal.execution import provider_updates

    runner.arguments = {"provider": "kimi", "actor": "someone-else"}
    monkeypatch.setattr(provider_updates, "prepare_runtime", lambda *args, **kwargs: pytest.fail("unexpected preparation"))
    with pytest.raises(ValueError, match="参数"):
        install_provider_runtime.main()


@pytest.mark.parametrize(("running", "extra", "action"), [
    (True, {"hardware": "A100"}, "colab.start"), (False, {}, "colab.stop"),
])
def test_colab_checks_device_and_passes_the_run_caller(monkeypatch, runner, running, extra, action):
    runner.operation = "colab_session"
    runner.arguments = {"resource_id": DEVICE, "running": running, **extra}
    resolutions = []
    runner.domain_isa = lambda *args, **kwargs: resolutions.append((args, kwargs)) or {
        "resource": {"resource_id": DEVICE}, "target": {"resource_type": "device"},
    }
    operations = []
    report = {"session": "portal-colab", "changed": True, "running": running}
    monkeypatch.setattr(colab_session, "change_colab_session", lambda *args, **kwargs:
                        operations.append((args, kwargs)) or report)
    assert colab_session.main() == 0
    assert resolutions == [(("ResolveResource",), {"address": DEVICE})]
    assert operations == [((DEVICE, action, extra), {"actor": ACTOR})]
    assert runner.outputs == [report]


def test_colab_permission_error_cannot_turn_into_success(monkeypatch, runner):
    runner.operation = "colab_session"
    runner.arguments = {"resource_id": DEVICE, "running": False}
    runner.domain_isa = lambda *args, **kwargs: {
        "resource": {"resource_id": DEVICE}, "target": {"resource_type": "device"},
    }

    def denied(*args, **kwargs):
        raise PermissionError("device action denied")

    monkeypatch.setattr(colab_session, "change_colab_session", denied)
    with pytest.raises(PermissionError, match="device action denied"):
        colab_session.main()
    assert not runner.outputs


@pytest.mark.parametrize("scope", [
    {}, {"paths": []}, {"paths": ["."]}, {"paths": ["../site.json"]},
    {"paths": ["/tmp/file"]}, {"paths": [r"C:\other"]}, {"all": False},
    {"paths": ["SPEC.md"], "all": True}, {"paths": ["SPEC.md"], "instance": "another"},
])
def test_publish_scope_is_explicit_and_cannot_escape_code(scope):
    with pytest.raises(ValueError, match="参数|路径"):
        publish_release.publish_options({"message": "fix", **scope})


def test_false_publish_all_flag_keeps_explicit_paths():
    assert publish_release.publish_options({"message": "fix", "paths": ["SPEC.md"], "all": False}) == [
        "--message=fix", "--path=code/SPEC.md",
    ]


def test_publish_invokes_configured_dev_and_keeps_partial_push_receipt(monkeypatch, runner, tmp_path):
    runner.operation = "publish_release"
    runner.arguments = {"message": "--fix", "paths": ["SPEC.md", "scripts/publish_release.py"]}
    dev = tmp_path / "dev" / "code"
    calls = []
    report = {
        "commit": "abc", "paths": ["code/SPEC.md"], "status": "failed", "stage": "push",
        "push": {"ok": False, "remote": "origin"}, "health": {"status": "not_queued"},
        "error": "发布推送失败",
    }
    instance = SimpleNamespace(deployments={"release": {}, "dev": {}}, code_root=lambda name: dev)
    monkeypatch.setattr(publish_release, "load", lambda: instance)
    monkeypatch.setattr(publish_release.subprocess, "run", lambda command, **kwargs: (
        calls.append((command, kwargs)) or
        SimpleNamespace(returncode=1, stdout=json.dumps(report), stderr="private details")
    ))
    assert publish_release.main() == 1
    assert calls[0][0] == [
        sys.executable, str(dev / "scripts/tfi_dev.py"), "publish", "--message=--fix",
        "--path=code/SPEC.md", "--path=code/scripts/publish_release.py",
    ]
    assert calls[0][1]["cwd"] == dev.parent
    assert runner.outputs == [report]


@pytest.mark.parametrize("stage", ["push", "activation"])
def test_release_cli_reports_progress_before_a_failure(monkeypatch, tmp_path, capsys, stage):
    from scripts import portal_seed_inventory, tfi_development_runtime

    main, dev = tmp_path / "main", tmp_path / "dev"
    monkeypatch.setattr(tfi_dev, "configuration", lambda: {
        "dev": dev, "main": main, "remote": "origin", "runtime_root": tmp_path, "code_directory": "code",
    })
    monkeypatch.setattr(tfi_dev, "lock", lambda _: nullcontext())
    monkeypatch.setattr(tfi_dev, "branch", lambda *_: None)
    monkeypatch.setattr(portal_seed_inventory, "verify_publication_inventory", lambda *_: None)
    monkeypatch.setattr(tfi_dev, "require_dependency_environments", lambda *_: {})
    calls = []

    def git(root, *args):
        calls.append(args)
        if args[0] == "rev-parse":
            return "dev-sha" if root == dev else "release-sha"
        if args[0] == "diff":
            return "code/SPEC.md"
        if args[0] == "push" and stage == "push":
            raise subprocess.CalledProcessError(1, ("git", *args), stderr="push diagnostic")
        return ""

    def activate(*args, **kwargs):
        calls.append(("activation",))
        raise RuntimeError("activation diagnostic")

    monkeypatch.setattr(tfi_dev, "git", git)
    monkeypatch.setattr(tfi_development_runtime, "request_activation", activate)

    assert tfi_dev.main(["publish", "--message", "fix"]) == 1
    capture = capsys.readouterr()
    report = json.loads(capture.out)
    assert [args[0] for args in calls][-1] == stage
    assert report["status"] == "failed"
    assert report["stage"] == stage
    assert report["commit"] == "dev-sha"
    assert report["release_advanced"] is True
    assert report["paths"] == ["code/SPEC.md"]
    assert report["push"] == {"ok": stage != "push", "remote": "origin"}
    assert report["health"] == {"status": "not_queued" if stage == "push" else "unknown"}
    assert "diagnostic" not in capture.out


def test_large_publish_receipt_keeps_every_path(monkeypatch, runner, tmp_path):
    runner.operation = "publish_release"
    runner.arguments = {"message": "fix", "all": True}
    monkeypatch.setenv("PORTAL_SCRIPT_ARTIFACTS", str(tmp_path))
    monkeypatch.setattr(publish_release, "load", lambda: SimpleNamespace(
        deployments={"dev": {}}, code_root=lambda name: tmp_path,
    ))
    paths = [f"code/long-file-name-{i:04d}" for i in range(3000)]
    report = {"status": "published", "paths": paths, "commit": "abc"}
    monkeypatch.setattr(publish_release.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=json.dumps(report),
    ))
    assert publish_release.main() == 0
    output, = runner.outputs
    assert output["paths_count"] == len(paths)
    assert output["paths"] == []
    assert json.loads((tmp_path / output["paths_artifact"]).read_text())["paths"] == paths
