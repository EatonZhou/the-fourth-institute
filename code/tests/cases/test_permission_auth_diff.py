"""授权差报告 (decision_b820faf42218 §5.12, thinking_3cbdd375ebf8 §14.4).

Saving rules, merging a source's changes and activating a scheme report what
principals gain and lose on the current objects, summed up into boxes of
(P node, A levels, R node) with the number of principals and resources each
holds.  The report only informs; nothing is refused.
"""
import copy

import pytest

from management_portal import permission_system
from management_portal.permission_system import authdiff
from management_portal.permission_system.facts import Fact, user_fact
from management_portal.permission_system.par import PARPermissions
from management_portal.permission_system.scheme.repository import builtin_documents

TEAM, OTHER = "Team", "Other"
LINEAGE, OTHER_LINEAGE = "lineage_aaaaaaaaaaaa", "lineage_bbbbbbbbbbbb"
WORKBENCHES = [f"workbench_00000000000{index}" for index in range(1, 6)]


def facts():
    return [user_fact("root", category="portal_admin"), user_fact("alice"), user_fact("sample_intern", category="intern"),
            user_fact("bob", category="intern"),
            Fact("project", TEAM), Fact("project", OTHER),
            Fact("lineage", LINEAGE, {"project": TEAM}), Fact("lineage", OTHER_LINEAGE, {"project": OTHER}),
            Fact("resident", "resident_000000000001", {"lineage": LINEAGE}),
            Fact("resident", "resident_000000000002", {"lineage": OTHER_LINEAGE}),
            *(Fact("workbench", workbench, {"project": TEAM}) for workbench in WORKBENCHES)]


@pytest.fixture
def off(monkeypatch):
    implementation = PARPermissions()
    monkeypatch.setattr(permission_system, "configured_mode", lambda: permission_system.OPEN)
    monkeypatch.setattr(permission_system, "_IMPLEMENTATION", implementation)
    yield implementation
    permission_system.forget_implementation()


def call(name, expected_version=None, **arguments):
    return permission_system.manage(name, arguments, actor="alice", collect_facts=facts, expected_version=expected_version)


def rules(scheme_id, *, workbench_under_project=True, without=()):
    document = copy.deepcopy(builtin_documents()["default"])
    document.update({"scheme_id": scheme_id, "title": scheme_id})
    if not workbench_under_project:
        document["trees"]["R"]["nodes"] = [node for node in document["trees"]["R"]["nodes"] if node["id"] != "@project_workbench"]
    document["entries"] = [entry for entry in document["entries"] if entry.get("id") not in without]
    return document


def boxes(report):
    return [(item["kind"], item["p"], item["r"], item["before"], item["after"], item["principals"], item["resources"])
            for item in report["groups"]["items"]]


def test_a_merge_reports_the_workbenches_moving_under_their_project(off):
    """来源把 Workbench 移到所属 Project 下，合并的 dry_run、保存结果和 last_merge 都报告同一份授权差

    原来没有 Lineage 的 Workbench 在“公共内容”下，人人可管理；移到 Project 下以后，sample_intern 凭实例中加的表项保留协作，
    不属于该 Project 的其他主体（实习用户 bob、别的 Project 的 Resident）降为查看与发消息，Portal 管理员、一般用户和
    Project 内的 Resident 不变。报告按盒子汇总，不展开到三元组，每组带主体数和资源数；diff_limit 限制列出的组数。
    """
    from management_portal.permission_system.scheme.merge import merge

    call("CreatePermissionScheme", scheme_id="base", rules=rules("base", workbench_under_project=False))
    call("CreatePermissionScheme", scheme_id="team", source_scheme_id="base")
    call("CreatePermissionEntry", scheme_id="team", p="sample_intern", a="@contributor", r=TEAM)
    call("UpdatePermissionScheme", scheme_id="base", rules=rules("base"), expected_version=1)
    found = call("GetPermissionScheme", scheme_id="team")
    merged, _conflicts = merge(found["source"]["rules"], call("GetPermissionScheme", scheme_id="base")["rules"], found["rules"])
    arguments = {"scheme_id": "team", "rules": merged, "source_digest": found["source"]["current_digest"]}

    preview = call("UpdatePermissionScheme", dry_run=True, **arguments)["auth_diff"]
    team_workbenches = {"node": TEAM, "kinds": ["workbench"]}
    assert boxes(preview) == [
        ("tightening", {"node": "@principals", "title": "全部主体", "except": ["@members", "@portal_admins", TEAM, "sample_intern"],
                        "except_total": 4}, team_workbenches, ["@admin"], ["@viewer", "@message"], 2, 5),
        ("tightening", {"node": "sample_intern"}, team_workbenches, ["@admin"], ["@contributor"], 1, 5),
    ]
    assert preview["groups"]["items"][1]["lost"] == ["@admin"] and preview["groups"]["items"][1]["gained"] == []
    assert (preview["kind"], preview["complete"], preview["groups"]["total"]) == ("tightening", True, 2)
    assert preview["gained"] == {"triples": 0, "principals": 0, "resources": 0}
    assert preview["lost"]["principals"] == 3 and preview["lost"]["resources"] == 5
    assert preview["lost"]["triples"] == 2 * 5 * 2 + 1 * 5 * 1

    limited = call("UpdatePermissionScheme", dry_run=True, diff_limit=1, **arguments)["auth_diff"]
    assert limited["groups"]["total"] == 2 and boxes(limited) == boxes(preview)[:1]

    saved = call("UpdatePermissionScheme", expected_version=found["version"], **arguments)
    assert saved["auth_diff"] == preview
    assert call("GetPermissionScheme", scheme_id="team")["source"]["last_merge"]["auth_diff"] == preview


def test_saving_and_activating_report_against_the_instance_they_replace(off):
    """保存规则对比被替换的实例，启用方案对比启用中方案的实例；dry_run 与正式结果相同

    去掉“一般用户管理全部 Project”后保存，一般用户 alice 在全部 Project 上从管理降为查看与发消息（收紧）；
    启用这个方案相对启用中的方案同样收紧，再启用一次是中性。只改指令映射时实例不变，授权仍随移动的元素改变，并列出要求改变的指令。
    """
    call("CreatePermissionScheme", scheme_id="team", rules=rules("team"))
    call("ActivatePermissionScheme", scheme_id="team")
    call("CreatePermissionScheme", scheme_id="strict", rules=rules("strict"))
    strict = rules("strict", without={"members_manage_projects"})
    projects = ("tightening", {"node": "alice"}, {"node": "@projects", "title": "全部 Project"}, ["@admin"], ["@viewer", "@message"])

    preview = call("UpdatePermissionScheme", scheme_id="strict", rules=strict, dry_run=True)["auth_diff"]
    assert [item[:5] for item in boxes(preview)] == [projects] and preview["kind"] == "tightening"
    assert call("UpdatePermissionScheme", scheme_id="strict", rules=strict, expected_version=1)["auth_diff"] == preview

    activating = call("ActivatePermissionScheme", scheme_id="strict", dry_run=True)
    assert activating["activated"] is False and activating["auth_diff"] == preview
    assert call("ActivatePermissionScheme", scheme_id="strict")["auth_diff"] == preview
    again = call("ActivatePermissionScheme", scheme_id="strict")["auth_diff"]
    assert (again["kind"], again["groups"]["total"], again["remapped"]["total"]) == ("neutral", 0, 0)

    remapped = copy.deepcopy(strict)
    remapped["instructions"]["ListPosts"] = {"a": "@admin", "r": "@shared"}
    report = call("UpdatePermissionScheme", scheme_id="strict", rules=remapped, dry_run=True)
    assert report["regenerates"] is False and report["auth_diff"]["kind"] == "tightening"
    assert [item["instruction"] for item in report["auth_diff"]["remapped"]["items"]] == ["ListPosts"]
    assert report["auth_diff"]["remapped"]["items"][0]["after"] == [{"element": "ListPosts", "a": "@admin", "resource": "@shared"}]


def test_moving_an_instruction_element_reports_what_it_grants_and_takes_away(off):
    """改指令分类是移动 A 元素，授权随之改变：按元素报告得失，与实例变化的组一起计入类别和 diff_limit

    把 DeleteLineage 从 @admin 改到 @viewer，实例不变：能查看 Lineage 但不能管理它的主体获得这条指令，报告放宽，
    每组都只有这个元素，资源只算 Lineage；保存结果与 dry_run 相同，remapped 照常列出。
    固定槽 ListPosts 从 @viewer 改到 @admin 时，对“共享”没有 @admin 的主体失去它（收紧），资源是固定槽的 R 节点。
    两处一起改报告混合；实例同时改变时，实例变化的组和元素的组一起排序、受 diff_limit 限制。
    """
    call("CreatePermissionScheme", scheme_id="team", rules=rules("team"))
    looser = rules("team")
    looser["instructions"]["DeleteLineage"] = {"a": "@viewer"}

    preview = call("UpdatePermissionScheme", scheme_id="team", rules=looser, dry_run=True)
    report = preview["auth_diff"]
    assert preview["regenerates"] is False
    assert report["kind"] == "loosening" and report["complete"] is True
    assert report["lost"] == {"triples": 0, "principals": 0, "resources": 0}
    items = report["groups"]["items"]
    assert items and report["groups"]["total"] == len(items)
    for item in items:
        assert (item["kind"], item["element"], item["gained"], item["lost"]) == ("loosening", "DeleteLineage", ["DeleteLineage"], [])
        assert (item["before"], item["after"]) == ([], ["DeleteLineage"])
        assert item["a"] == {"before": "@admin", "after": "@viewer"} and "resource" not in item
    # sample_intern, bob and the other Project's Resident may view each Lineage but not manage it.
    everyone = {"node": "@principals", "title": "全部主体"}
    assert boxes(report) == [
        ("loosening", {**everyone, "except": ["@members", "@portal_admins", OTHER], "except_total": 3},
         {"node": OTHER_LINEAGE, "kinds": ["lineage"]}, [], ["DeleteLineage"], 3, 1),
        ("loosening", {**everyone, "except": ["@members", "@portal_admins", TEAM], "except_total": 3},
         {"node": LINEAGE, "kinds": ["lineage"]}, [], ["DeleteLineage"], 3, 1),
    ]
    assert report["gained"] == {"triples": 6, "principals": 4, "resources": 2}
    assert [item["instruction"] for item in report["remapped"]["items"]] == ["DeleteLineage"]
    assert call("UpdatePermissionScheme", scheme_id="team", rules=looser, expected_version=1)["auth_diff"] == report

    stricter = copy.deepcopy(looser)
    stricter["instructions"]["ListPosts"] = {"a": "@admin", "r": "@shared"}
    posts = call("UpdatePermissionScheme", scheme_id="team", rules=stricter, dry_run=True)["auth_diff"]
    assert posts["kind"] == "tightening" and posts["gained"]["triples"] == 0
    for item in posts["groups"]["items"]:
        assert (item["element"], item["lost"], item["resources"]) == ("ListPosts", ["ListPosts"], 1)
        assert item["r"]["node"] == "@shared" and item["resource"] == {"before": "@shared", "after": "@shared"}
        assert item["a"] == {"before": "@viewer", "after": "@admin"}
    assert [item["p"] for item in posts["groups"]["items"]] == [{**everyone, "except": ["@portal_admins"], "except_total": 1}]
    assert posts["lost"] == {"triples": 5, "principals": 5, "resources": 0}

    both = copy.deepcopy(rules("team"))
    both["instructions"]["DeleteLineage"] = {"a": "@viewer"}
    both["instructions"]["ListPosts"] = {"a": "@admin", "r": "@shared"}
    call("UpdatePermissionScheme", scheme_id="team", rules=rules("team"), expected_version=2)
    mixed = call("UpdatePermissionScheme", scheme_id="team", rules=both, dry_run=True)["auth_diff"]
    assert mixed["kind"] == "mixed" and {item["element"] for item in mixed["groups"]["items"]} == {"DeleteLineage", "ListPosts"}

    changing = rules("team", without={"members_manage_projects"})
    changing["instructions"]["ListPosts"] = {"a": "@admin", "r": "@shared"}
    together = call("UpdatePermissionScheme", scheme_id="team", rules=changing, dry_run=True)["auth_diff"]
    assert together["groups"]["total"] == len(together["groups"]["items"])
    assert {item.get("element") for item in together["groups"]["items"]} == {None, "ListPosts"}
    sizes = [item["principals"] * item["resources"] * (len(item["gained"]) + len(item["lost"]))
             for item in together["groups"]["items"]]
    assert sizes == sorted(sizes, reverse=True)
    limited = call("UpdatePermissionScheme", scheme_id="team", rules=changing, dry_run=True, diff_limit=1)["auth_diff"]
    assert limited["groups"]["total"] == together["groups"]["total"] and limited["groups"]["items"] == together["groups"]["items"][:1]


def test_moved_place_and_via_elements_count_only_what_they_can_judge(off):
    """移动的 place 槽和 via 槽只计它们实际能判定的资源

    place 槽判定新对象将落下的位置：它的所属对象（这里是 Project），或没有所属对象时的分类节点，所以不计 Lineage、
    User 账号等对象。via 槽判定记录字段指向的对象：回执的 lineage 只指向 Lineage。
    """
    call("CreatePermissionScheme", scheme_id="team", rules=rules("team"))
    moved = rules("team")
    moved["instructions"]["RegisterResource"] = {"a": "@viewer"}
    moved["instructions"]["GetInstructionExecution:instruction_id"] = {"a": "@contributor"}
    report = call("UpdatePermissionScheme", scheme_id="team", rules=moved, dry_run=True, diff_limit=100)["auth_diff"]
    counted = {}
    for item in report["groups"]["items"]:
        counted.setdefault(item["element"], {})[item["r"]["node"]] = (item["kind"], item["resources"])
    placed = counted["RegisterResource"]
    assert placed[TEAM] == placed[OTHER] == ("loosening", 1)
    assert all(node.startswith("@") or node in (TEAM, OTHER) for node in placed)
    judged = counted["GetInstructionExecution:instruction_id"]
    assert judged == {LINEAGE: ("tightening", 1), OTHER_LINEAGE: ("tightening", 1)}


def test_the_startup_merge_records_its_report_in_the_last_merge(off, monkeypatch):
    """发布改变内置规则后，启动时的自动合并把授权差写进 last_merge

    发布删掉“一般用户管理全部 Project”，复制品合并后 last_merge.auth_diff 报告收紧，与合并报告相同。
    """
    from types import SimpleNamespace

    from management_portal.permission_system.scheme import repository
    from management_portal.resources.service import ResourceService

    service = SimpleNamespace(permission_facts=facts)
    ResourceService.ensure_permission_instance(service)
    call("CreatePermissionScheme", scheme_id="team", source_scheme_id="default")
    released = repository.builtin_documents()
    released["default"]["entries"] = [entry for entry in released["default"]["entries"]
                                      if entry.get("id") != "members_manage_projects"]
    monkeypatch.setattr(repository, "builtin_documents", lambda: copy.deepcopy(released))

    report = ResourceService.merge_permission_sources(service)["team"]["auth_diff"]
    assert report["kind"] == "tightening" and [item["p"] for item in report["groups"]["items"]] == [{"node": "alice"}]
    assert call("GetPermissionScheme", scheme_id="team")["source"]["last_merge"]["auth_diff"] == report


def test_a_release_that_only_reclassifies_records_what_the_moved_elements_change(off, monkeypatch):
    """发布只改默认方案的指令分类时，内置方案跟随发布记一条 last_merge，复制品的合并同样带授权差，实例不动

    树和表项不变，实例不需要重新生成；内置方案的基准推进到新规则，last_merge 记下移动元素的授权差（merged_by portal），
    再次启动无事可做。只改说明时基准同样推进，不记 last_merge。复制品自动合并只有分类变化时，last_merge 也带同样的授权差。
    """
    from types import SimpleNamespace

    from management_portal.permission_system.scheme import repository
    from management_portal.resources.service import ResourceService

    service = SimpleNamespace(permission_facts=facts)
    ResourceService.ensure_permission_instance(service)
    call("CreatePermissionScheme", scheme_id="team", source_scheme_id="default")
    instance = {item["entry_id"] for item in call("ListPermissionEntries", scheme_id="default", r="@portal", r_scope="subtree", limit=500)["entries"]}

    retitled = repository.builtin_documents()
    retitled["default"]["description"] = "新的说明"
    monkeypatch.setattr(repository, "builtin_documents", lambda: copy.deepcopy(retitled))
    monkeypatch.setattr(permission_system, "_IMPLEMENTATION", PARPermissions())
    assert PARPermissions().stale_builtin() == ["default"]
    quiet = ResourceService.ensure_permission_instance(service)["default"]
    assert quiet["instance"] is None and quiet["auth_diff"]["kind"] == "neutral"
    assert call("GetPermissionScheme", scheme_id="default").get("last_merge") is None
    assert PARPermissions().stale_builtin() == []

    released = copy.deepcopy(retitled)
    released["default"]["instructions"]["DeleteLineage"] = {"a": "@viewer"}
    monkeypatch.setattr(repository, "builtin_documents", lambda: copy.deepcopy(released))
    monkeypatch.setattr(permission_system, "_IMPLEMENTATION", PARPermissions())
    assert PARPermissions().stale_builtin() == ["default"]
    report = ResourceService.ensure_permission_instance(service)["default"]
    assert report["instance"] is None
    assert report["auth_diff"]["kind"] == "loosening"
    assert {item["element"] for item in report["auth_diff"]["groups"]["items"]} == {"DeleteLineage"}
    merged = call("GetPermissionScheme", scheme_id="default")["last_merge"]
    assert (merged["merged_by"], merged["to_digest"]) == ("portal", repository.source_digest(released["default"]))
    assert merged["auth_diff"] == report["auth_diff"] and merged["regenerated"] is False
    assert {item["entry_id"] for item in call("ListPermissionEntries", scheme_id="default", r="@portal", r_scope="subtree", limit=500)["entries"]} == instance
    assert ResourceService.ensure_permission_instance(service) is None

    copied = ResourceService.merge_permission_sources(service)["team"]["auth_diff"]
    assert copied["kind"] == "loosening" and {item["element"] for item in copied["groups"]["items"]} == {"DeleteLineage"}
    copy_merge = call("GetPermissionScheme", scheme_id="team")["source"]["last_merge"]
    assert copy_merge["auth_diff"] == copied and copy_merge["regenerated"] is False


def test_a_release_that_turns_a_fixed_slot_into_an_argument_slot_follows_with_its_report(off, monkeypatch):
    """发布把固定槽改成参数槽时，内置方案照常跟随发布，last_merge 记下这个元素的授权差

    RunScript 原先在“Portal 运维”上判定 @admin，发布改为对参数 lineage_id 判定 @admin。基准仍按它当时的分类
    比较（固定在 @operations），跟随发布推进基准，记下放宽：Project 成员得到自己 Lineage 上的 RunScript，
    资源从 @operations 变为参数所指对象。仍给这个元素写 R 节点的保存规则照样载入，只是新规则不接受它。
    """
    from types import SimpleNamespace

    from management_portal.permission_system.scheme import repository
    from management_portal.permission_system.scheme.compiler import SchemeError
    from management_portal.resources.service import ResourceService

    service = SimpleNamespace(permission_facts=facts)
    ResourceService.ensure_permission_instance(service)
    instance = {item["entry_id"] for item in call("ListPermissionEntries", scheme_id="default", r="@portal", r_scope="subtree", limit=500)["entries"]}
    stale = repository.builtin_documents()["default"]
    stale["instructions"]["RunScript"] = {"a": "@admin", "r": "@operations"}
    with off.store.transaction() as connection:
        off.store.scoped("default").set_basis(connection, stale, replace=True)
    monkeypatch.setattr(permission_system, "_IMPLEMENTATION", PARPermissions())
    assert PARPermissions().stale_builtin() == ["default"]

    report = ResourceService.ensure_permission_instance(service)["default"]
    assert report["instance"] is None and report["auth_diff"]["kind"] == "loosening"
    items = report["auth_diff"]["groups"]["items"]
    assert items and {item["element"] for item in items} == {"RunScript"}
    for item in items:
        assert item["a"] == {"before": "@admin", "after": "@admin"} and item["resource"] == {"before": "@operations", "after": ""}
    assert {item["r"]["node"] for item in items} <= {LINEAGE, OTHER_LINEAGE, TEAM, OTHER}
    merged = call("GetPermissionScheme", scheme_id="default")["last_merge"]
    assert (merged["merged_by"], merged["regenerated"], merged["auth_diff"]) == ("portal", False, report["auth_diff"])
    assert {item["entry_id"] for item in call("ListPermissionEntries", scheme_id="default", r="@portal", r_scope="subtree", limit=500)["entries"]} == instance
    assert ResourceService.ensure_permission_instance(service) is None

    loaded = off.schemes.compile(stale, saved=True)
    assert loaded.rules_digest == off.schemes.compiled("default").rules_digest
    with pytest.raises(SchemeError, match="RunScript"):
        off.schemes.compile(stale)


def test_the_report_stops_at_its_time_budget(off):
    """计算超过时间上限时停止，报告 complete: false"""
    call("CreatePermissionScheme", scheme_id="team", rules=rules("team"))
    scheme = off.schemes.compiled("team")
    store = off.store.scoped("team")
    before = authdiff.View.stored(scheme, store)
    after = authdiff.View.generated(off.schemes.compile(rules("team", without={"members_manage_projects"})), store,
                                    permission_system.facts.flatten(facts()))
    assert authdiff.compare(before, after, limit=50)["complete"] is True
    assert authdiff.compare(before, after, limit=50, budget=0)["complete"] is False
