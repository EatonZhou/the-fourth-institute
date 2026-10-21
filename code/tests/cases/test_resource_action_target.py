"""RunResourceAction finds a resource the search index has not caught up with.

:importance: P1
"""
from __future__ import annotations

from types import SimpleNamespace

from management_portal.hatg import catalog
from management_portal.hatg.contracts import InstructionCall, InstructionContext
from management_portal.hatg.kernel import Invocation
from management_portal.resources import isa as resource_isa

WORKBENCH = "workbench_0123456789ab"


def _invoke(arguments: dict) -> Invocation:
    declaration = catalog.declaration("RunResourceAction")
    declaration.validate_arguments(arguments)
    return Invocation(declaration, InstructionCall(instruction="RunResourceAction", arguments=arguments),
                      InstructionContext(caller="resident_0123456789ab", entry="harness"), "inst_res_00000000000000000000")


def test_an_action_runs_on_a_resource_registered_moments_ago(monkeypatch):
    """资源动作作用于刚注册、索引尚未收录的资源

    RunResourceAction 的目标在搜索索引中暂时找不到时（例如刚从页面创建的工作台，索引刷新还在进行），从资源自己的提供方解析，与 ReadResource 读取的方式一致；设计师保存生成结果不会因此得到 instruction.not_found。
    """
    # A workbench created from its page is registered without waiting for the
    # index; the designer saves its generation right after (10-07 TAU).
    actions = []

    def not_indexed(resource_id, *, actor=""):
        raise KeyError(resource_id)

    monkeypatch.setattr(resource_isa, "_service", lambda: SimpleNamespace(
        get=not_indexed,
        resolve=lambda address, *, actor="": {
            "resource_type": "workbench", "record": {"resource_id": address, "title": "Fresh"},
        },
        action=lambda resource_id, action, arguments, *, actor="": actions.append((resource_id, action, actor)) or {
            "revision": 2,
        },
    ))
    arguments = {"resource_id": WORKBENCH, "action": "generation.save", "arguments": {
        "job_id": "job", "base_revision": 1, "title": "Page", "description": "Done", "html": "<h1>Page</h1>",
    }}
    assert resource_isa.run_resource_action(_invoke(arguments)) == {"result": {"revision": 2}}
    assert actions == [(WORKBENCH, "generation.save", "resident_0123456789ab")]
    # It changes the workbench, so the instruction is recorded.
    assert resource_isa.reads_only(_invoke(arguments)) is False


def test_a_resource_its_provider_does_not_know_either_is_not_found(monkeypatch):
    """提供方也不认识的资源仍然不存在

    索引和资源提供方都找不到目标时，RunResourceAction 仍报告资源不存在。
    """
    def missing(resource_id, *_args, **_kwargs):
        raise KeyError(resource_id)

    monkeypatch.setattr(resource_isa, "_service", lambda: SimpleNamespace(get=missing, resolve=missing))
    try:
        resource_isa.run_resource_action(_invoke({"resource_id": WORKBENCH, "action": "generation.save", "arguments": {}}))
    except KeyError as error:
        assert error.args == (WORKBENCH,)
    else:
        raise AssertionError("an unknown resource must stay not found")
