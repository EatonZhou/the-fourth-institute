"""Forking a Resident: creation and its execution work, copied Agent Data, inherited settings, account
selection and fork context.

:module: resident.identity
:importance: P2
"""
from __future__ import annotations
from tests.cases.resident_delivery_test_support import published_delivery as _published_delivery
from tests.cases.agent_data_test_support import register_session
from management_portal.society_service import resident_backend
from management_portal.society_service import resident_catalog
from management_portal.society_service import resident_conversation_stream
from management_portal.society_service.domain_ports import execution_port
from management_portal.execution.accounts import default_execution_accounts
from management_portal.execution import resident_runtime_settings, codex_runtime_settings

import threading
import traceback
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import pytest

pytestmark = pytest.mark.usefixtures("registered_execution_defaults")

from management_portal.society_service import (
    lineage_resident_nodes,
    resident_hatg_credentials,
    resident_access,
    resident_execution_domain,
    resident_fork_domain,
    resident_execution_work,
    resident_lineage,
    resident_protocol,
    resident_record_domain,
    resident_runtime_domain,
    resident_session_backend_domain,
    resident_prompt_domain,
)
from management_portal.society_service import lifecycle
from management_portal.society_service import resident_domain as backend
from management_portal.society_service.resident_execution_work import bind_execution
from management_portal.execution.agent_data_store import get_agent_data_store
from tests.cases.resident_execution_test_support import _resident_test_codex_registry
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401 - fixture


def _add_created_resident_to_lineage(
    record: dict[str, object],
    created_resident_id: str,
    actor: str = "CASE-A",
) -> None:
    created_resident_record = resident_catalog.get_catalog().get(created_resident_id)
    created_resident_record["workspace_path"] = record["workspace_path"]
    updated = lineage_resident_nodes.add_registered_resident_node(
        created_resident_record,
        actor,
    )
    assert updated is not None


def test_exact_resident_creation_job_is_applied_to_its_own_execution_work(
    resident_record: dict[str, object],
) -> None:
    """派生 Resident 创建完成仅应用于其自身执行工作"""
    from tests.cases.fork_context_test_support import record_session_turn
    record_session_turn(resident_record)
    detail = backend.create_resident_full_context_fork(
        str(resident_record["resident_id"]),
        "CASE-A",
        defer_backend_start=True,
    )
    created_resident_id = str(detail["resident_id"])
    _add_created_resident_to_lineage(resident_record, created_resident_id)
    exact_record = resident_record_domain.find_resident(created_resident_id)
    assert exact_record is not None
    fork_mode = exact_record["fork_mode"]
    forked_from_at = exact_record["forked_from_at"]
    job = {
        'execution_id': "execution-" + "3" * 24,
        'operation': "context_fork_create",
        "resident_id": created_resident_id,
        "actor": "CASE-A",
        "queue": [],
        **resident_execution_work.continuation_fields(exact_record),
    }
    continuation_id = str(job["continuation_id"])
    resident_execution_work.write(exact_record, job)

    created_session_id = "01a00000-0000-7000-8000-000000000199"

    def create_exact(record, current_job):
        bind_execution(
            current_job,
            execution_id="execution-" + "3" * 24,


            now="2026-08-27T00:00:00Z",
        )
        resident_execution_work.write(record, current_job)
        return {
            "id": created_session_id,
            "backend_route_id": "zero",
        }

    with (
        mock.patch.object(
            resident_session_backend_domain,
            "create_exact_resident_session",
            side_effect=create_exact,
        ) as create_session,
        mock.patch.object(
            resident_execution_domain,
            "_finalize_applied_execution",
            return_value=False,
        ) as finalize,
    ):
        resident_execution_domain.run_resident_execution(created_resident_id, continuation_id)

    create_session.assert_called_once()
    finalize.assert_called_once()
    persisted = resident_record_domain.find_resident(created_resident_id)
    assert persisted is not None
    assert persisted["session_state"] == "active"
    assert persisted["fork_mode"] == fork_mode == "full"
    assert persisted["forked_from_at"] == forked_from_at
    assert persisted["session_created_via"] == "execution_service"
    assert "native_backend_fork" not in persisted
    first_turn = resident_execution_domain._portable_context_execution(
        persisted,
        {"actor": "CASE-A", "message": "continue"},
    )
    assert first_turn["fork_context_input"]["segments"] == [
        {"segment_id": item["segment_id"], "provider_turn_id": item.get("provider_turn_id", "")}
        for item in get_agent_data_store().agent_context_snapshot(created_resident_id)["segments"]
    ]
    assert resident_execution_work.completion_applied(resident_execution_work.read(persisted))


def test_exact_resident_native_fork_uses_cross_lineage_source_binding(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """跨 Lineage 分叉先复制选定数据再创建目标绑定会话"""
    source_id = str(resident_record["resident_id"])
    target_id = "resident_bbbbbbbbbbbb"
    target = {
        **resident_record,
        "resident_id": target_id,
        "lineage_id": "lineage_bbbbbbbbbbbb",
        "source_resident_id": source_id,
        "backend_route_id": "zero",
    }
    owned_workspace = resident_record_domain.resident_owned_workspace_path(target, str(target["resident_id"]))
    owned_workspace.mkdir(parents=True, exist_ok=True)
    target["owned_workspace_id"] = "workspace_bbbbbbbbbbbb"
    target["default_cwd"] = str(owned_workspace)
    target["owned_workspace"] = {
        "workspace_id": target["owned_workspace_id"],
        "path": str(owned_workspace),
    }
    job = {
        'execution_id': "execution-" + "7" * 24,
        'operation': "context_fork_create",
        "actor": "CASE-A",
        "queue": [],
        "agent_data_fork": {
            "source_agent_id": source_id,
            "target_agent_id": target_id,
            "segment_ids": ["segment_chosen"],
        },
    }
    monkeypatch.setattr(
        resident_record_domain,
        "find_resident",
        lambda resident_id: (
            resident_record if resident_id == source_id else None
        ),
    )
    monkeypatch.setattr(
        resident_access,
        "has_resident_workspace_authority",
        lambda *_args, **_kwargs: True,
    )
    captured: dict[str, object] = {}
    operations: list[str] = []
    monkeypatch.setattr(
        execution_port,
        "fork_agent_data",
        lambda *_args, **_kwargs: operations.append("agent_data"),
    )
    monkeypatch.setattr(
        "management_portal.society_service.changes.resident_changed",
        lambda *_args, **_kwargs: None,
    )

    def create_between_bindings(**kwargs):
        operations.append("provider")
        captured.update(kwargs)
        return {
            "backend_session_id": "01a00000-0000-7000-8000-000000000299",
            "forkedFromId": resident_backend.record_session_id(
                resident_record
            ),
            "backend_route_id": "zero",
        }

    monkeypatch.setattr(
        resident_session_backend_domain,
        "run_backend_session_operation",
        create_between_bindings,
    )

    result = resident_session_backend_domain.create_exact_resident_session(target, job)

    assert result["id"] == "01a00000-0000-7000-8000-000000000299"
    assert "source_binding_id" not in captured
    assert captured["operation"] == "create"
    assert captured["binding_id"] == f"resident:{target_id}"
    assert captured["resident_record"] is target
    assert captured["execution_work"] is job
    assert captured["backend_route_id"] == "zero"
    assert operations == ["agent_data", "provider"]


def test_queued_agent_data_fork_replays_authoritative_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """排队的数据分叉按权威选段重新物化并发布变化"""
    target_id = "resident_bbbbbbbbbbbb"
    copied: list[tuple[tuple[object, ...], dict[str, object]]] = []
    changed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        execution_port,
        "fork_agent_data",
        lambda *args, **kwargs: copied.append((args, kwargs)),
    )
    monkeypatch.setattr(
        "management_portal.society_service.changes.resident_changed",
        lambda resident_id, *, scope: changed.append((resident_id, scope)),
    )
    job = {
        "actor": "CASE-A",
        "agent_data_fork": {
            "source_agent_id": "resident_aaaaaaaaaaaa",
            "target_agent_id": target_id,
            "segment_ids": ["segment_selected_1", "segment_selected_2"],
        },
    }

    resident_session_backend_domain._materialize_queued_agent_data_fork(
        {"resident_id": target_id},
        job,
    )

    assert copied == [(('resident_aaaaaaaaaaaa', target_id), {
        "actor": "CASE-A",
        "segment_ids": ["segment_selected_1", "segment_selected_2"],
    })]
    assert changed == [(target_id, "conversation")]

    resident_session_backend_domain._materialize_queued_agent_data_fork(
        {"resident_id": target_id},
        job,
    )
    assert len(copied) == 2
    assert changed == [
        (target_id, "conversation"),
        (target_id, "conversation"),
    ]


def test_fork_operation_commits_created_resident_before_publishing_work(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分叉先提交新 Resident 再发布执行工作"""
    from tests.cases.fork_context_test_support import record_session_turn
    selected_ids = record_session_turn(resident_record)
    root_id = str(resident_record["resident_id"])
    notifications: list[str] = []

    def assert_committed_before_wake(resident_id: str) -> None:
        target_id = str(resident_id)
        notifications.append(target_id)
        catalog_record = resident_catalog.get_catalog().get(target_id)
        assert catalog_record["resident_id"] == target_id
        root = resident_record_domain.find_resident(root_id)
        assert root is not None
        family = resident_lineage.snapshot(root)
        assert resident_lineage.find_in_snapshot(family, target_id) is not None
        job = resident_execution_work.read(catalog_record)
        assert "status" not in job
        assert "queue_reason" not in job
        assert job["agent_data_fork"] == {
            "source_agent_id": root_id,
            "target_agent_id": target_id,
            # Only the turn input and answer are trace content.
            "segment_ids": selected_ids,
        }

    monkeypatch.setattr(
        lifecycle,
        "notify_execution_binding",
        assert_committed_before_wake,
    )

    created = backend.create_resident_full_context_fork(
        root_id,
        "CASE-A",
    )

    assert notifications == [created["resident_id"]]


def test_fork_creation_persists_and_projects_inherited_runtime_settings(
    resident_record: dict[str, object],
) -> None:
    """分叉保存并投影继承的模型、推理强度和服务等级"""
    from tests.cases.fork_context_test_support import record_session_turn

    inherited = resident_runtime_settings.mark_portal_manual_authority(
        resident_runtime_settings.merge_settings(
            resident_runtime_domain.read_resident_runtime_settings(resident_record),
            {
                "model": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
                "service_tier": "priority",
            },
            actor="CASE-A",
            updated_at="2026-09-20T09:09:01Z",
        ),
        actor="CASE-A",
        updated_at="2026-09-20T09:09:01Z",
    )
    resident_runtime_domain.write_resident_runtime_settings(resident_record, inherited)
    resident_record_domain.save_resident_record(resident_record)
    record_session_turn(resident_record)

    created = backend.create_resident_full_context_fork(
        str(resident_record["resident_id"]),
        "CASE-A",
        defer_backend_start=True,
    )
    child = resident_catalog.get_catalog().get(created["resident_id"])
    persisted = resident_runtime_domain.read_resident_runtime_settings(child)
    assert persisted["model"] == "gpt-5.6-sol"
    assert persisted["reasoning_effort"] == "xhigh"
    assert persisted["service_tier"] == "priority"
    projected = resident_conversation_stream.get_repository().current_projection(
        created["resident_id"],
    )
    assert projected["runtime_settings"]["model"] == "gpt-5.6-sol"
    assert projected["runtime_settings"]["reasoning_effort"] == "xhigh"
    assert projected["runtime_settings"]["service_tier"] == "priority"


def test_fork_creation_does_not_enter_shared_registry_or_family_locks(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分叉创建避开共享注册表和聚合状态锁"""
    @contextmanager
    def forbidden_family_lock(_record):
        raise AssertionError("fork creation entered a family write lock")
        yield

    @contextmanager
    def forbidden_state_lock():
        raise AssertionError("fork creation entered the aggregate state lock")
        yield

    monkeypatch.setattr(resident_record_domain, "state_lock", forbidden_state_lock)
    monkeypatch.setattr(
        resident_hatg_credentials,
        "ensure_resident_credential",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("fork creation entered the credential registry lock")
        ),
    )

    from tests.cases.fork_context_test_support import record_session_turn
    record_session_turn(resident_record)
    created = backend.create_resident_full_context_fork(
        str(resident_record["resident_id"]),
        "CASE-A",
        defer_backend_start=True,
    )

    assert created["schema"] == "tfi.resident-creation-result.v1"
    assert resident_catalog.get_catalog().get(created["resident_id"])[
        "resident_id"
    ] == created["resident_id"]


def test_concurrent_fork_creation_commits_distinct_residents_without_family_merge(
    resident_record: dict[str, object],
) -> None:
    """并发分叉提交不同 Resident 且两者均保留在所属图中"""
    from tests.cases.fork_context_test_support import record_session_turn
    record_session_turn(resident_record)
    root_id = str(resident_record["resident_id"])
    barrier = threading.Barrier(3)
    created: list[dict[str, object]] = []
    errors: list[str] = []

    def create() -> None:
        try:
            barrier.wait(timeout=2)
            created.append(backend.create_resident_full_context_fork(
                root_id,
                "CASE-A",
                defer_backend_start=True,
            ))
        except BaseException:  # pragma: no cover - asserted below
            errors.append(traceback.format_exc())

    threads = [threading.Thread(target=create) for _index in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=5)

    assert not errors, "\n\n".join(errors)
    assert len(created) == 2
    resident_ids = {str(item["resident_id"]) for item in created}
    assert len(resident_ids) == 2
    family = resident_lineage.snapshot(
        resident_record_domain.find_resident(root_id)
    )
    assert resident_ids.issubset({
        str(item.get("resident_id") or "")
        for item in family["residents"]
    })


def test_send_now_into_a_forks_first_turn_does_not_repeat_its_inherited_context(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """立即发送进 Fork 首轮时不重复继承的上下文

    Fork 首轮运行期间排队的消息按当时状态带着继承的历史构建；立即发送进正在运行的这一轮时，只发送消息本身，不再附带继承的历史，也不会因此超过输入长度上限。

    An input queued while a Fork's first turn runs was built with the
    inherited context; Send now gives the running turn only the message.

    :module: resident.fork-handoff
    :importance: P1
    :design: decision_0f57656a2acd
    """
    from management_portal.society_service.resident_record_domain import (
        save_resident_record as _save_resident_record,
    )

    store = get_agent_data_store()
    sid = store.put_segment(segment_type="model", content="source question", created_by_agent_id="resident_bbbbbbbbbbbb")
    store.append_agent_segments(resident_record["resident_id"], [sid], actor_ref="CASE-A")
    resident_id = str(resident_record["resident_id"])
    resident_record.update({
        "fork_mode": "recent-5",
        "source_resident_id": "resident_bbbbbbbbbbbb",
    })
    _save_resident_record(resident_record)
    queued_message_id = "msg_cccccccccccccccccccccccccccccccc"
    queued_prompt = resident_prompt_domain.build_resident_prompt(
        resident_record, {"actor": "CASE-A", "message": "补充检查移动端"},
    )
    prepared = resident_execution_domain._portable_context_execution(
        resident_record, {"actor": "CASE-A", "raw_message": "补充检查移动端"})
    assert "source question" in prepared["fork_context_input"]["portable_prompt"]
    resident_execution_work.write(resident_record, {

        "execution_id": "execution-333333333333333333333333",
        "actor": "CASE-A",
        "queue": [{
            'execution_id': "job_queued_input",
            "user_message_id": queued_message_id,
            "input_message": {
                "message_id": queued_message_id, "role": "user", "author_id": "CASE-A",
                "content_parts": [{"type": "text", "text": "补充检查移动端"}],
                "created_at": "2026-10-10T00:00:00Z",
            },
            "actor": "CASE-A",
            "raw_message": "补充检查移动端",
            "effective_message": "补充检查移动端",
            "message": queued_prompt,
            "actual_sent_prompt": queued_prompt,
            "fork_context_input": prepared["fork_context_input"],
        }],
     "submitted_at": "2026-10-14T00:00:00Z"})
    sent: list[list[dict[str, object]]] = []

    class Service:
        def status(self, *_args, **_kwargs):
            return {"status": "running"}

        def append_input(self, _execution_id, *, input, **_kwargs):
            sent.append(input)
            return {"control_id": "control-333333333333333333333333", "status": "delivered",
                    "expected_turn_id": "turn-active"}

        def input_control(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(execution_port, "service", lambda: Service())
    _published_delivery(resident_protocol.inject_queued_input_into_active_turn(resident_id, "CASE-A", queued_message_id))

    (text_part,) = sent[0]
    assert "补充检查移动端" in text_part["text"]
    assert "source question" not in text_part["text"]
    assert "<resident_trace_context>" not in text_part["text"]


def test_native_fork_keeps_session_phase_without_creating_an_input(resident_record):
    """原生分叉保留会话启动阶段且不创建输入消息"""
    job = { 'operation': "context_fork_create",
           "execution_id": "execution-" + "b" * 24, "submitted_at": "2026-10-14T00:00:00Z"}
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    resident_execution_work.publish_conversation_state(resident_record, job, snapshot={"status": "running"})
    state = repository.published_state(resident_id)
    assert state["work_state"] == "running"
    assert state["work_phase"] == "starting_session"
    assert state["active_message_id"] == ""
    assert state["queued_count"] == 0
    assert resident_execution_work.conversation_state(
        {**job, "user_message_id": "real-input"}, intake_state="open", snapshot={"status": "running"}
    ).get("work_phase") is None
    resident_execution_work.publish_conversation_state(
        resident_record, {**job, "outcome": "completed", "finished_at": "2026-10-13T10:00:00Z"},
        snapshot={"status": "completed"},
    )
    assert repository.published_state(resident_id) == {
        "work_state": "idle", "active_message_id": "", "queued_count": 0, "intake_state": "open",
    }


def test_fork_account_selection_defaults_to_source_session_account(
    resident_record: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分叉账号默认使用来源会话账号且可显式选择目标账号"""
    registry, _zero_home, _one_home = _resident_test_codex_registry(
        tmp_path, monkeypatch,
    )
    source_session_id = resident_backend.record_session_id(
        resident_record
    )
    register_session(
        resident_record,
        source_session_id,
        backend_route_id="zero",
    )
    monkeypatch.setattr(
        resident_runtime_domain, "default_execution_accounts",
        lambda: default_execution_accounts(account_registry=registry),
    )

    selection = backend.account_selection_payload(
        resident_record,
        resident_runtime_domain.resident_session_account_id(resident_record),
    )
    derived_resident = {
        "resident_id": "resident_bbbbbbbbbbbb",
        "backend": "codex",
        "source_resident_id": str(resident_record["resident_id"]),
    }
    selected = resident_runtime_domain.assign_derived_resident_account_route(
        resident_record,
        derived_resident,
        resident_record,
        requested_account_id="one",
    )

    assert selection == {
        "enabled": True,
        "account_ids": ["codex_zero", "codex_one"],
        "source_account_id": "codex_zero",
        "default_account_id": "codex_zero",
        "copy_context_when_changed": True,
    }
    assert selected == "one"
    assert derived_resident["backend_route_id"] == "one"
    assert "source_backend_route_id" not in derived_resident
    assert "codex_context_copy_required" not in derived_resident


def test_fork_account_selection_reconciles_the_inherited_provider_tuple(
    resident_record: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分叉切换账号时按目标能力修正继承的运行设置"""
    registry, _zero_home, _one_home = _resident_test_codex_registry(tmp_path, monkeypatch)
    register_session(
        resident_record,
        resident_backend.record_session_id(resident_record),
        backend_route_id="zero",
    )
    monkeypatch.setattr(
        resident_runtime_domain, "default_execution_accounts",
        lambda: default_execution_accounts(account_registry=registry),
    )
    monkeypatch.setattr(
        codex_runtime_settings,
        "codex_runtime_catalog",
        lambda *, account_id: {
            "models": ["target-model"],
            "default_model": "target-model",
            "efforts_by_model": {"target-model": ["low"]},
            "default_efforts_by_model": {"target-model": "low"},
            "service_tiers_by_model": {"target-model": []},
        },
    )
    derived_resident = {
        "resident_id": "resident_bbbbbbbbbbbb",
        "source_resident_id": str(resident_record["resident_id"]),
        "backend": "codex",
        "execution_settings": {
            "model": "source-only-model",
            "reasoning_effort": "xhigh",
            "service_tier": "priority",
        },
        "harness_config": {},
    }

    selected = resident_runtime_domain.assign_derived_resident_account_route(
        resident_record,
        derived_resident,
        resident_record,
        requested_account_id="one",
    )

    assert selected == "one"
    assert {
        field: derived_resident["execution_settings"][field]
        for field in ("model", "reasoning_effort", "service_tier")
    } == {
        "model": "target-model",
        "reasoning_effort": "low",
        "service_tier": "",
    }


def test_full_context_fork_stays_on_source_account(
    resident_record: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """完整上下文分叉默认在来源账号创建目标会话

    :module: resident.fork-handoff"""
    registry, zero_home, _one_home = _resident_test_codex_registry(
        tmp_path, monkeypatch,
    )
    source_session_id = resident_backend.record_session_id(
        resident_record
    )
    created_session_id = "019f0000-0000-7000-8000-000000000099"
    source_rollout = (
        zero_home
        / "sessions"
        / "2026"
        / "07"
        / "28"
        / f"rollout-2026-07-28T00-00-00-{source_session_id}.jsonl"
    )
    source_rollout.parent.mkdir(parents=True)
    source_rollout.write_text('{"type":"session_meta"}\n', encoding="utf-8")
    monkeypatch.setattr(resident_runtime_domain, "default_execution_accounts",
                        lambda: default_execution_accounts(account_registry=registry))
    from tests.cases.fork_context_test_support import record_session_turn
    record_session_turn(resident_record)
    created = backend.create_resident_full_context_fork(
        str(resident_record["resident_id"]),
        "CASE-A",
        defer_backend_start=True,
    )
    target_id = str(created["resident_id"])
    target = resident_record_domain.find_resident(target_id)
    assert target is not None

    def run_operation(**kwargs):
        assert kwargs["backend_route_id"] == "zero"
        assert kwargs['operation'] == 'create'
        assert kwargs["binding_id"] == f"resident:{target_id}"
        return {
            "backend_session_id": created_session_id,
            "backend_route_id": "zero",
        }

    monkeypatch.setattr(
        resident_session_backend_domain,
        "run_backend_session_operation",
        run_operation,
    )
    creation_job = {
        'execution_id': "job_native_source_account",
        'operation': "context_fork_create",
        "actor": "CASE-A",
    }
    resident_execution_work.write(target, creation_job)
    result = resident_session_backend_domain.create_exact_resident_session(target, creation_job)

    assert result['id'] == created_session_id
    assert result['backend_route_id'] == 'zero'
    assert get_agent_data_store().agent_context_snapshot(target_id)['segments']
    assert source_rollout.is_file()


def test_cross_account_codex_fork_uses_portable_context_seed(resident_record, monkeypatch):
    """跨账号 codex 分叉通过可迁移上下文传递选定历史"""
    from tests.cases.fork_context_test_support import record_session_turn
    selected_ids = record_session_turn(resident_record)
    created = backend.create_resident_full_context_fork(
        resident_record["resident_id"], "CASE-A", backend_route_id="one", defer_backend_start=True,
    )
    child = resident_record_domain.find_resident(created["resident_id"])
    assert child["backend_route_id"] == "one"
    assert child["fork_mode"] == "full"
    snapshot = get_agent_data_store().agent_context_snapshot(child["resident_id"])
    assert [item["segment_id"] for item in snapshot["segments"]] == selected_ids
    prepared = resident_execution_domain._portable_context_execution(child, {"actor": "CASE-A", "raw_message": "continue"})
    assert 'source question' in prepared['fork_context_input']['portable_prompt']
    assert 'source answer' in prepared['fork_context_input']['portable_prompt']
    assert 'source question' not in prepared['actual_sent_prompt']


@pytest.mark.parametrize("fork_mode", ["full", "message", "recent-5", "recent-10"])
def test_portal_fork_context_is_injected_until_provider_accepts_input(resident_record, fork_mode):
    """Portal 分叉上下文在 Provider 接受输入后停止注入

    :module: resident.conversation
    :aspects: functional, performance
    """
    from tests.cases.test_resident_fork_context_delivery import _accept_child_context
    record = dict(resident_record, fork_mode=fork_mode)
    store = get_agent_data_store()
    sid = store.put_segment(segment_type="model", content="source answer", created_by_agent_id="resident_bbbbbbbbbbbb")
    store.append_agent_segments(record["resident_id"], [sid], actor_ref="CASE-A")
    queued = {"actor": "CASE-A", "raw_message": "continue"}
    first = resident_execution_domain._portable_context_execution(record, queued)
    assert 'source answer' in first['fork_context_input']['portable_prompt']
    assert 'continue' in first['actual_sent_prompt']
    _accept_child_context(record)
    later = resident_execution_domain._portable_context_execution(record, first)
    assert 'fork_context_input' not in later
    assert 'source answer' not in later['actual_sent_prompt']


def test_fork_intake_state_uses_protocol_state_and_holds_promoted_queue_item(
    resident_record: dict[str, object],
) -> None:
    """分叉 Resident 的接收状态以协议状态为准"""
    created_resident_id = "resident_bbbbbbbbbbbb"
    registry = resident_lineage.snapshot(resident_record)
    registry["residents"].append({
        "resident_id": created_resident_id,
        "source_resident_id": resident_record["resident_id"],
        "lineage_id": resident_record["lineage_id"],
        "created_by": "CASE-A",
        "backend": "codex",
    })
    resident_fork_domain._persist_derived_resident(resident_record, registry["residents"][-1])

    response = resident_protocol.change_resident_state(
        created_resident_id,
        "CASE-A",
        state_domain="intake",
        transition="close",
    )

    updated_registry = resident_lineage.snapshot(resident_record)
    derived_resident = resident_lineage.find_in_snapshot(updated_registry, created_resident_id)
    assert response["intake_state"] == "closed"
    assert derived_resident is not None
    assert "intake_state" not in derived_resident
    assert resident_protocol.get_resident_state(created_resident_id, "CASE-A")["state"][
        "intake_state"
    ] == "closed"
    exact_resident = resident_record_domain.find_resident(created_resident_id)
    assert exact_resident is not None
