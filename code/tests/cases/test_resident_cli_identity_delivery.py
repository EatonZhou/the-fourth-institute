"""
:module: lineage.service
:aspects: functional, behavior
"""
from __future__ import annotations
from management_portal.society_service import resident_backend

import copy
import json
import os
from types import SimpleNamespace
from pathlib import Path
import subprocess
import sys
from unittest import mock

import pytest

from management_portal.execution.backends import BackendContext, BackendInvocation, CodexBackend
from management_portal.execution.native_options import prepare_invocation
from management_portal.execution.contracts import ExecutionRequest
from management_portal.execution.session_manager import RuntimeSession
from management_portal.society_service import (
    resident_access,
    resident_execution_engine_domain,
    resident_hatg_credentials,
    resident_prompt_domain,
    resident_record_domain,
    resident_turn_scope,
)
from management_portal.society_service import resident_execution_domain
from management_portal.society_service import resident_runtime_domain
from tests.cases.test_resident_execution_protocol import resident_record
from scripts import resident_hatg_instruction


@pytest.mark.parametrize("session_id", ["", "existing-session-on-one"])
def test_resident_request_delivers_cli_identity_without_sender_impersonation(
    resident_record, monkeypatch, tmp_path, session_id,
):
    """Resident 每轮执行传递规范 CLI 身份且不冒充发送者"""
    record = resident_record
    calls = []
    monkeypatch.setattr(resident_backend, "record_session_id", lambda _: session_id)
    monkeypatch.setattr(resident_runtime_domain, "resident_session_account_id", lambda *a, **k: "one")
    monkeypatch.setattr(resident_access, "has_resident_workspace_authority", lambda *a: False)
    monkeypatch.setattr(
        resident_hatg_credentials, "ensure_resident_credential",
        lambda resident_id, lineage_id: calls.append((resident_id, lineage_id)),
    )
    scope = {
        "principal_id": record["resident_id"],
        "source_principal_id": "test-actor", "paths": [],
    }
    monkeypatch.setattr(resident_turn_scope, "filesystem_scope", lambda *a, **k: scope)
    configured = {"config_overrides": {
        "unrelated.setting": True,
        "shell_environment_policy.set": {
            "PORTAL_RESIDENT_ID": "resident_111111111111",
            "PORTAL_LINEAGE_ID": "lineage_111111111111",
            "LANG": "C.UTF-8",
        },
    }}
    original = copy.deepcopy(configured)
    from management_portal.execution.native_options import CodexOptions
    monkeypatch.setattr(CodexOptions, "runtime_options", staticmethod(lambda *a, **k: copy.deepcopy(configured)))
    monkeypatch.setenv("PORTAL_RESIDENT_ID", "resident_222222222222")
    monkeypatch.setenv("PORTAL_LINEAGE_ID", "lineage_222222222222")
    before_environment = dict(os.environ)
    job = {
        "actor": "test-actor", "job_id": "job-cli-identity",
        "user_message_id": "msg_1111111111111111", "message": "query authorized papers",
        "resident_id": "resident_333333333333", "lineage_id": "lineage_333333333333",
    }

    request = resident_execution_domain._build_resident_execution_request(record, job)
    request = ExecutionRequest.from_payload(request.to_payload())
    request = prepare_invocation(BackendInvocation(request, SimpleNamespace(backend_route_id="one"), request.prompt, "", False, False)).request
    config = CodexBackend._thread_config(request.runtime_options)
    environment = config["shell_environment_policy.set"]
    assert environment["PORTAL_RESIDENT_ID"] == record["resident_id"]
    assert environment["PORTAL_LINEAGE_ID"] == record["lineage_id"]
    assert environment["LANG"] == "C.UTF-8"
    assert request.actor == "test-actor"
    assert request.agent_id == record["resident_id"]
    assert request.metadata["lineage_id"] == record["lineage_id"]
    assert request.runtime_options["allow_backend_route_fallback"] is False
    assert not {"approval_policy", "sandbox_policy", "permissions"} & set(request.runtime_options)
    assert calls == [(record["resident_id"], record["lineage_id"])]
    assert os.environ == before_environment
    assert configured == original
    with monkeypatch.context() as shell:
        for key, value in environment.items():
            shell.setenv(key, value)
        assert resident_hatg_instruction._resident_id("") == record["resident_id"]
        with pytest.raises(RuntimeError, match="不一致"):
            resident_hatg_instruction._resident_id(job["resident_id"])


@pytest.mark.parametrize("backend", ["codex", "claude", "kimi"])
def test_every_turn_carries_exact_cli_identity_in_front_of_the_message(
    resident_record, monkeypatch, backend,
):
    """每轮在消息前带上准确的 CLI 身份

    标准预设下运行身份作为每轮的消息前部分发送，包含 resident_id、lineage_id 与 HATG ISA 调用说明，不进入领域拼好的消息。
    """
    record = {**resident_record, "backend": backend}
    monkeypatch.setattr(resident_backend, "record_session_id", lambda _: "existing-session")
    job = {"actor": "test-actor", "message": "query authorized papers", "context_refs": {}}
    prompt = resident_prompt_domain.build_resident_prompt(record, dict(job))
    parts = {part.name: part for part in resident_execution_domain.resident_system_instructions(record, job).parts}
    identity = parts["runtime_identity"]
    # The standard preset sends the identity on every turn, before the message.
    assert (identity.timing, identity.placement) == ("every_turn", "prompt_prefix")
    assert f"--resident-id {record['resident_id']}" in identity.content
    assert f"lineage_id: {record['lineage_id']}" in identity.content
    assert "hatg_instruction" in identity.content
    assert "query authorized papers" in prompt
    assert "<resident-runtime-identity>" not in prompt
    assert "<portal-system-prompt" not in prompt


@pytest.mark.parametrize("actor,expected", [
    ("sample_admin", "当前发送者是用户 sample_admin；"),
    ("resident_2a1fc6cd272d", "当前消息由 Resident resident_2a1fc6cd272d 发送；"
                              "本轮回复只留在你自己的对话里，不会自动送回 resident_2a1fc6cd272d，"
                              "要让它收到，用 DeliverMessage 发给它；"),
    ("sample_guest", "当前发送者是用户 sample_guest；"),
    ("portal", "当前消息由 Portal 服务 portal 投递；"),
    ("", "当前消息由 Portal 投递；"),
])
def test_turn_prompt_names_the_real_sender(resident_record, monkeypatch, tmp_path, actor, expected):
    """每轮提示写明真实发送者

    每轮提示按任务的真实发送者说明 owner、其他用户、Resident、Portal 服务或未标明的投递，不把非 owner 的消息说成 owner 的；发送者是 Resident 时写明本轮回复只留在自己的对话里，不会自动送回，要让它收到须用 DeliverMessage 发给它。

    A message from another Resident or a non-owner is not presented as the owner's.

    :design: decision_c524c775e0be
    """

    monkeypatch.setattr(resident_runtime_domain, "resident_working_directory", lambda _: tmp_path)
    monkeypatch.setattr(resident_backend, "record_session_id", lambda _: "existing-session")
    monkeypatch.setattr(resident_prompt_domain, "attached_references_text", lambda refs: "<resource address=\"r\"/>" if refs else "")
    prompt = resident_prompt_domain.build_resident_prompt(resident_record, {
        "actor": actor, "message": "status?", "context_refs": {"resources": ["r"]},
    })
    assert expected in prompt
    assert "本 Resident Agent 的 owner" not in prompt
    assert '"owner_ids"' not in prompt


@pytest.mark.parametrize("reference,embedded,internal", [
    ("family-route/resident_2a1fc6cd272d/message/msg_0123456789abcdef0123456789abcdef", False, True),
    ("family-route/resident_2a1fc6cd272d/message/msg_0123456789abcdef0123456789abcdef", True, True),
    ("family-route/resident_111111111111/message/msg_0123456789abcdef0123456789abcdef", False, False),
    ("family-route/resident_2a1fc6cd272d", False, False),
])
def test_family_router_prompt_keeps_control_packets_off_the_facade_inbox(
    resident_record, monkeypatch, reference, embedded, internal,
):
    """The routing duty's packet continues its Effect, rather than becoming a new facade request."""

    monkeypatch.setattr(resident_backend, "record_session_id", lambda _: "existing-session")
    monkeypatch.setattr(resident_prompt_domain, "attached_references_text", lambda _: '<resource address="r"/>')
    job = {"actor": "resident_2a1fc6cd272d", "message": "为原请求选择承接成员，输出 route packet。",
           "context_refs": {"resources": ["r"]}}
    if embedded:
        job["input_message"] = {"business_ref": reference}
    else:
        job["business_ref"] = reference
    prompt = resident_prompt_domain.build_resident_prompt(resident_record, job)
    assert ("只输出 route packet" in prompt) is internal
    assert ("要让它收到，用 DeliverMessage 发给它" in prompt) is not internal


def test_a_model_api_resident_is_told_it_has_no_tools(resident_record) -> None:
    """Model API Resident 的运行身份写明没有工具

    provider_api Resident 的运行身份块说明它通过模型 API 直接作答，没有读写文件、运行命令、调用 HATG 指令或启动子 agent 的工具，每轮只看到本轮消息；其他 Provider 的身份块仍说明怎样以自身身份调用 HATG ISA。

    A provider_api Resident's model has no tools: told to call them, it writes commands it never ran.

    :module: resident.identity
    :importance: P1
    :aspects: functional
    """
    agent = resident_runtime_domain.resident_runtime_identity_block(resident_record)
    model_api = resident_runtime_domain.resident_runtime_identity_block({**resident_record, "backend": "provider_api"})
    assert "hatg_instruction 工具" in agent and "没有任何工具" not in agent
    assert "没有任何工具" in model_api and "hatg_instruction" not in model_api
    assert f"resident_id: {resident_record['resident_id']}" in model_api


@pytest.mark.parametrize("field,value", [
    ("resident_id", ""), ("resident_id", "test-actor"),
    ("lineage_id", ""), ("lineage_id", "resident_111111111111"),
])
def test_cli_identity_delivery_rejects_incomplete_canonical_identity(
    resident_record, field, value,
):
    """CLI 身份注入拒绝缺失或非法 Resident 与 Lineage ID"""
    record = {**resident_record, field: value}
    with pytest.raises(ValueError):
        resident_runtime_domain.resident_runtime_identity_block(record)


@pytest.mark.parametrize("full_access", [False, True])
def test_native_turn_keeps_resident_cli_environments_request_local(
    resident_record, monkeypatch, tmp_path, full_access,
):
    """共享原生客户端逐请求隔离 Resident CLI 环境"""
    expected_database_url = os.environ["PORTAL_DATABASE_URL"]
    monkeypatch.setattr(resident_runtime_domain, "reconcile_resident_codex_runtime_configuration", lambda record: record)
    monkeypatch.setattr(resident_hatg_credentials, "ensure_resident_credential", lambda *a: None)
    monkeypatch.setattr(resident_runtime_domain, "resident_working_directory", lambda *a: tmp_path)
    monkeypatch.setattr(resident_backend, "record_session_id", lambda *a: "existing-session")
    monkeypatch.setattr(resident_runtime_domain, "resident_session_account_id", lambda *a, **k: "one")
    def runtime_options(record, job, **_kwargs):
        job["filesystem_scope"] = {}
        return {
            "runtime_settings": {"inherit_session_runtime": True},
            "resident_environment": resident_runtime_domain.resident_cli_environment(record),
        }

    monkeypatch.setattr(resident_execution_engine_domain, "resident_execution_runtime_options", runtime_options)
    monkeypatch.setattr(resident_access, "has_resident_workspace_authority", lambda *a: full_access)
    route = mock.Mock(account_id="one", socket_path=tmp_path / "unused.sock")
    registry = mock.Mock()
    registry.get.return_value = route
    registry.find_thread_rollout.return_value = None
    client = mock.Mock()
    backend = CodexBackend(account_registry=registry, client_factory=lambda _: client)
    requests = []
    observed = []
    observed_database = []

    def run_turn(*args, **kwargs):
        environment = kwargs["config"]["shell_environment_policy.set"]
        observed_database.append(environment.get("PORTAL_DATABASE_URL") == expected_database_url)
        # The CLI's actual parser runs in a child environment, without credentials or HTTP.
        parsed = subprocess.run(
            [sys.executable, "-c", "import json, os; from scripts.resident_hatg_instruction import _resident_id; "
             "print(json.dumps({'resident_id': _resident_id(''), 'lineage_id': os.environ['PORTAL_LINEAGE_ID']}))"],
            cwd=Path(__file__).resolve().parents[2],
            env={**os.environ, **environment}, capture_output=True, text=True, check=True,
        )
        observed.append(json.loads(parsed.stdout))
        return mock.Mock(thread_id="019f0000-0000-7000-8000-000000000451",
                         turn_id="019f0000-0000-7000-8000-000000000452",
                         response="done", token_usage={})

    client.run_turn.side_effect = run_turn
    before_environment = dict(os.environ)
    for resident_id, lineage_id in (
        (resident_record["resident_id"], resident_record["lineage_id"]),
        ("resident_bbbbbbbbbbbb", "lineage_000000000002"),
    ):
        record = {**resident_record, "resident_id": resident_id, "lineage_id": lineage_id}
        request = resident_execution_domain._build_resident_execution_request(record, {
            "actor": "test-actor", "job_id": "job-" + resident_id, "message": "query",
        })
        request = prepare_invocation(BackendInvocation(request, SimpleNamespace(backend_route_id="one"), request.prompt, "", False, False)).request
        requests.append(request)
        assert "PORTAL_DATABASE_URL" not in json.dumps(request.to_payload())
        assert "PORTAL_DATABASE_URL" not in backend._runtime_thread_config(request).get("shell_environment_policy.set", {})
        backend.execute(
            BackendInvocation(
                request=request,
                session=RuntimeSession(
                    mode="persistent", binding_id=request.session.binding_id,
                    backend_session_id="019f0000-0000-7000-8000-000000000451",
                    backend_route_id="one", is_new_backend_session=False,
                ),
                user_prompt=request.prompt, system_context="",
                attach_system_context=False, is_new_backend_session=False,
            ),
            BackendContext(emit_event=lambda *a: None, cancellation_requested=lambda: False,
                           set_interrupt_target=lambda *a: None),
        )
    assert observed == [
        {"resident_id": request.agent_id, "lineage_id": request.metadata["lineage_id"]}
        for request in requests
    ]
    assert observed_database == [False] * len(requests)
    assert requests[0].runtime_options["config_overrides"]["shell_environment_policy.set"] == {
        "PORTAL_RESIDENT_ID": resident_record["resident_id"],
        "PORTAL_LINEAGE_ID": resident_record["lineage_id"],
    }
    assert client.run_turn.call_count == 2
    client.start_thread.assert_not_called()
    assert os.environ == before_environment


@pytest.mark.parametrize("backend,placement", [
    ("codex", "prompt_prefix"), ("dsh", "prompt_prefix"),
    ("claude", "system"), ("kimi", "system"),
])
def test_resident_md_reaches_every_provider_once(resident_record, monkeypatch, backend, placement):
    """resident.md 在每种 Provider 的第一轮恰好送达一次

    Resident 的 resident.md 在会话第一轮恰好送达一次：Codex、DSH 放在用户消息里，Claude Code、Kimi Code 随 Portal 系统指令发送；以 Resident 自己的工作区为工作目录时也不漏发。

    A Resident's resident.md reaches its model on the first turn, exactly once.

    Until 10-09 no Claude Code or Kimi Code session received it: their turn
    prompt leaves it out, and nothing put it in Portal's system instructions.
    Codex skipped it when the Resident's own workspace was the working
    directory, as if Codex read it there; Codex reads no project docs.

    :importance: P1
    :design: decision_ef1d6c925955
    """
    from management_portal.execution.injection_plan import plan_injection
    from management_portal.society_service.resident_record_domain import (
        resident_folder_path as _resident_folder_path,
    )

    record = {**resident_record, "backend": backend}
    folder = _resident_folder_path(record)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "resident.md").write_text("我负责网络管理。", encoding="utf-8")
    workspace = resident_record_domain.resident_workspace_path(record)
    monkeypatch.setattr(resident_runtime_domain, "resident_working_directory", lambda _: workspace)
    monkeypatch.setattr(resident_backend, "record_session_id", lambda _: "")
    job = {"actor": "sample_admin", "message": "检查出口", "context_refs": {}}

    prompt = resident_prompt_domain.build_resident_prompt(record, dict(job))
    parts = resident_execution_domain.resident_system_instructions(record, dict(job)).parts
    plan = plan_injection(parts, prompt, generation=True, first_injection=True)

    assert {part.name: part.placement for part in parts}["resident_md"] == placement
    assert (plan.system_context + plan.user_prompt).count("我负责网络管理。") == 1
    assert ("我负责网络管理。" in plan.system_context) is (placement == "system")
    assert "检查出口" in plan.user_prompt


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
