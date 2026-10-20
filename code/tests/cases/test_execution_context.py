from __future__ import annotations

from pathlib import Path
from functools import partial
import shutil
import sys

import pytest

from management_portal.agent_prompt_context import (
    portal_system_instructions,
    portal_turn_instructions,
)
from management_portal import agent_prompt_context
from management_portal.execution.backends import BackendResult
from management_portal.execution.contracts import (
    ExecutionRequest,
    SessionOptions,
)
from management_portal.execution.runtime import ExecutionRuntime


@pytest.fixture(autouse=True)
def skill_root(tmp_path, monkeypatch):
    root = tmp_path / "Skills"
    root.mkdir()
    shutil.copytree(Path(__file__).resolve().parents[2] / "core_skills/tfi-agent-rule", root / "tfi-agent-rule")
    extra = root / "manage-knowledge-base/SKILL.md"
    extra.parent.mkdir()
    extra.write_text("---\nname: manage-knowledge-base\ndescription: Test fixture only\n---\n# Task rules\n")
    for name in ("portal_system_instructions", "portal_turn_instructions"):
        monkeypatch.setattr(sys.modules[__name__], name,
                            partial(getattr(agent_prompt_context, name), skill_root=str(root)))
    return root


ROLE_FILES = {
    "development": "portal-development.md",
    "resident": "portal-resident.md",
}


def _request(
    prompt: str,
    *,
    binding_id: str = "",
    backend_session_id: str = "",
    injection_policy: str = "session_start",
) -> ExecutionRequest:
    return ExecutionRequest.new(
        actor="sample_admin",
        prompt=prompt,
        service_type="agent_session",
        provider="codex",
        operation="turn",
        session=SessionOptions(
            mode="persistent" if binding_id else "stateless",
            binding_id=binding_id,
        ),
        system_instructions=portal_system_instructions(
            "resident",
            injection_policy=injection_policy,
        ),
    )


def test_portal_materializes_one_entry_and_registered_role_contracts(subtests, isolated_portal_case, skill_root):
    """Portal 系统指令内联角色规则和附加 Skill，并按注入策略标明 session 启动或本轮刷新"""
    with subtests.test(contract='test_portal_materializes_one_entry_and_registered_role'), isolated_portal_case() as case_path:
        instructions = portal_system_instructions(
            "resident",
            additional_skills=("manage-knowledge-base",),
        )

        assert '<portal-materialized-skill-set role="resident"' in (
            instructions.parts[0].content
        )
        assert (
            f'<portal-materialized-skill path="{skill_root}/tfi-agent-rule/'
            'SKILL.md" sha256="'
        ) in instructions.parts[0].content
        assert (
            f'<portal-materialized-skill path="{skill_root}/tfi-agent-rule/'
            'portal-resident.md" sha256="'
        ) in instructions.parts[0].content
        assert (
            '<portal-system-prompt role="resident">'
            in instructions.parts[0].content
        )
        assert (
            f"{skill_root}/manage-knowledge-base/SKILL.md"
            in instructions.parts[0].content
        )
        assert instructions.parts[0].timing == "session_start"
        assert "这是当前 Provider session 的启动指令，在整个 session 中有效。" in instructions.parts[0].content
        assert "Portal 不在每个 turn 刷新这些核心规则" in instructions.parts[0].content
        assert "不要重复读取这些 Skill" in instructions.parts[0].content
        assert "Before acting" not in instructions.parts[0].content
    with subtests.test(contract='test_portal_every_turn_instructions_explicitly_request_a_refresh'), isolated_portal_case() as case_path:
        instructions = portal_system_instructions(
            "resident",
            injection_policy="every_turn",
        )

        assert instructions.parts[0].timing == "every_turn"
        assert "这是 Portal 为当前 turn 明确发送的规则刷新指令。" in instructions.parts[0].content
        assert "请在本轮继续遵循" in instructions.parts[0].content
        assert "启动指令，在整个 session 中有效" not in instructions.parts[0].content
    with subtests.test(contract='test_portal_context_builder_rejects_unknown_roles'), isolated_portal_case() as case_path:
        with pytest.raises(ValueError, match="unknown Portal agent role"):
            portal_system_instructions("resident-v1")
    with subtests.test(contract='test_all_portal_roles_have_flat_rule_files'), isolated_portal_case() as case_path:
        role_root = skill_root / "tfi-agent-rule"

        for filename in ROLE_FILES.values():
            assert (role_root / filename).is_file()
        assert not list(role_root.glob("references"))
        assert not list(role_root.glob("agents"))


def test_portal_resolves_additional_skill_resource_id(
    monkeypatch: pytest.MonkeyPatch, skill_root,
) -> None:
    """以资源 ID 指定的附加 Skill 按调用者解析为实际 SKILL.md 路径"""
    observed: list[tuple[str, str]] = []

    def resolve(skill_id: str, *, actor: str) -> str:
        observed.append((skill_id, actor))
        return f"{skill_root}/manage-knowledge-base/SKILL.md"

    monkeypatch.setattr(
        agent_prompt_context,
        "_resolve_skill_resource_path",
        resolve,
    )

    instructions = portal_system_instructions(
        "general",
        additional_skills=("skill_577f37784184",),
        actor="archive-agent",
    )

    assert observed == [("skill_577f37784184", "archive-agent")]
    assert f"{skill_root}/manage-knowledge-base/SKILL.md" in instructions.parts[0].content
    assert f"{skill_root}/skill_577f37784184/SKILL.md" not in instructions.parts[0].content


@pytest.mark.parametrize("skill", ["../outside", "/tmp/outside/SKILL.md", "name/child", "UPPERCASE"])
def test_prompt_skill_paths_reject_inputs_outside_resident_rules(skill):
    """附加 Skill 名含路径穿越、绝对路径、子目录或大写字母时被拒绝

    :design: decision_ef1d6c925955
    :design-point:
    """
    with pytest.raises(ValueError):
        portal_turn_instructions(additional_skills=(skill,))


def test_skill_resource_resolution_cannot_escape_the_shared_skill_root(monkeypatch, tmp_path):
    """Skill 资源 ID 解析到共享 Skill 根目录之外时被拒绝"""
    skill = tmp_path / "SKILL.md"
    skill.write_text("outside rules")
    monkeypatch.setattr(agent_prompt_context, "_resolve_skill_resource_path", lambda *_args, **_kwargs: str(skill))
    with pytest.raises(ValueError, match="必须位于"):
        portal_turn_instructions(additional_skills=("skill_577f37784184",), actor="alice")


def test_named_skill_symlink_cannot_escape_the_shared_skill_root(tmp_path):
    """按名称引用的 Skill 软链接指向共享 Skill 根目录之外时被拒绝"""
    from management_common.skill_paths import normalize_additional_skills
    root = tmp_path / "skills"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("outside")
    (root / "escaped").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="必须位于"):
        normalize_additional_skills(["escaped"], skill_root=str(root))


def test_portal_turn_instructions_are_only_task_deltas(skill_root) -> None:
    """每轮指令只携带本轮附加的 Skill，不含 Portal 核心规则，无附加时为空"""
    assert portal_turn_instructions() is None

    instructions = portal_turn_instructions(
        additional_skills=("manage-knowledge-base",),
    )

    assert instructions is not None
    assert instructions.parts[0].timing == "every_turn"
    assert "<portal-turn-prompt>" in instructions.parts[0].content
    assert f"{skill_root}/manage-knowledge-base/SKILL.md" in instructions.parts[0].content
    assert "<portal-system-prompt" not in instructions.parts[0].content
    assert "tfi-agent-rule" not in instructions.parts[0].content


def test_runtime_transports_opaque_instructions_once_per_binding(
    tmp_path: Path,
) -> None:
    """持久会话的系统指令每个绑定只附带一次，后续轮次不再重复"""
    invocations = []

    class RecordingBackend:
        provider_id = "codex"

        def execute(self, invocation, _context):
            invocations.append(invocation)
            return BackendResult(
                output={"message": invocation.user_prompt},
                backend_session_id=(
                    invocation.session.backend_session_id or "session-1"
                ),
            )

        def interrupt(self, **_values):
            return True

    runtime = ExecutionRuntime(
        agent_providers={"codex": RecordingBackend()},
        state_root=tmp_path,
    )
    binding_id = "resident:resident_000000000001"
    runtime.execute(_request("first", binding_id=binding_id))
    runtime.execute(
        _request(
            "second",
            binding_id=binding_id,
            backend_session_id="session-1",
        )
    )

    assert [item.attach_system_context for item in invocations] == [
        True,
        False,
    ]
    binding = runtime.sessions.provider_binding(binding_id)
    assert binding["context_injection_count"] == 1
    assert "context_role" not in binding


def test_create_then_first_turn_injects_session_start_instructions(
    tmp_path: Path,
) -> None:
    """codex 会话 create 时不带启动指令，第一个可见轮次才注入且只注入一次"""
    from types import SimpleNamespace
    from unittest.mock import Mock
    from management_portal.execution.backends import CodexBackend

    attached: list[tuple[str, bool]] = []
    client = Mock()
    client.start_thread.return_value = {"thread": {"id": "019f0000-0000-7000-8000-000000000411"}}
    accounts = Mock()
    accounts.candidates.return_value = [SimpleNamespace(account_id="one", socket_path=tmp_path / "codex.sock")]
    backend = CodexBackend(account_registry=accounts, client_factory=lambda path: client)

    class RecordingBackend:
        provider_id = "codex"

        def execute(self, invocation, _context):
            if invocation.request.operation == "create":
                backend._resolve_client(invocation, _context)
                assert not client.start_thread.call_args.args[0].get("developerInstructions")
            attached.append(
                (
                    invocation.request.operation,
                    invocation.attach_system_context,
                )
            )
            return BackendResult(
                output={"message": invocation.user_prompt},
                backend_session_id="session-created-first",
            )

        def interrupt(self, **_values):
            return True

    runtime = ExecutionRuntime(
        agent_providers={"codex": RecordingBackend()},
        state_root=tmp_path,
    )
    binding_id = "resident:resident_000000000002"
    runtime.execute(
        ExecutionRequest.new(
            actor="sample_admin",
            prompt="create",
            service_type="agent_session",
            provider="codex",
            operation="create",
            session=SessionOptions(mode="persistent", binding_id=binding_id),
            system_instructions=portal_system_instructions("resident"),
        )
    )
    runtime.execute(
        _request(
            "first visible turn",
            binding_id=binding_id,
            backend_session_id="session-created-first",
        )
    )

    assert attached == [("create", False), ("turn", True)]
    binding = runtime.sessions.provider_binding(binding_id)
    assert binding["context_injection_count"] == 1


def test_runtime_every_turn_and_stateless_instruction_lifecycle(
    tmp_path: Path,
) -> None:
    """every_turn 策略每轮都附带系统指令，无状态执行每次附带且各用独立会话"""
    attached = []

    class RecordingBackend:
        provider_id = "codex"

        def execute(self, invocation, _context):
            attached.append(invocation.attach_system_context)
            return BackendResult(
                output={"message": invocation.user_prompt},
                backend_session_id=(
                    invocation.session.backend_session_id or "session-2"
                ),
            )

        def interrupt(self, **_values):
            return True

    runtime = ExecutionRuntime(
        agent_providers={"codex": RecordingBackend()},
        state_root=tmp_path,
    )
    binding_id = "resident:resident_000000000003"
    runtime.execute(
        _request(
            "first",
            binding_id=binding_id,
            injection_policy="every_turn",
        )
    )
    runtime.execute(
        _request(
            "second",
            binding_id=binding_id,
            backend_session_id="session-2",
            injection_policy="every_turn",
        )
    )
    stateless_one = _request("stateless")
    stateless_two = _request("stateless again")
    runtime.execute(stateless_one)
    runtime.execute(stateless_two)

    assert attached == [True, True, True, True]
    with runtime.agent_data_projector.store.database.read() as connection:
        session_ids = {
            str(row["backend_session_id"])
            for row in connection.execute(
                "SELECT backend_session_id FROM provider_sessions"
            )
        }
    assert stateless_one.execution_id in session_ids
    assert stateless_two.execution_id in session_ids
