"""The directory list on Google's new/load wire requests is scoped to the turn."""
from pathlib import Path

import pytest

from management_common import host_instances
from management_common.instance_manifest import load
from management_portal import antigravity_accounts as accounts
from management_portal.execution.antigravity_backend import AntigravityOptions
from management_portal.execution.antigravity_cli_backend import AntigravityCliBackend as AntigravityBackend
from management_portal.execution.backend_contracts import BackendInvocation
from management_portal.society_service.resident_session_backend_domain import build_backend_session_operation_request
from management_portal.society_service.resident_turn_scope import filesystem_scope
from tests.cases.instance_test_support import write_instance
from tests.cases.test_antigravity import agent, context, invocation, requests


RESIDENT = "resident_123456789abc"


@pytest.fixture
def turn(agent, monkeypatch):
    instance = load(write_instance(agent / "instance"))
    foreign = load(write_instance(agent / "other", areas={"projects": agent / "other-projects"}))
    monkeypatch.setenv("PORTAL_INSTANCE_ROOT", str(instance.root))
    monkeypatch.setattr(host_instances, "read_registry", lambda: {
        instance.name: str(instance.root), "foreign": str(foreign.root)})
    monkeypatch.setattr(accounts, "environment", lambda _: {
        "GEMINI_HOME": str(agent), "FAKE_ADDITIONAL_DIRECTORIES": "1"})

    folder = instance.area("lineages") / "lineage_123456789abc" / "residents" / RESIDENT
    cwd = folder / "workspace"
    shared = folder.parents[1] / "shared"
    project = agent / "RoboLab"
    for path in (cwd, shared, project, foreign.area("projects")):
        path.mkdir(parents=True, exist_ok=True)
    call = invocation(cwd)
    call.request.agent_id = RESIDENT
    scope = filesystem_scope({"resident_id": RESIDENT}, {"actor": "user"}, cwd=str(cwd), resident_folder=str(folder),
                             bindings=[{"path": str(p)} for p in (cwd, shared, project, shared)])
    call.request.metadata = {"business_type": "resident", "resident_id": RESIDENT, "filesystem_scope": scope}
    return call, scope, folder, shared, project, foreign


def test_new_and_load_refresh_current_writable_roots(agent, turn):
    call, scope, folder, shared, project, _ = turn
    backend = AntigravityBackend()
    call.request.operation = "create"
    first = backend.execute(call, context()[0])
    call.request.operation = "turn"
    call.session.backend_session_id = first.backend_session_id
    scope["paths"] = [row for row in scope["paths"] if row["path"] != str(project)]
    backend.execute(call, context()[0])
    frames = [r for r in requests(agent) if r["method"] in {"session/new", "session/load"}]
    assert [r["method"] for r in frames] == ["session/new", "session/load"]
    assert [r["params"]["additionalDirectories"] for r in frames] == [
        [str(folder), str(shared), str(project)], [str(folder), str(shared)]]


def test_unsafe_or_unbound_paths_are_not_sent(agent, turn):
    call, scope, folder, shared, project, foreign = turn
    symlink = agent / "changed-binding"
    symlink.symlink_to(project, target_is_directory=True)
    file = project / "note"
    file.touch()
    def row(path, *, access="write", source="workspace_binding"):
        return {"path": str(path), "access": access, "source": source}
    scope["paths"] = [row(shared), row(file), row(project / "missing"), row("relative"), row(symlink),
                      row(Path(agent.anchor)), row(foreign.root), row(foreign.area("projects")),
                      row(project, access="read"), row(folder, source="message_attachment"),
                      row(project, source="resident_folder"), row(folder.parents[2]), row(agent)]
    call.request.runtime_options["add_dirs"] = [str(project)]
    AntigravityBackend().execute(call, context()[0])
    frame = next(r for r in requests(agent) if r["method"] == "session/new")
    assert frame["params"]["additionalDirectories"] == [str(shared)]


def test_writable_parent_cannot_promote_read_only_binding(agent, turn):
    call, scope, folder, shared, project, _ = turn
    restricted = project / "read-only"
    restricted.mkdir()
    scope["paths"].append({"path": str(restricted), "source": "workspace_binding", "access": "read"})
    AntigravityBackend().execute(call, context()[0])
    frame = next(r for r in requests(agent) if r["method"] == "session/new")
    assert frame["params"]["additionalDirectories"] == [str(folder), str(shared)]


@pytest.mark.parametrize("owner,identity,preset,allowed", [
    (True, RESIDENT, "full-access", True),
    (False, RESIDENT, "full-access", False),
    (True, "resident_ffffffffffff", "full-access", False),
    (True, RESIDENT, "portal-tools-only", False),
])
def test_resident_session_creation_scope_survives_option_preparation(agent, turn, owner, identity, preset, allowed):
    call, scope, folder, shared, project, _ = turn
    request = build_backend_session_operation_request(
        backend="antigravity", operation="create", actor="caller", cwd=call.request.cwd,
        binding_id=f"resident:{RESIDENT}", correlation_id="creation-test",
        metadata={"filesystem_scope": scope},  # This alone cannot authorize any directory.
        runtime_options={"runtime_settings": {"permission_preset": preset, "model": "gemini-test"},
                         "runtime_root": str(agent), "is_owner": owner, "authorization_scope": scope,
                         "resident_environment": {"PORTAL_RESIDENT_ID": identity}},
    )
    actual = BackendInvocation(request=request, session=call.session, user_prompt="", system_context="",
                               attach_system_context=False, is_new_backend_session=True)
    prepared = AntigravityOptions().prepare(actual)
    assert "authorization_scope" not in prepared.request.runtime_options
    AntigravityBackend().execute(prepared, context()[0])
    frame = next(r for r in requests(agent) if r["method"] == "session/new")
    assert frame["params"].get("additionalDirectories", []) == ([str(folder), str(shared), str(project)] if allowed else [])


@pytest.mark.parametrize("condition", ["restricted", "disabled", "wrong_resident", "wrong_principal", "wrong_cwd",
                                       "not_resident", "old_record", "old_runtime"])
def test_narrow_or_older_session_load(agent, turn, monkeypatch, condition):
    call, scope, *_ = turn
    call.session.backend_session_id = "retained-session"
    if condition == "restricted":
        call.request.permission_profile = "restricted"
    elif condition == "disabled":
        call.request.runtime_options["native_tools_enabled"] = False
    elif condition == "wrong_resident":
        call.request.agent_id = "resident_ffffffffffff"
    elif condition == "wrong_principal":
        scope["principal_id"] = "resident_ffffffffffff"
    elif condition == "wrong_cwd":
        scope["cwd"] = str(agent)
    elif condition == "not_resident":
        call.request.metadata["business_type"] = "other"
    elif condition == "old_record":
        del call.request.metadata["filesystem_scope"]
    elif condition == "old_runtime":
        monkeypatch.setattr(accounts, "environment", lambda _: {"GEMINI_HOME": str(agent)})
    if condition in {"restricted", "disabled", "old_runtime"}:
        error = RuntimeError if condition == "old_runtime" else PermissionError
        with pytest.raises(error):
            AntigravityBackend().execute(call, context()[0])
        return
    AntigravityBackend().execute(call, context()[0])
    frame = next(r for r in requests(agent) if r["method"] == "session/load")["params"]
    assert "additionalDirectories" not in frame
