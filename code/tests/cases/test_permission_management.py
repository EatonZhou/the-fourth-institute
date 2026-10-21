"""Permission instructions ask the instructions they protect, never a level or a role (decision_22227d0ad075).

Entries, checks and temporary credentials name A nodes of the active
scheme; whether a caller may write or change entries on a resource or look at
another principal is the answer the scheme gives for the instruction called:
Create/Update/DeletePermissionEntry on the entry's resource, the principal_id
requirement of GetEffectivePermissions and its siblings.
"""
import pytest

from management_portal import permission_system
from management_portal.hatg.catalog import instruction_catalog
from management_portal.hatg.contracts import InstructionContext, InstructionError
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401

PROJECT = "Team"
LINEAGE = "lineage_aaaaaaaaaaaa"


def call(name, actor, *, expected_version=None, **arguments):
    return permission_system.manage(name, arguments, actor=actor, expected_version=expected_version)


@pytest.fixture
def world(permissions):  # noqa: F811
    permissions.users("alice")
    permissions.users("bob", "carol", category="intern")
    permissions.place(Fact("project", PROJECT), Fact("lineage", LINEAGE, {"project": PROJECT}))
    permissions.grant("alice", LINEAGE, "@admin")
    return permissions


def test_entries_name_a_nodes_and_ask_the_instruction_on_their_resource(world):
    """表项写 A 树节点；写入、修改和删除都按表项资源上的 CreatePermissionEntry 判定

    a 只接受 A 树节点，旧的等级名不再有效；返回的表项没有来源，只有内容、停用状态和版本。
    修改和删除在入口按库中已存表项的 r 判定（via: permission_entry.r），改 r 时新的 r 也判定：
    不能在那里写入表项的主体不能修改或删除那里的表项；指向不存在的表项按不存在返回。
    调用信封给出 expected_version 时按表项版本比较，版本已变化返回 instruction.version_conflict；不给时按当前内容提交。
    """
    entry = call("CreatePermissionEntry", "alice", p="bob", a="@contributor", r=LINEAGE)["entry"]
    entry_id = entry["entry_id"]
    assert entry["a"] == "@contributor" and entry["version"] == 1 and entry["disabled"] is False and "origin" not in entry
    with pytest.raises(ValueError, match="A 树"):
        call("CreatePermissionEntry", "alice", p="bob", a="contributor", r=LINEAGE)
    for name, arguments in (("CreatePermissionEntry", {"p": "carol", "a": "@viewer", "r": LINEAGE}),
                            ("UpdatePermissionEntry", {"entry_id": entry_id, "disabled": True}),
                            ("DeletePermissionEntry", {"entry_id": entry_id})):
        with pytest.raises(InstructionError) as refused:
            attempt(name, "bob", arguments)
        assert refused.value.code == "instruction.forbidden"
    with pytest.raises(InstructionError) as refused:
        attempt("UpdatePermissionEntry", "alice", {"entry_id": entry_id, "r": "@shared"})
    assert refused.value.code == "instruction.forbidden" and refused.value.details["target_id"] == "@shared"
    with pytest.raises(InstructionError) as missing:
        attempt("DeletePermissionEntry", "alice", {"entry_id": "entry_000000000000"})
    assert missing.value.code == "instruction.not_found"
    assert world.holds("bob", "@contributor", LINEAGE)
    disabled = call("UpdatePermissionEntry", "alice", entry_id=entry_id, expected_version=1, disabled=True)["entry"]
    assert disabled["disabled"] is True and disabled["version"] == 2 and not world.holds("bob", "@contributor", LINEAGE)
    with pytest.raises(InstructionError) as stale:
        call("UpdatePermissionEntry", "alice", entry_id=entry_id, expected_version=1, disabled=False)
    assert stale.value.code == "instruction.version_conflict"
    assert stale.value.details == {"object_id": entry_id, "expected_version": 1, "current_version": 2}
    assert call("UpdatePermissionEntry", "alice", entry_id=entry_id, disabled=False)["entry"]["version"] == 3
    assert call("DeletePermissionEntry", "alice", entry_id=entry_id)["deleted"] == [entry_id]


def attempt(name, actor, arguments):
    """A call as it arrives: the entry check on the arguments, then the handler."""
    permission_system.authorize_instruction(instruction_catalog()[name], arguments, InstructionContext(caller=actor, entry="web"))
    return permission_system.manage(name, arguments, actor=actor)


def administrators_entry(world):
    [entry] = call("ListPermissionEntries", "portal", p="@portal_admins", a="@actions", r="@shared")["entries"]
    return entry


def test_a_member_cannot_delete_the_administrators_entry(world):
    """一般用户不能删除管理员表项

    默认方案中 (@portal_admins, @actions, @shared) 让 Portal 管理员拥有全部权限。删除在入口
    按表项已保存的 r 判定：一般用户 alice 在“共享”上没有 @admin，删除被拒绝，表项还在。
    """
    entry = administrators_entry(world)
    with pytest.raises(InstructionError) as refused:
        attempt("DeletePermissionEntry", "alice", {"entry_id": entry["entry_id"]})
    assert refused.value.code == "instruction.forbidden"
    assert administrators_entry(world) == entry


def test_a_member_cannot_take_over_the_administrators_entry(world):
    """一般用户不能把管理员表项的 p 改成自己、r 改到自己管理的 Project

    一般用户 alice 管理所有 Project，在新的 r 上能写入表项；修改在入口还按表项已保存的 r 判定，
    alice 在“共享”上没有 @admin，修改被拒绝，表项原样保留。
    """
    entry = administrators_entry(world)
    with pytest.raises(InstructionError) as refused:
        attempt("UpdatePermissionEntry", "alice", {"entry_id": entry["entry_id"], "expected_version": entry["version"],
                                                   "p": "alice", "r": PROJECT})
    assert refused.value.code == "instruction.forbidden" and refused.value.details["target_id"] == "@shared"
    assert administrators_entry(world) == entry


def test_a_batch_delete_writes_nothing_when_one_entry_is_refused(world):
    """批量删除对每条表项的 r 都判定，只要有一条不通过，整批都不删除

    alice 能删除自己 Lineage 上的表项，但不能删除管理员表项；两条一起删除时被拒绝，自己 Lineage 上的那条也还在。
    """
    own = call("CreatePermissionEntry", "alice", p="carol", a="@viewer", r=LINEAGE)["entry"]
    entry = administrators_entry(world)
    with pytest.raises(InstructionError) as refused:
        attempt("DeletePermissionEntry", "alice", {"entry_ids": [own["entry_id"], entry["entry_id"]]})
    assert refused.value.code == "instruction.forbidden"
    assert world.holds("carol", "@viewer", LINEAGE) and administrators_entry(world) == entry
    assert attempt("DeletePermissionEntry", "alice", {"entry_ids": [own["entry_id"]]})["deleted"] == [own["entry_id"]]


def test_inspection_checks_and_loans_use_a_nodes(world):
    """查看、试判定和临时凭证都用 A 树节点

    GetEffectivePermissions 按 A 树分类节点给出覆盖范围，并报告 User 的分类。查看他人由方案中这几条指令对 principal_id 的要求判定：
    principal_id 必填，查看自己也要写明；查看自己不需要表项，实习用户查看别人被拒绝，管理员可以。
    CheckPermission 与 IssueTemporaryCredential 用 a 参数，凭证列表也报告 a。
    """
    world.grant("bob", LINEAGE, "@contributor")
    with pytest.raises(ValueError, match="principal_id"):
        call("GetEffectivePermissions", "bob")
    effective = call("GetEffectivePermissions", "bob", principal_id="bob")
    assert "levels" not in effective and effective["user_category"] == "intern"
    assert LINEAGE in effective["coverage"]["@contributor"]["permit"]
    assert LINEAGE not in effective["coverage"]["@admin"]["permit"]
    bob = InstructionContext(caller="bob", entry="web")
    for name in ("GetEffectivePermissions", "ListTemporaryCredentials", "CheckPermission"):
        assert permission_system.authorize_reads(instruction_catalog()[name], [{"principal_id": "bob"}], bob) == [None]
        assert instruction_catalog()[name].parameter_map["principal_id"].required, name
        [refused] = permission_system.authorize_reads(instruction_catalog()[name], [{"principal_id": "alice"}], bob)
        assert refused is not None and refused.code == "instruction.not_found", name
    world.administrator("carol")
    carol = InstructionContext(caller="carol", entry="web")
    assert permission_system.authorize_reads(instruction_catalog()["GetEffectivePermissions"], [{"principal_id": "bob"}], carol) == [None]
    assert call("GetEffectivePermissions", "carol", principal_id="bob")["principal_id"] == "bob"
    assert call("CheckPermission", "alice", principal_id="bob", a="@contributor", resource_id=LINEAGE)["allowed"] is True
    assert call("CheckPermission", "alice", principal_id="bob", a="@admin", resource_id=LINEAGE)["allowed"] is False
    loan = call("IssueTemporaryCredential", "alice", principal_id="carol", a="@viewer", resource_id=LINEAGE)
    assert loan["a"] == "@viewer" and "level" not in loan
    [listed] = call("ListTemporaryCredentials", "alice", principal_id="alice")["credentials"]
    assert listed["a"] == "@viewer" and listed["direction"] == "issued"


def test_temporary_credentials_are_rows_that_end_with_their_task_or_time(world, monkeypatch):
    """临时凭证是一行带签发者和过期时间的 permit：随消息签发的在输入结束时删除，按时长签发的可由签发者续期

    签发只核对签发者此刻持有；签发者的 deny 不复制给接收者，签发者之后失去权限也不影响凭证。随消息签发要求接收 Resident 的这条输入仍在处理，
    输入结束（end_task_credentials 在同一事务中）时凭证删除；随消息的凭证不能续期，按时长的只能由签发者续期且不超过签发后 7 天。
    签发者可以撤回、接收者可以交还，其他人不能撤销；处理代码不再按凭证资源向引擎追问。
    """
    from management_portal.permission_system import management

    resident = "resident_aaaaaaaaaaaa"
    world.place(Fact("resident", resident, {"lineage": "lineage_bbbbbbbbbbbb"}))
    world.deny("alice", "decision_aaaaaaaaaaaa", "@admin")
    open_inputs = {(resident, "msg_task")}
    monkeypatch.setattr(management, "input_open", lambda principal, message: (principal, message) in open_inputs)
    with pytest.raises(PermissionError, match="没有"):
        call("IssueTemporaryCredential", "bob", principal_id=resident, a="@admin", resource_id=LINEAGE)
    with pytest.raises(PermissionError, match="处理中"):
        call("IssueTemporaryCredential", "alice", principal_id=resident, a="@admin", resource_id=LINEAGE, message_id="msg_done")
    task = call("IssueTemporaryCredential", "alice", principal_id=resident, a="@admin", resource_id=LINEAGE, message_id="msg_task")
    assert task["message_id"] == "msg_task" and task["expires_at"] - task["issued_at"] == pytest.approx(7 * 24 * 3600, abs=5)
    assert world.holds(resident, "@admin", LINEAGE)
    with world.store.read() as connection:
        lent = world.store.entries(connection, where="issued_by = 'alice'")
    assert [(entry.effect, entry.p, entry.a, entry.r) for entry in lent] == [("permit", resident, "@admin", LINEAGE)]
    with pytest.raises(ValueError, match="不能续期"):
        call("RenewTemporaryCredential", "alice", credential_id=task["credential_id"], expires_in_seconds=3600)
    with world.store.transaction() as connection:
        assert permission_system.end_task_credentials(connection, resident, "msg_task") == 1
    assert not world.holds(resident, "@admin", LINEAGE)

    timed = call("IssueTemporaryCredential", "alice", principal_id="carol", a="@viewer", resource_id=LINEAGE,
                 expires_in_seconds=600)
    with pytest.raises(PermissionError, match="签发者"):
        call("RenewTemporaryCredential", "carol", credential_id=timed["credential_id"], expires_in_seconds=3600)
    renewed = call("RenewTemporaryCredential", "alice", credential_id=timed["credential_id"], expires_in_seconds=7 * 24 * 3600)
    assert renewed["expires_at"] == pytest.approx(timed["issued_at"] + 7 * 24 * 3600, abs=1)
    world.administrator("dave")
    for outsider in ("bob", "dave"):
        with pytest.raises(PermissionError, match="签发者或接收者"):
            call("RevokeTemporaryCredential", outsider, credential_id=timed["credential_id"])
    assert call("RevokeTemporaryCredential", "carol", credential_id=timed["credential_id"]) == {"revoked": timed["credential_id"]}


def test_business_questions_ask_instructions(world):
    """业务代码按指令询问：能否管理 Lineage、谁收失败通知、页面开关

    permitted 只判定该指令映射到这个参数的要求；allows 判定整条调用；User 分类是一项事实，模板按它把 User 放进 P 树的分类节点。
    """
    assert permission_system.permitted_ids("alice", "UpdateLineage", "lineage_id", [LINEAGE]) == {LINEAGE}
    assert permission_system.permitted_ids("bob", "UpdateLineage", "lineage_id", [LINEAGE]) == set()
    assert permission_system.permitted_ids("bob", "GetLineage", "lineage_id", [LINEAGE]) == {LINEAGE}
    # A parameter the instruction maps nothing to passes; an unmapped instruction asks its free request.
    assert permission_system.permitted_ids("bob", "UpdateLineage", "title", [LINEAGE]) == {LINEAGE}
    assert permission_system.permitted_ids("bob", "GetPortalStatus", "anything", [LINEAGE]) == {LINEAGE}
    assert not permission_system.allows("alice", "CreateUser")
    assert permission_system.allows("alice", "UpdateLineage", {"lineage_id": LINEAGE})
    assert not permission_system.allows("alice", "UpdateLineage", {})
    world.administrator("carol")
    assert permission_system.allows("carol", "CreateUser") and permission_system.user_categories()["carol"] == "portal_admin"
    assert permission_system.allows("carol", "OpenTerminal") and not permission_system.allows("alice", "OpenTerminal")
