"""Permission schemes with the permission system on or off (decision_b820faf42218 §5, decision_22227d0ad075).

A permission scheme is its rules plus the instance saved with it.  While
the permission system is off everyone signed in may read and change
schemes and their entries; activating a scheme only records the choice, and
turning the permission system on brings that scheme's instance up to date.
"""
import copy

import pytest

from management_portal import permission_system
from management_portal.hatg.contracts import InstructionError
from management_portal.permission_system.engine.model import PERMIT, Entry
from management_portal.permission_system.engine.store import new_entry_id
from management_portal.permission_system.facts import Fact, user_fact
from management_portal.permission_system.par import PARPermissions
from management_portal.permission_system.scheme.repository import builtin_documents
from tests.cases.permission_test_support import ACTIVE_COPY, Permissions, enable

PROJECT = "Team"
LINEAGE = "lineage_aaaaaaaaaaaa"


def facts():
    return [user_fact("alice"), user_fact("carol"), Fact("project", PROJECT), Fact("lineage", LINEAGE, {"project": PROJECT})]


@pytest.fixture
def off(monkeypatch):
    """The permission system off; permission instructions run on this process's implementation, unchecked."""
    implementation = PARPermissions()
    monkeypatch.setattr(permission_system, "configured_mode", lambda: permission_system.OPEN)
    monkeypatch.setattr(permission_system, "_IMPLEMENTATION", implementation)
    yield implementation
    permission_system.forget_implementation()


def call(name, actor="alice", objects=facts, expected_version=None, **arguments):
    # expected_version travels in the call envelope, not among the instruction's arguments.
    return permission_system.manage(name, arguments, actor=actor, collect_facts=objects, expected_version=expected_version)


def rules(scheme_id="team", **changes):
    document = copy.deepcopy(builtin_documents()["default"])
    document.update({"scheme_id": scheme_id, "title": "团队方案", **changes})
    return document


def position(document, entry_id):
    return next(index for index, entry in enumerate(document["entries"]) if entry.get("id") == entry_id)


def refused(name, **arguments) -> InstructionError:
    with pytest.raises(InstructionError) as raised:
        call(name, **arguments)
    return raised.value


EVERYTHING = {"r": "@portal", "r_scope": "subtree"}


def contents(scheme_id, **filters):
    listed = call("ListPermissionEntries", scheme_id=scheme_id, limit=500, **(filters or EVERYTHING))["entries"]
    return {(item["effect"], item["p"], item["a"], item["r"]) for item in listed}


def test_schemes_stay_editable_while_the_permission_system_is_off(off):
    """权限系统关闭时方案照常查看和编辑

    关闭时 GetPermissionSystem 报告开启时将使用的方案和它是否需要生成实例；任何已登录主体都能创建、修改方案，
    复制方案连同实例一起复制，编译错误带 JSON 路径；调用信封给出的 expected_version 过期时返回版本冲突并带当前版本，
    不给时按当前内容保存，与开启时一致。
    """
    system = call("GetPermissionSystem")
    assert (system["enabled"], system["mode"], system["needs_regenerate"]) == (False, "open", True)
    assert all(system["allows"].values())
    assert {key: system["scheme"][key] for key in ("scheme_id", "version", "builtin")} == {
        "scheme_id": "default", "version": 0, "builtin": True}
    assert call("CreatePermissionScheme", scheme_id="team", rules=rules(), dry_run=True) == {
        "valid": True, "errors": [], "unmapped": []}
    created = call("CreatePermissionScheme", scheme_id="team", rules=rules())
    assert (created["scheme_id"], created["version"]) == ("team", 1) and created["entries"] > 0
    copied = call("CreatePermissionScheme", scheme_id="team_copy", source_scheme_id="team")
    assert (copied["nodes"], copied["entries"]) == (created["nodes"], created["entries"])
    assert call("GetPermissionScheme", scheme_id="team_copy")["rules"]["title"] == "团队方案 副本"
    assert refused("CreatePermissionScheme", scheme_id="default", source_scheme_id="team").code == "instruction.conflict"

    broken = rules()
    broken["entries"][0]["a"] = "@nowhere"
    invalid = refused("UpdatePermissionScheme", scheme_id="team", rules=broken, expected_version=1)
    assert invalid.code == "instruction.invalid_arguments"
    assert [item["path"] for item in invalid.details["errors"]] == ["entries[0].a"]
    retitled = call("UpdatePermissionScheme", scheme_id="team", rules=rules(description="改过"), expected_version=1)
    assert (retitled["version"], retitled["regenerated"]) == (2, None)
    stale = refused("UpdatePermissionScheme", scheme_id="team", rules=rules(), expected_version=1)
    assert stale.code == "instruction.version_conflict"
    assert stale.details == {"object_id": "team", "expected_version": 1, "current_version": 2}
    assert call("UpdatePermissionScheme", scheme_id="team", rules=rules(description="改过"))["version"] == 3
    assert refused("UpdatePermissionScheme", scheme_id="default", rules=rules("default"), expected_version=0).code == "instruction.conflict"

    listed = {item["scheme_id"]: (item["active"], item["entry_count"]) for item in call("ListPermissionSchemes")["schemes"]}
    assert listed == {"default": (True, 0), "team": (False, created["entries"]),
                      "team_copy": (False, created["entries"])}
    found = call("GetPermissionScheme", scheme_id="team", vocabulary=True)
    assert found["active"] is False and found["rules"]["description"] == "改过" and found["created_by"] == "alice"
    assert any(item["name"] == "UpdatePermissionScheme" for item in found["vocabulary"]["instructions"])
    assert call("DeletePermissionScheme", scheme_id="team_copy") == {"deleted": "team_copy"}
    assert "team_copy" not in {item["scheme_id"] for item in call("ListPermissionSchemes")["schemes"]}


def test_changing_rules_regenerates_the_instance_and_reports_the_lost_edits(off):
    """修改规则即重新生成实例，dry_run 报告增删与将丢失的手工修改

    删掉“全员可管理公共内容”规则后，dry_run 列出将删除的表项，以及手工加的、删掉的和停用的表项（重新生成时丢失），实例不变；
    保存后实例按新规则生成：手工修改都不在了，临时凭证保留。只改标题不重新生成，手工修改保留。
    """
    call("CreatePermissionScheme", scheme_id="team", rules=rules())
    call("CreatePermissionEntry", scheme_id="team", p="carol", a="@viewer", r="personal_space:alice")
    fixed = call("ListPermissionEntries", scheme_id="team", p="@members", a="@admin", r="@projects")["entries"]
    call("DeletePermissionEntry", scheme_id="team", entry_id=fixed[0]["entry_id"])
    [own] = call("ListPermissionEntries", scheme_id="team", p=PROJECT, r=PROJECT)["entries"]
    call("UpdatePermissionEntry", scheme_id="team", entry_id=own["entry_id"], expected_version=1, disabled=True)
    store = off.schemes.store.scoped("team")
    with store.transaction() as connection:
        store.add(connection, [Entry(new_entry_id(), PERMIT, "carol", "@viewer", LINEAGE, expires_at=4102444800.0)])

    retitled = call("UpdatePermissionScheme", scheme_id="team", rules=rules(title="团队"), expected_version=1)
    assert retitled["regenerated"] is None and ("permit", "carol", "@viewer", "personal_space:alice") in contents("team")

    changed = rules(title="团队")
    del changed["entries"][position(changed, "everyone_manages_public")]
    before = contents("team")
    report = call("UpdatePermissionScheme", scheme_id="team", rules=changed, dry_run=True)
    assert report["valid"] is True and report["regenerates"] is True and contents("team") == before
    diff = report["diff"]
    assert {"effect": "permit", "p": "@principals", "a": "@admin", "r": "@public", "disabled": False} in diff["removed"]["items"]
    lost = {(item["kind"], item["entry"]["p"], item["entry"]["r"]) for item in diff["lost_edits"]["items"]}
    assert lost == {("added", "carol", "personal_space:alice"), ("deleted", "@members", "@projects"), ("disabled", PROJECT, PROJECT)}

    saved = call("UpdatePermissionScheme", scheme_id="team", rules=changed, expected_version=2)
    assert saved["version"] == 3 and saved["regenerated"]["entries"] > 0
    after = contents("team")
    assert ("permit", "carol", "@viewer", "personal_space:alice") not in after
    assert ("permit", "@principals", "@admin", "@public") not in after
    assert ("permit", "@members", "@admin", "@projects") in after
    assert call("ListPermissionEntries", scheme_id="team", p=PROJECT, r=PROJECT)["entries"][0]["disabled"] is False
    assert call("ListPermissionEntries", scheme_id="team", temporary=True, **EVERYTHING)["total"] == 1
    assert call("UpdatePermissionScheme", scheme_id="team", rules=changed, dry_run=True)["regenerates"] is False


def test_entries_are_checked_for_nodes_only(off):
    """表项写入只校验节点存在，不做语义检查

    任何已登录主体都能写入、修改、停用和删除表项，返回所属方案；节点不存在时拒绝，内容相同的表项不能有两条；
    与其他表项矛盾或互相覆盖的表项照常写入。内置方案（这里是启用中的默认方案）的实例只读；临时凭证不能当作表项删除。
    """
    assert refused("CreatePermissionEntry", p="carol", a="@admin", r="@operations").code == "instruction.conflict"
    call("CreatePermissionScheme", scheme_id="team", rules=rules())
    entry = call("CreatePermissionEntry", scheme_id="team", p="carol", a="@admin", r="@operations")
    assert entry["scheme_id"] == "team" and (entry["entry"]["p"], entry["entry"]["version"]) == ("carol", 1)
    assert "origin" not in entry["entry"] and "rule" not in entry["entry"]
    with pytest.raises(ValueError, match="A 树"):
        call("CreatePermissionEntry", scheme_id="team", p="carol", a="admin", r="@shared")
    with pytest.raises(ValueError, match="P 树"):
        call("CreatePermissionEntry", scheme_id="team", p="@nobody", a="@admin", r="@shared")
    assert refused("CreatePermissionEntry", actor="", scheme_id="team", p="carol", a="@admin", r="@shared").code == "instruction.forbidden"
    duplicate = refused("CreatePermissionEntry", scheme_id="team", p="carol", a="@admin", r="@operations")
    assert duplicate.code == "instruction.conflict" and entry["entry"]["entry_id"] in str(duplicate)
    # A deny over everything and a permit inside it: nothing checks what rows mean together.
    denied = call("CreatePermissionEntry", actor="bob", scheme_id="team", effect="deny", p="@principals", a="@actions", r="@shared")
    inside = call("CreatePermissionEntry", actor="bob", scheme_id="team", p="@principals", a="@viewer", r=LINEAGE)
    moved = call("UpdatePermissionEntry", scheme_id="team", entry_id=inside["entry"]["entry_id"], expected_version=1, r=PROJECT)
    assert (moved["entry"]["r"], moved["entry"]["version"]) == (PROJECT, 2)
    assert call("DeletePermissionEntry", scheme_id="team", entry_ids=[denied["entry"]["entry_id"], inside["entry"]["entry_id"]])[
        "deleted"] == [denied["entry"]["entry_id"], inside["entry"]["entry_id"]]

    store = off.schemes.store.scoped("team")
    loan = Entry(new_entry_id(), PERMIT, "carol", "@viewer", LINEAGE, expires_at=4102444800.0)
    with store.transaction() as connection:
        store.add(connection, [loan])
    assert refused("DeletePermissionEntry", scheme_id="team", entry_id=loan.entry_id).code == "instruction.conflict"


def test_entries_are_listed_by_node_and_scope(off):
    """ListPermissionEntries 按三棵树的节点和范围筛选，分页报告总数

    exact 只看节点本身，ancestors 看节点及其祖先（判定会读到的表项），subtree 看节点及其下的全部节点；还可按 effect、停用与临时筛选。
    SearchPermissionNodes 按 ID 或标题找类节点和对象，GetPermissionNode 列出子节点。
    """
    call("CreatePermissionScheme", scheme_id="team", rules=rules())
    call("CreatePermissionEntry", scheme_id="team", p="carol", a="@contributor", r=LINEAGE)
    assert contents("team", r=LINEAGE) == {("permit", "carol", "@contributor", LINEAGE)}
    above = contents("team", r=LINEAGE, r_scope="ancestors")
    assert {("permit", "@members", "@admin", "@projects"), ("permit", "carol", "@contributor", LINEAGE),
            ("permit", PROJECT, "@admin", PROJECT)} <= above
    assert ("permit", "carol", "@contributor", LINEAGE) in contents("team", r=PROJECT, r_scope="subtree")
    assert ("permit", "carol", "@contributor", LINEAGE) not in contents("team", r=PROJECT)
    assert contents("team", p="carol", a="@viewer", a_scope="subtree") == set()
    assert contents("team", p="carol", a="@actions", a_scope="subtree", r=LINEAGE) == {("permit", "carol", "@contributor", LINEAGE)}
    first = call("ListPermissionEntries", scheme_id="team", limit=1, **EVERYTHING)
    page = call("ListPermissionEntries", scheme_id="team", limit=1, cursor=first["next_cursor"], **EVERYTHING)
    assert page["total"] == len(contents("team")) and len(page["entries"]) == 1
    assert page["entries"] != first["entries"] and "offset" not in page
    pages, cursor = [], None
    while True:
        listed = call("ListPermissionEntries", scheme_id="team", limit=7, **({"cursor": cursor} if cursor else {}), **EVERYTHING)
        pages.append(listed["entries"])
        cursor = listed.get("next_cursor")
        if not cursor:
            break
    assert sum(len(entries) for entries in pages) == page["total"]
    assert call("ListPermissionEntries", scheme_id="team", disabled=True, **EVERYTHING)["total"] == 0
    assert call("ListPermissionEntries", scheme_id="team", effect="deny", **EVERYTHING)["total"] == 0
    with pytest.raises(ValueError, match="至少给一个"):
        call("ListPermissionEntries", scheme_id="team", a="@admin")

    found = call("SearchPermissionNodes", scheme_id="team", tree="R", query="lineage_aaaa")
    assert found["scheme_id"] == "team" and found["nodes"][0]["path"][-2:] == [PROJECT, LINEAGE]
    assert any(item["id"] == "@projects" for item in call("SearchPermissionNodes", scheme_id="team", tree="R", query="projects")["nodes"])
    assert call("SearchPermissionNodes", scheme_id="team", tree="A", query="RunScript")["nodes"][0]["id"] == "RunScript"
    node = call("GetPermissionNode", scheme_id="team", tree="R", node_id=PROJECT)
    assert node["node"]["path"][-1] == PROJECT and [child["id"] for child in node["children"]] == [LINEAGE]


def test_schemes_report_the_instructions_they_leave_unmapped(off):
    """方案读取与 dry_run 列出没有映射的指令

    默认方案映射全部指令；去掉两条映射后，dry_run 创建与启用、GetPermissionScheme 都按名称列出它们，编译失败的 dry_run 也列出。
    """
    assert call("GetPermissionScheme", scheme_id="default")["unmapped"] == []
    partial = rules()
    del partial["instructions"]["RunScript"], partial["instructions"]["PublishRelease"]
    assert call("CreatePermissionScheme", scheme_id="team", rules=partial, dry_run=True)["unmapped"] == ["PublishRelease", "RunScript"]
    broken = copy.deepcopy(partial)
    broken["entries"][0]["a"] = "@nowhere"
    report = call("CreatePermissionScheme", scheme_id="team", rules=broken, dry_run=True)
    assert report["valid"] is False and report["unmapped"] == ["PublishRelease", "RunScript"]
    call("CreatePermissionScheme", scheme_id="team", rules=partial)
    assert call("GetPermissionScheme", scheme_id="team")["unmapped"] == ["PublishRelease", "RunScript"]
    assert call("UpdatePermissionScheme", scheme_id="team", rules=rules(), dry_run=True)["unmapped"] == []
    assert call("ActivatePermissionScheme", scheme_id="team", dry_run=True)["unmapped"] == ["PublishRelease", "RunScript"]


def test_a_scheme_activated_while_off_is_completed_when_turned_on(off):
    """关闭时启用方案只记录选择，开启时把它的实例补齐

    关闭时 ActivatePermissionScheme 不改实例，报告开启时补齐将新增、移动和删除的数量；GetPermissionSystem、方案列表和读取都把它标为启用；它不能删除。
    开启时实例补齐：期间新建的 Project 得到规则给它的表项，实例中的编辑保留。内置方案也可以启用（§5.11）。
    """
    call("CreatePermissionScheme", scheme_id="team", rules=rules())
    kept = call("CreatePermissionEntry", scheme_id="team", p="carol", a="@admin", r=LINEAGE)["entry"]
    later = [*facts(), Fact("project", "LaterProject")]
    planned = call("ActivatePermissionScheme", scheme_id="team", objects=lambda: later)
    assert planned["activated"] is True and planned["regenerate"] is False
    assert (planned["added_nodes"], planned["removed_nodes"]) == (2, 0) and planned["added_entries"] > 0
    assert ("permit", "LaterProject", "@admin", "LaterProject") not in contents("team")
    assert call("ActivatePermissionScheme", scheme_id="default", dry_run=True)["activated"] is False

    assert call("GetPermissionSystem")["scheme"]["scheme_id"] == "team"
    current = call("GetPermissionScheme")
    assert (current["scheme_id"], current["active"]) == ("team", True)
    assert [item["scheme_id"] for item in call("ListPermissionSchemes")["schemes"] if item["active"]] == ["team"]
    assert refused("DeletePermissionScheme", scheme_id="team").code == "instruction.conflict"

    report = PARPermissions().prepare_active(later)
    assert report["scheme_id"] == "team" and report["regenerate"] is False and report["added_entries"] == planned["added_entries"]
    assert ("permit", "LaterProject", "@admin", "LaterProject") in contents("team")
    assert kept["entry_id"] in {item["entry_id"] for item in call("ListPermissionEntries", scheme_id="team", p="carol")["entries"]}


def test_a_builtin_scheme_is_used_as_it_is_and_a_release_keeps_its_edits(off, monkeypatch):
    """内置方案规则只读、可直接启用：实例照常编辑，发布改变内置规则时启动写回实例中的编辑（§5.11）

    启用中的默认方案实例可以增加、停用、删除表项，也可以签发临时凭证。发布改变默认方案的规则后，ResourceService 启动时
    按新规则重新生成默认方案自己的实例，和复制品的合并一样写回：增加的行、停用的行带原 ID 回来，人删掉、新规则仍给出的行保持删除，
    临时凭证保留；这次写回连同授权差记为默认方案的 last_merge。复制品的实例不动，只标出来源已改变。
    实例在保存规则全文之前生成、规则又没变时，启动时补存规则全文，作为下次发布的比较基准。
    """
    from types import SimpleNamespace

    from management_portal.permission_system.scheme import repository
    from management_portal.resources.service import ResourceService

    service = SimpleNamespace(permission_facts=facts)
    # The active built-in scheme gets its instance at startup.
    generated = ResourceService.ensure_permission_instance(service)
    assert list(generated) == ["default"] and generated["default"]["instance"]["entries"] > 0
    assert ResourceService.ensure_permission_instance(service) is None
    added = call("CreatePermissionEntry", p="carol", a="@admin", r="@operations")["entry"]
    [disabled] = call("ListPermissionEntries", scheme_id="default", p="@members", a="@admin", r="@projects")["entries"]
    assert disabled["allows"] == {"UpdatePermissionEntry": True, "DeletePermissionEntry": True}
    call("UpdatePermissionEntry", scheme_id="default", entry_id=disabled["entry_id"], expected_version=1, disabled=True)
    [deleted] = call("ListPermissionEntries", scheme_id="default", p="@principals", a="@message", r="@shared")["entries"]
    call("DeletePermissionEntry", scheme_id="default", entry_id=deleted["entry_id"])
    loan = call("IssueTemporaryCredential", principal_id="carol", a="@viewer", resource_id=LINEAGE, expires_in_seconds=3600)
    call("CreatePermissionScheme", scheme_id="team", source_scheme_id="default")
    copied = off.store.scoped("team").instance()

    # An instance saved before the rules were kept with it gets them while its rules are current.
    with off.store.transaction() as connection:
        off.store.execute(connection, "UPDATE {s}.scheme_instances SET rules = NULL WHERE scheme_id = 'default'")
    assert off.stale_builtin() == [] and off.store.scoped("default").basis() == builtin_documents()["default"]

    released = repository.builtin_documents()
    del released["default"]["entries"][position(released["default"], "everyone_manages_public")]
    monkeypatch.setattr(repository, "builtin_documents", lambda: copy.deepcopy(released))
    report = ResourceService.ensure_permission_instance(service)["default"]
    assert (report["source_scheme_id"], report["unrestored"], report["conflicts"]) == ("default", [], [])
    assert report["deleted_again"] == [{"effect": "permit", "p": "@principals", "a": "@message", "r": "@shared"}]
    # These facts hold no public object, so taking the public rule away changes no decision.
    assert report["auth_diff"]["kind"] == "neutral"
    rows = {item["entry_id"]: item for item in call("ListPermissionEntries", scheme_id="default", **EVERYTHING, limit=500)["entries"]}
    assert ("permit", "@principals", "@admin", "@public") not in contents("default")
    assert ("permit", "@principals", "@message", "@shared") not in contents("default")
    assert rows[added["entry_id"]]["r"] == "@operations" and rows[disabled["entry_id"]]["disabled"] is True
    assert loan["credential_id"] in rows
    merged = call("GetPermissionScheme", scheme_id="default")["last_merge"]
    assert (merged["source_scheme_id"], merged["to_digest"], merged["merged_by"]) == (
        "default", repository.source_digest(released["default"]), "portal")
    assert merged["deleted_again"] == report["deleted_again"] and merged["auth_diff"] == report["auth_diff"]
    assert merged["regenerated"] is True
    assert off.store.scoped("default").basis() == released["default"]
    assert ResourceService.ensure_permission_instance(service) is None
    # The copy's instance stays as it was; it marks its source as changed, for the merge that follows.
    assert off.store.scoped("team").instance()["rules_digest"] == copied["rules_digest"]
    assert ("permit", "@principals", "@admin", "@public") in contents("team")
    assert PARPermissions().schemes.source("team")["outdated"] is True


def test_activating_before_the_startup_merge_keeps_the_builtin_edits(off, monkeypatch):
    """启动写回之前（或写回失败后）启用内置方案或开启开关，同样写回实例中的编辑，记为 last_merge

    发布改变默认方案的规则后，ResourceService 还没跟随（启动预热中，或跟随失败）时，ActivatePermissionScheme 的 dry_run
    报告写回的结果而不写入；开启开关（prepare_active）按新规则重新生成并写回增加的行，写回连同授权差记为 last_merge，
    之后启动跟随无事可做。7a4a 审查 c6eb4deb 时复现：整套重新生成，增加的行丢失且没有记录。
    """
    from types import SimpleNamespace

    from management_portal.permission_system.scheme import repository
    from management_portal.resources.service import ResourceService

    service = SimpleNamespace(permission_facts=facts)
    ResourceService.ensure_permission_instance(service)
    added = call("CreatePermissionEntry", p="carol", a="@admin", r="@operations")["entry"]
    released = repository.builtin_documents()
    del released["default"]["entries"][position(released["default"], "everyone_manages_public")]
    monkeypatch.setattr(repository, "builtin_documents", lambda: copy.deepcopy(released))
    monkeypatch.setattr(permission_system, "_IMPLEMENTATION", PARPermissions())
    assert PARPermissions().stale_builtin() == ["default"]

    preview = call("ActivatePermissionScheme", scheme_id="default", dry_run=True)
    assert preview["regenerate"] is True and preview["unrestored"] == [] and preview["restored_entries"] >= 1
    assert PARPermissions().stale_builtin() == ["default"] and call("GetPermissionScheme", scheme_id="default").get("last_merge") is None

    report = PARPermissions().prepare_active(facts())
    assert report["regenerate"] is True and report["unrestored"] == []
    assert ("permit", "carol", "@admin", "@operations") in contents("default")
    assert ("permit", "@principals", "@admin", "@public") not in contents("default")
    rows = {item["entry_id"] for item in call("ListPermissionEntries", scheme_id="default", **EVERYTHING, limit=500)["entries"]}
    assert added["entry_id"] in rows
    merged = call("GetPermissionScheme", scheme_id="default")["last_merge"]
    assert (merged["source_scheme_id"], merged["to_digest"]) == ("default", repository.source_digest(released["default"]))
    assert ResourceService.ensure_permission_instance(service) is None


@pytest.mark.parametrize("dry_run", [False, True])
def test_activating_while_another_process_follows_the_release_brings_the_instance_up_to_date(off, monkeypatch, dry_run):
    """启用或开启开关读到旧实例后，另一个进程先完成了启动跟随，这次启用改为补齐，不失败

    Dev 和 Release 共库，启动跟随和启用可能同时进行：prepare 读到实例还在旧规则上，拿到跟随锁时另一个进程已经写回，
    follow_release 返回 None。这时实例已在新规则上，报告与“实例已在当前规则上”相同（regenerate 为 false），
    另一个进程写回的行仍在。7a4a 审查 b623122e 时复现：prepare_active 抛 TypeError，启用以 instruction.failed 失败。
    """
    import threading
    from types import SimpleNamespace

    from management_portal.permission_system import schemes as schemes_module
    from management_portal.permission_system.scheme import repository
    from management_portal.resources.service import ResourceService

    service = SimpleNamespace(permission_facts=facts)
    ResourceService.ensure_permission_instance(service)
    call("CreatePermissionEntry", p="carol", a="@admin", r="@operations")
    released = repository.builtin_documents()
    del released["default"]["entries"][position(released["default"], "everyone_manages_public")]
    monkeypatch.setattr(repository, "builtin_documents", lambda: copy.deepcopy(released))
    monkeypatch.setattr(permission_system, "_IMPLEMENTATION", PARPermissions())

    original = schemes_module.follow_release
    raced = []

    def follow_release(*arguments, **options):
        if not raced:
            # The other process's startup merge commits between prepare's read and its own follow.
            raced.append(None)
            other = threading.Thread(target=lambda: raced.append(PARPermissions().follow_release("default", facts())))
            other.start()
            other.join()
        return original(*arguments, **options)

    monkeypatch.setattr(schemes_module, "follow_release", follow_release)
    if dry_run:
        report = call("ActivatePermissionScheme", scheme_id="default", dry_run=True)
    else:
        report = PARPermissions().prepare_active(facts())
    assert raced[1]["instance"]["restored_entries"] == 1
    assert report["regenerate"] is False
    assert ("permit", "carol", "@admin", "@operations") in contents("default")
    assert ("permit", "@principals", "@admin", "@public") not in contents("default")
    assert PARPermissions().stale_builtin() == []


def test_a_copy_records_its_source_and_a_merge_moves_the_record_on(off):
    """复制品记录来源方案和复制时的来源规则；来源改变后标为过期，带 source_digest 保存的合并把来源记录推进到当前规则

    来源记录供编辑器三方对比：复制时的来源规则、来源现在的规则、复制品的规则（§5.11）。摘要不含 scheme_id 与 title；
    普通保存不改来源记录；source_digest 与来源现在的摘要不同时冲突；来源删除后不再标为过期；按规则新建的方案没有来源。
    """
    from management_portal.permission_system.scheme.repository import source_digest

    call("CreatePermissionScheme", scheme_id="team", rules=rules())
    call("CreatePermissionScheme", scheme_id="team_copy", source_scheme_id="team")
    assert "source" not in call("GetPermissionScheme", scheme_id="team")
    source = call("GetPermissionScheme", scheme_id="team_copy")["source"]
    assert (source["scheme_id"], source["rules"], source["outdated"]) == ("team", rules(), False)
    assert source["digest"] == source["current_digest"] == source_digest(rules()) == source_digest(rules(title="别的标题"))
    call("CreatePermissionScheme", scheme_id="default_copy", source_scheme_id="default")
    assert call("GetPermissionScheme", scheme_id="default_copy")["source"]["digest"] == source_digest(builtin_documents()["default"])

    changed = rules(description="来源改过")
    call("UpdatePermissionScheme", scheme_id="team", rules=changed, expected_version=1)
    listed = {item["scheme_id"]: (item["source_scheme_id"], item["outdated"]) for item in call("ListPermissionSchemes")["schemes"]}
    assert listed["team_copy"] == ("team", True) and listed["team"] == ("", False) and listed["default"] == ("", False)
    source = call("GetPermissionScheme", scheme_id="team_copy")["source"]
    assert source["outdated"] is True and source["rules"]["description"] != "来源改过"

    mine = call("GetPermissionScheme", scheme_id="team_copy")["rules"]
    call("UpdatePermissionScheme", scheme_id="team_copy", rules={**mine, "title": "只改标题"}, expected_version=1)
    assert call("GetPermissionScheme", scheme_id="team_copy")["source"]["outdated"] is True
    stale = refused("UpdatePermissionScheme", scheme_id="team_copy", rules={**mine, "description": "来源改过"},
                    expected_version=2, source_digest=source["digest"])
    assert stale.code == "instruction.conflict" and source["current_digest"] in str(stale)
    with pytest.raises(ValueError, match="没有记录来源方案"):  # instruction.invalid_arguments through the ISA
        call("UpdatePermissionScheme", scheme_id="team", rules=changed, expected_version=2, source_digest=source["current_digest"])
    merged = call("UpdatePermissionScheme", scheme_id="team_copy", rules={**mine, "description": "来源改过"},
                  expected_version=2, source_digest=source["current_digest"])
    assert (merged["version"], merged["source_digest"]) == (3, source["current_digest"])
    source = call("GetPermissionScheme", scheme_id="team_copy")["source"]
    assert source["outdated"] is False and source["rules"] == changed

    call("UpdatePermissionScheme", scheme_id="team", rules=rules(description="又改"), expected_version=2)
    call("DeletePermissionScheme", scheme_id="team")
    source = call("GetPermissionScheme", scheme_id="team_copy")["source"]
    assert (source["current_digest"], source["outdated"]) == (None, False)
    call("DeletePermissionScheme", scheme_id="team_copy")
    assert "team_copy" not in off.schemes.sources()


def test_a_saved_scheme_still_loads_after_a_release_removes_an_instruction_it_maps(off, monkeypatch):
    """发布删除了某条指令后，仍映射它的已保存方案照常加载（跳过这条映射），再次保存时才报告

    没有人能调用已删除的指令，跳过它的映射不改变任何判定；启用中的方案因此不会整份失效。保存规则仍严格编译，
    编辑器会看到这条映射的路径，删除它后才能保存。
    """
    from management_portal.permission_system.scheme import repository

    call("CreatePermissionScheme", scheme_id="team", rules=rules())
    call("ActivatePermissionScheme", scheme_id="team")
    remaining = {name: decides for name, decides in repository.declared_decisions().items() if name != "GetResident"}
    monkeypatch.setattr(repository, "declared_decisions", lambda: remaining)
    monkeypatch.setattr(permission_system, "_IMPLEMENTATION", PARPermissions())

    assert call("GetPermissionSystem")["scheme"]["scheme_id"] == "team"
    assert "GetResident" not in permission_system.active().current()[0].instructions
    error = refused("UpdatePermissionScheme", scheme_id="team", rules=rules(title="改标题"), expected_version=1)
    assert error.code == "instruction.invalid_arguments" and error.details["errors"][0]["path"] == "instructions.GetResident"
    document = rules(title="改标题")
    del document["instructions"]["GetResident"]
    assert call("UpdatePermissionScheme", scheme_id="team", rules=document, expected_version=1)["regenerated"] is None


def test_startup_merges_a_builtin_release_into_its_copy_and_keeps_hand_rows(off, monkeypatch):
    """发布改变内置规则后，ResourceService 启动时三方合并到复制品，冲突保留复制品的版本，实例中的编辑写回

    复制品没改过的项换成来源的新版本（改映射、新增表规则、删除节点）；两边都改了的映射保留复制品的版本，记为冲突，
    GetPermissionScheme 的 source.last_merge 能读到。树和表规则变了，实例按新规则重新生成：实例中的编辑（加的表项、停用的行）带原 ID 写回，
    人删掉的规则行仍然删掉，临时凭证保留；节点已不存在的编辑和临时凭证不写回，逐条报告。来源记录推进到来源现在的规则，
    再启动一次什么也不做。
    """
    from types import SimpleNamespace

    from management_portal.permission_system.scheme import repository
    from management_portal.resources.service import ResourceService

    service = SimpleNamespace(permission_facts=facts)
    ResourceService.ensure_permission_instance(service)
    call("CreatePermissionScheme", scheme_id="team", source_scheme_id="default")
    call("ActivatePermissionScheme", scheme_id="team")
    mine = call("GetPermissionScheme", scheme_id="team")["rules"]
    mine["instructions"]["ListPosts"] = {"a": "@contributor", "r": "@shared"}
    call("UpdatePermissionScheme", scheme_id="team", rules=mine)
    kept = [call("CreatePermissionEntry", p="carol", a="@admin", r="@operations")["entry"],
            call("CreatePermissionEntry", p="carol", a="@viewer", r=LINEAGE)["entry"]]
    lost = call("CreatePermissionEntry", p="carol", a="@admin", r="@papers")["entry"]
    [reads] = call("ListPermissionEntries", p="@principals", a="@viewer", r="@shared")["entries"]
    disabled = call("UpdatePermissionEntry", entry_id=reads["entry_id"], disabled=True)["entry"]
    [messages] = call("ListPermissionEntries", p="@principals", a="@message", r="@shared")["entries"]
    call("DeletePermissionEntry", entry_id=messages["entry_id"])
    store = off.store.scoped("team")
    loans = [Entry(new_entry_id(), PERMIT, "carol", "@viewer", resource, issued_by="alice", issued_at=1.0, expires_at=4102444800.0)
             for resource in (LINEAGE, "@papers")]
    with store.transaction() as connection:
        store.add(connection, loans)

    released = repository.builtin_documents()
    default = released["default"]
    default["instructions"]["ListPosts"] = {"a": "@admin", "r": "@shared"}
    default["instructions"]["GetPost"] = {"a": "@contributor"}
    default["entries"].append({"id": "members_read_public", "effect": "permit", "p": "@members", "a": "@viewer", "r": "@public"})
    default["trees"]["R"]["nodes"] = [node for node in default["trees"]["R"]["nodes"] if node["id"] != "@papers"]
    for node in default["trees"]["R"]["nodes"]:
        if node["id"] == "@project_paper":
            node["otherwise"] = "@public"
    monkeypatch.setattr(repository, "builtin_documents", lambda: copy.deepcopy(released))

    report = ResourceService.merge_permission_sources(service)["team"]
    assert [conflict["key"] for conflict in report["conflicts"]] == ["instruction|ListPosts"]
    assert report["conflicts"][0]["own"] == mine["instructions"]["ListPosts"]
    assert sorted((item["entry_id"], item["missing"]) for item in report["unrestored"]) == sorted(
        [(lost["entry_id"], ["R"]), (loans[1].entry_id, ["R"])])
    rules_now = call("GetPermissionScheme", scheme_id="team")
    assert rules_now["rules"]["instructions"]["ListPosts"] == mine["instructions"]["ListPosts"]
    assert rules_now["rules"]["instructions"]["GetPost"] == default["instructions"]["GetPost"]
    assert "@papers" not in {node["id"] for node in rules_now["rules"]["trees"]["R"]["nodes"]}
    # This process loaded the built-in rules before the release; a restarted one compares with the released rules.
    assert PARPermissions().schemes.source("team")["outdated"] is False
    last = rules_now["source"]["last_merge"]
    assert [conflict["key"] for conflict in last["conflicts"]] == ["instruction|ListPosts"] and last["regenerated"] is True
    assert {item["entry_id"] for item in last["unrestored"]} == {lost["entry_id"], loans[1].entry_id}
    message_row = {"effect": "permit", "p": "@principals", "a": "@message", "r": "@shared"}
    assert report["deleted_again"] == last["deleted_again"] == [message_row] and last["merged_by"] == "portal"

    rows = {item["entry_id"]: item for item in call("ListPermissionEntries", scheme_id="team", limit=500, **EVERYTHING)["entries"]}
    assert all(entry["entry_id"] in rows for entry in kept) and lost["entry_id"] not in rows
    assert rows[disabled["entry_id"]]["disabled"] is True and rows[disabled["entry_id"]]["version"] == disabled["version"]
    assert ("permit", "@members", "@viewer", "@public") in contents("team")
    assert ("permit", "@principals", "@message", "@shared") not in contents("team")
    with store.read() as connection:
        remaining = {entry.entry_id for entry in store.entries(connection, where="expires_at IS NOT NULL")}
    assert remaining == {loans[0].entry_id}
    assert ResourceService.merge_permission_sources(service) == {}


def test_a_merge_saved_in_the_editor_keeps_hand_rows_like_an_automatic_one(off):
    """编辑器里带 source_digest 保存的合并和自动合并同一套写回，dry_run 报告不能写回的表项，保存后记为 last_merge

    来源删掉一个节点、加一条表规则后，复制品按三方合并的规则保存：实例中的编辑（加的表项、停用的行）带原 ID 写回，人删掉的规则行仍删掉并列在
    deleted_again，节点已不存在的编辑不写回、列在 unrestored；dry_run 报告同样的结果而不写入。不带 source_digest 的保存仍按 §5.3
    重新生成，实例中的编辑随之丢弃。
    """
    from management_portal.permission_system.scheme.merge import merge

    call("CreatePermissionScheme", scheme_id="base", rules=rules("base"))
    call("CreatePermissionScheme", scheme_id="team", source_scheme_id="base")
    kept = call("CreatePermissionEntry", scheme_id="team", p="carol", a="@admin", r="@operations")["entry"]
    lost = call("CreatePermissionEntry", scheme_id="team", p="carol", a="@admin", r="@papers")["entry"]
    [reads] = call("ListPermissionEntries", scheme_id="team", p="@principals", a="@viewer", r="@shared")["entries"]
    disabled = call("UpdatePermissionEntry", scheme_id="team", entry_id=reads["entry_id"], disabled=True)["entry"]
    [messages] = call("ListPermissionEntries", scheme_id="team", p="@principals", a="@message", r="@shared")["entries"]
    call("DeletePermissionEntry", scheme_id="team", entry_id=messages["entry_id"])

    changed = rules("base")
    changed["entries"].append({"id": "members_read_public", "effect": "permit", "p": "@members", "a": "@viewer", "r": "@public"})
    changed["trees"]["R"]["nodes"] = [node for node in changed["trees"]["R"]["nodes"] if node["id"] != "@papers"]
    for node in changed["trees"]["R"]["nodes"]:
        if node["id"] == "@project_paper":
            node["otherwise"] = "@public"
    call("UpdatePermissionScheme", scheme_id="base", rules=changed, expected_version=1)
    found = call("GetPermissionScheme", scheme_id="team")
    merged, conflicts = merge(found["source"]["rules"], call("GetPermissionScheme", scheme_id="base")["rules"], found["rules"])
    assert conflicts == []
    arguments = {"scheme_id": "team", "rules": merged, "source_digest": found["source"]["current_digest"]}
    message_row = {"effect": "permit", "p": "@principals", "a": "@message", "r": "@shared"}

    before = call("ListPermissionEntries", scheme_id="team", limit=500, **EVERYTHING)["entries"]
    preview = call("UpdatePermissionScheme", dry_run=True, **arguments)
    assert [(item["entry_id"], item["missing"]) for item in preview["unrestored"]] == [(lost["entry_id"], ["R"])]
    assert preview["deleted_again"] == [message_row]
    assert [item["entry"]["r"] for item in preview["diff"]["lost_edits"]["items"]] == ["@papers"]
    assert {"effect": "permit", "p": "@members", "a": "@viewer", "r": "@public", "disabled": False} in preview["diff"]["added"]["items"]
    assert call("ListPermissionEntries", scheme_id="team", limit=500, **EVERYTHING)["entries"] == before
    assert "last_merge" not in call("GetPermissionScheme", scheme_id="team")["source"]

    saved = call("UpdatePermissionScheme", expected_version=found["version"], **arguments)
    assert (saved["unrestored"], saved["deleted_again"]) == (preview["unrestored"], preview["deleted_again"])
    rows = {item["entry_id"]: item for item in call("ListPermissionEntries", scheme_id="team", limit=500, **EVERYTHING)["entries"]}
    assert kept["entry_id"] in rows and lost["entry_id"] not in rows
    assert rows[disabled["entry_id"]]["disabled"] is True and rows[disabled["entry_id"]]["version"] == disabled["version"]
    assert ("permit", "@members", "@viewer", "@public") in contents("team")
    assert ("permit", "@principals", "@message", "@shared") not in contents("team")
    last = call("GetPermissionScheme", scheme_id="team")["source"]["last_merge"]
    assert (last["merged_by"], last["conflicts"], last["deleted_again"]) == ("alice", [], [message_row])
    assert [item["entry_id"] for item in last["unrestored"]] == [lost["entry_id"]]

    plain = call("GetPermissionScheme", scheme_id="team")
    plain["rules"]["entries"] = [entry for entry in plain["rules"]["entries"] if entry.get("id") != "members_read_public"]
    call("UpdatePermissionScheme", scheme_id="team", rules=plain["rules"], expected_version=plain["version"])
    assert kept["entry_id"] not in {item["entry_id"] for item in
                                    call("ListPermissionEntries", scheme_id="team", limit=500, **EVERYTHING)["entries"]}


def test_a_merge_of_mappings_and_descriptions_leaves_the_instance_alone(off, monkeypatch):
    """来源只改了指令映射和说明时，合并只改规则，实例不动；等着写入的补齐发现实例已换规则时不写入

    合并后实例的每一行（包括 ID 和版本）都和合并前相同。补齐在锁外按读到的实例规则算好位置，拿到锁时实例已按别的规则生成，
    整次不写入并报 InstanceReplaced。
    """
    from types import SimpleNamespace

    from management_portal.permission_system.facts import InstanceReplaced
    from management_portal.permission_system.scheme import repository
    from management_portal.resources.service import ResourceService

    service = SimpleNamespace(permission_facts=facts)
    ResourceService.ensure_permission_instance(service)
    call("CreatePermissionScheme", scheme_id="team", source_scheme_id="default")
    call("CreatePermissionEntry", scheme_id="team", p="carol", a="@admin", r="@operations")
    before = call("ListPermissionEntries", scheme_id="team", limit=500, **EVERYTHING)["entries"]

    released = repository.builtin_documents()
    released["default"]["description"] = "发布改过的说明"
    released["default"]["instructions"]["GetPost"] = {"a": "@contributor"}
    monkeypatch.setattr(repository, "builtin_documents", lambda: copy.deepcopy(released))
    report = ResourceService.merge_permission_sources(service)["team"]
    assert (report["conflicts"], report["instance"], report["unrestored"]) == ([], None, [])
    merged = call("GetPermissionScheme", scheme_id="team")["rules"]
    assert merged["description"] == "发布改过的说明" and merged["instructions"]["GetPost"]["a"] == "@contributor"
    assert call("ListPermissionEntries", scheme_id="team", limit=500, **EVERYTHING)["entries"] == before

    instance = off.current()[2].__class__(off.schemes.compiled("team"), off.store)
    with pytest.raises(InstanceReplaced):
        instance.complete([*facts(), Fact("project", "LaterProject")], rules_digest="generated-from-other-rules")
    assert call("ListPermissionEntries", scheme_id="team", limit=500, **EVERYTHING)["entries"] == before


def test_completing_at_startup_and_hourly_repairs_missed_syncs_and_keeps_every_row(off, monkeypatch):
    """启动和每小时对启用方案补齐：补上提交后没执行的同步，保留全部表项，绝不重建；一次读少了对象时不写入

    提交后的同步是幂等的，重复执行不改变实例。补齐把漏掉的新对象按规则放入、把已删除的对象移出，实例中的编辑保留；
    没有改变时再跑一次什么也不做；将移出的对象多于上限时整次不写入并报错（事实很可能读少了），实例保持原样。
    """
    from types import SimpleNamespace

    from management_portal.permission_system.facts import TooManyRemoved
    from management_portal.resources.service import ResourceService

    call("CreatePermissionScheme", scheme_id="team", rules=rules())
    call("ActivatePermissionScheme", scheme_id="team")
    entry = call("CreatePermissionEntry", p="carol", a="@admin", r="@operations")["entry"]
    instance = off.current()[2]
    later = Fact("project", "LaterProject")
    assert instance.sync([later]) is True and instance.sync([later]) is False
    before = contents("team")

    missed = Fact("project", "MissedProject")
    live = [*facts(), later, missed]
    service = SimpleNamespace(permission_facts=lambda: live)
    report = ResourceService.complete_permission_instance(service)
    assert report["scheme_id"] == "team" and report["added_nodes"] == 2 and report["removed_nodes"] == 0
    assert ("permit", "MissedProject", "@admin", "MissedProject") in contents("team")
    assert before <= contents("team")
    again = ResourceService.complete_permission_instance(service)
    assert not any(again[name] for name in ("added_nodes", "moved_nodes", "removed_nodes", "added_entries", "removed_entries"))

    live.remove(missed)
    monkeypatch.setattr(ResourceService, "PERMISSION_COMPLETE_MAX_REMOVED", 1)
    after_missed = contents("team")
    live[:] = [user_fact("alice"), user_fact("carol")]
    with pytest.raises(TooManyRemoved):
        ResourceService.complete_permission_instance(service)
    assert contents("team") == after_missed
    live[:] = [*facts(), later]  # one object leaves, within the limit
    removed = ResourceService.complete_permission_instance(service)
    assert removed["removed_nodes"] == 2 and ("permit", "MissedProject", "@admin", "MissedProject") not in contents("team")
    assert [item["entry_id"] for item in call("ListPermissionEntries", p="carol", r="@operations")["entries"]] == [entry["entry_id"]]


def test_a_project_created_while_off_is_managed_by_its_creator_when_turned_on(off):
    """关闭时创建的 Project 在开启后由创建者管理

    创建者写在 Project 自己的登记记录里（created_by），权限系统不另存创建者；关闭时同步资源不维护实例。开启时从登记记录生成实例，
    实习用户创建者因“Project 创建人”得到 admin，记录里没有创建者的 Project 不给。
    """
    from management_portal import project_registry
    from management_portal.permission_system.facts import fact_from_record

    record = {"id": "NewProject", "status": "active", **permission_system.creation_fields("alice")}
    assert permission_system.sync_resource({**record, "resource_id": "NewProject"}, resource_type="project") is False
    with project_registry.mutate() as registry:
        registry["projects"].append(record)
    saved = next(item for item in project_registry.load()["projects"] if item["id"] == "NewProject")
    assert saved["created_by"] == "alice" and "created_for" not in saved

    built = PARPermissions()
    assert built.prepare_active([user_fact("alice", category="intern"), fact_from_record({**saved, "resource_id": "NewProject"}, "project"),
                                 Fact("project", "OtherProject")])["regenerate"] is True
    engine = built.current()[1]
    allowed = [decision.allowed for decision in engine.decide("alice", [("@admin", "NewProject"), ("@admin", "OtherProject")])]
    assert allowed == [True, False]


def test_a_resident_creating_for_a_users_input_creates_for_that_user(off, monkeypatch):
    """Resident 处理 User 的输入时创建的对象，这位 User 也是创建者

    端口从这次调用所属、仍在运行的执行取得直接输入的 User，写进新对象记录的 created_for，与 created_by 的 Resident 一起是创建者，
    不区分资源类型；没有所属执行时只写 Resident，没有操作者（导入）时两者都不写。开启生成实例与开启时增量同步一样从记录读取，
    实习用户因“Project 创建人”得到 admin。
    """
    from management_common.execution_association import execution_scope
    from management_portal.society_service import resident_work_origin
    from management_portal.permission_system.facts import fact_from_record

    resident = "resident_000000000001"
    running = {(resident, "execution-1"): "bob", (resident, "execution-2"): "carol"}
    monkeypatch.setattr(resident_work_origin, "execution_initiator",
                        lambda resident_id, execution_id: running.get((resident_id, execution_id), ""))
    with execution_scope("execution-1"):
        asked = permission_system.creation_fields(resident)
    assert asked == {"created_by": resident, "created_for": "bob"}
    assert permission_system.creation_fields(resident) == {"created_by": resident}
    assert permission_system.creation_fields("") == {}
    assert fact_from_record({"resource_id": "Asked:paper_000000000001", **asked}, "paper").creators == (resident, "bob")

    built = PARPermissions()
    objects = [user_fact("bob", category="intern"), user_fact("carol", category="intern"),
               Fact("resident", resident, {"lineage": "lineage_000000000001"}), Fact("lineage", "lineage_000000000001"),
               fact_from_record({"resource_id": "AskedProject", **asked}, "project"),
               fact_from_record({"resource_id": "SelfProject", **permission_system.creation_fields(resident)}, "project")]
    built.prepare_active(objects)
    with execution_scope("execution-2"):
        assert built.sync_resource({"resource_id": "LaterProject", **permission_system.creation_fields(resident)},
                                   resource_type="project") is True
    engine = built.current()[1]

    def admin(principal, resource):
        return engine.decide(principal, [("@admin", resource)])[0].allowed
    assert [admin("bob", "AskedProject"), admin("bob", "SelfProject"), admin("carol", "AskedProject")] == [True, False, False]
    assert admin(resident, "SelfProject") and admin("carol", "LaterProject") and not admin("bob", "LaterProject")


def test_activating_with_the_permission_system_on_switches_now(monkeypatch):
    """权限系统开启时启用方案立即补齐它的实例并切换

    启用在同一事务里补齐实例并记为启用；之后的判定按新方案，GetPermissionSystem 报告它。另一个进程中已加载原先启用方案（默认方案的副本）的实现，
    下一次判定从同一次读取得知启用的是新方案，加载它后作答，不抛出异常也不重试。修改启用方案的规则后判定随之重新加载。
    """
    permissions = enable(monkeypatch)
    try:
        permissions.users("admin")
        permissions.administrator("admin")
        elsewhere = Permissions(PARPermissions())
        assert elsewhere.holds("admin", "@admin", "@shared") and elsewhere.scheme.scheme_id == ACTIVE_COPY
        created = permission_system.manage("CreatePermissionScheme", {"scheme_id": "team", "rules": rules()}, actor="admin",
                                           collect_facts=lambda: [user_fact("admin")])
        assert created["version"] == 1
        activated = permission_system.manage("ActivatePermissionScheme", {"scheme_id": "team"}, actor="admin",
                                             collect_facts=lambda: [user_fact("admin"), Fact("project", PROJECT)])
        assert activated["activated"] is True and activated["regenerate"] is False and activated["added_nodes"] == 2
        assert permissions.implementation.schemes.active() == "team" and permissions.scheme.scheme_id == "team"
        assert permission_system.manage("GetPermissionSystem", {}, actor="admin")["scheme"]["scheme_id"] == "team"
        assert permissions.holds("admin", "@admin", PROJECT)
        scheme, _decisions = elsewhere.implementation._decide("admin", [("@admin", PROJECT)])
        assert scheme.scheme_id == "team" and _decisions[0].allowed

        changed = rules()
        changed["entries"][position(changed, "administrators")]["a"] = "@viewer"
        permission_system.manage("UpdatePermissionScheme", {"scheme_id": "team", "rules": changed}, expected_version=1,
                                 actor="admin", collect_facts=lambda: [user_fact("admin", category="portal_admin"), Fact("project", PROJECT)])
        assert permissions.implementation.schemes.version("team") == 2
        assert not permissions.holds("admin", "@admin", PROJECT) and permissions.holds("admin", "@viewer", PROJECT)
        assert not elsewhere.holds("admin", "@admin", PROJECT)
    finally:
        permission_system.forget_implementation()


def test_a_split_or_joined_instruction_names_its_elements_in_the_error(caplog):
    """指令的判定槽增减后，旧元素名的编译错误和加载时的警告写出这条指令现在的元素；同一指令的判定槽不能重名

    单槽指令的元素用指令名，多槽指令用“指令:参数或固定名”。方案里还写着旧元素名时，错误路径是
    instructions.<旧元素>，说明列出现在的元素，需要重新分类；加载已保存的规则时跳过旧元素的分类，警告同样列出现在的元素；
    声明两个同名判定槽的指令在声明时就被拒绝。
    """
    import logging

    from management_portal.hatg.declarations import Fixed, InstructionDeclaration, On
    from management_portal.permission_system.scheme import repository
    from management_portal.permission_system.scheme.compiler import SchemeError, compile_scheme

    document = copy.deepcopy(builtin_documents()["default"])
    instructions = document["instructions"]
    instructions["CreateTemplate"] = instructions.pop("CreateTemplate:@public")
    instructions["GetResident:resident_id"] = instructions.pop("GetResident")
    with pytest.raises(SchemeError) as failed:
        compile_scheme(document)
    messages = {item["path"]: item["message"] for item in failed.value.errors}
    assert "CreateTemplate:@public、CreateTemplate:from_selection.lineage_id" in messages["instructions.CreateTemplate"]
    assert "GetResident 的元素现在是 GetResident" in messages["instructions.GetResident:resident_id"]
    # Loading saved rules skips the old elements; the warning names what they are now.
    with caplog.at_level(logging.WARNING, logger=repository.LOGGER.name):
        kept = repository.installed({**document, "scheme_id": "renamed_case"})
    assert "CreateTemplate" not in kept["instructions"] and "GetResident:resident_id" not in kept["instructions"]
    assert "CreateTemplate now has CreateTemplate:@public, CreateTemplate:from_selection.lineage_id" in caplog.text
    assert "GetResident now has GetResident" in caplog.text
    with pytest.raises(RuntimeError, match="判定槽重名"):
        InstructionDeclaration(name="Twice", category="permission", owner="resource", description="",
                               decides=(Fixed("fixed"), On("fixed")))
