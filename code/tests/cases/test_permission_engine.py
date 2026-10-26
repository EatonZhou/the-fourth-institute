"""The PAR permission engine, scheme compiler and fact adapter (decision_b820faf42218, decision_600ae27b0538).

The engine only knows trees, entries and stored element paths; the default
scheme places Portal objects by their owners, and its instance holds the rows
its rules give those objects.
"""
import copy
import json
import time
from dataclasses import replace

import pytest

from management_portal.permission_system.engine import Engine
from management_portal.permission_system.engine.model import DENY, PERMIT, Entry
from management_portal.permission_system.engine.store import Store, new_entry_id
from management_portal.permission_system.facts import Fact, Instance, fact_from_record, user_fact
from management_portal.permission_system.scheme.compiler import SchemeError, compile_scheme
from management_portal.permission_system.scheme.repository import builtin_documents

PROJECT = "AI员工"
OTHER_PROJECT = "RobotLab"
LINEAGE = "lineage_111111111111"
OTHER_LINEAGE = "lineage_222222222222"
RESIDENT = "resident_111111111111"
OTHER = "resident_222222222222"
STEWARD = "resident_333333333333"
FAMILY = "family_111111111111"
DECISION = "decision_111111111111"


@pytest.fixture
def store(isolated_portal_case):
    with isolated_portal_case():
        yield Store()


@pytest.fixture
def default():
    return compile_scheme(builtin_documents()["default"])


def engine_for(scheme, store, **options):
    return Engine(scheme.trees, store.scoped(scheme.scheme_id), **options)


def build(scheme, store, facts, administrators=(), interns=()):
    """Users are 一般用户 (members manage every Project) unless named Portal 管理员 or 实习用户 here."""
    instance = Instance(scheme, store)
    instance.generate(categorized(facts, administrators, interns))
    return instance


def categorized(facts, administrators=(), interns=()):
    """The facts with the named Users' account records saying their category."""
    category = {**{name: "portal_admin" for name in administrators}, **{name: "intern" for name in interns}}
    return [replace(fact, category=category[fact.id]) if fact.kind == "user" and fact.id in category else fact for fact in facts]


def rows(store, where="TRUE", parameters=()):
    with store.read() as connection:
        return {(entry.p, entry.a, entry.r) for entry in store.entries(connection, where=where, parameters=parameters)}


def portal_facts():
    return [
        user_fact("alice"), user_fact("bob"),
        Fact("project", PROJECT), Fact("project", OTHER_PROJECT),
        Fact("lineage", LINEAGE, {"project": PROJECT}, creator="alice"),
        Fact("lineage", OTHER_LINEAGE, {"project": OTHER_PROJECT}),
        Fact("resident", RESIDENT, {"lineage": LINEAGE}, creator="alice",
             dependents=(Fact("workspace", "workspace_111111111111", {"resident": RESIDENT}),)),
        Fact("resident", OTHER, {"lineage": OTHER_LINEAGE}),
        Fact("resident", STEWARD, {"lineage": LINEAGE}),
        Fact("family", FAMILY, {"lineage": OTHER_LINEAGE}, steward=STEWARD),
        Fact("decision", DECISION, {"project": PROJECT}),
    ]


def levels(engine, scheme, principal, resource):
    """The widest of the default scheme's A nodes @admin ⊃ @contributor ⊃ @viewer the principal holds on ``resource``."""
    names = [name for name in ("admin", "contributor", "viewer")]
    decisions = engine.decide(principal, [(f"@{name}", resource) for name in names])
    return next((name for name, decision in zip(names, decisions) if decision.allowed), "none")


def test_default_scheme_places_objects_by_owner(store, default):
    """默认方案按归属把对象放进 R 树和 P 树

    Project 都在“全部 Project”下，Lineage 在所属 Project 下，Resident 在所在 Lineage 下，自有 Workspace 在 Resident 下；
    工作台在所属 Project 下，没有 Project 时在所在 Lineage 下；不属于 Project 的知识条目和既无 Project 也无 Lineage 的工作台在“公共内容”下；
    不属于 Project 的 Lineage（只有个人 AMA）及其 Resident 在“共享”下，既不在“全部 Project”也不在“公共内容”下；P 树中 Resident 也按 Project、Lineage 分层，User 按分类放在 Portal 管理员、一般用户或实习用户下，个人空间在“个人”下。
    """
    loose = [Fact("lineage", "lineage_333333333333"), Fact("resident", "resident_444444444444", {"lineage": "lineage_333333333333"}),
             Fact("skill", "skill_111111111111"), Fact("workbench", "workbench_111111111111"),
             Fact("workbench", "workbench_222222222222", {"project": OTHER_PROJECT, "lineage": LINEAGE}),
             Fact("workbench", "workbench_333333333333", {"lineage": LINEAGE})]
    build(default, store, [*portal_facts(), *loose])
    paths = store.paths("R", [LINEAGE, RESIDENT, OTHER, "workspace_111111111111", DECISION, "alice", "personal_space:alice",
                              "resident_444444444444", "skill_111111111111", "workbench_111111111111",
                              "workbench_222222222222", "workbench_333333333333"])
    assert paths["workbench_222222222222"] == ["@portal", "@shared", "@projects", OTHER_PROJECT, "workbench_222222222222"]
    assert paths["workbench_333333333333"] == ["@portal", "@shared", "@projects", PROJECT, LINEAGE, "workbench_333333333333"]
    assert paths[RESIDENT] == ["@portal", "@shared", "@projects", PROJECT, LINEAGE, RESIDENT]
    assert paths[OTHER] == ["@portal", "@shared", "@projects", OTHER_PROJECT, OTHER_LINEAGE, OTHER]
    assert paths["resident_444444444444"] == ["@portal", "@shared", "lineage_333333333333", "resident_444444444444"]
    assert paths["skill_111111111111"] == ["@portal", "@shared", "@public", "skill_111111111111"]
    assert paths["workbench_111111111111"] == ["@portal", "@shared", "@public", "workbench_111111111111"]
    assert paths["workspace_111111111111"][-2:] == [RESIDENT, "workspace_111111111111"]
    assert paths[DECISION] == ["@portal", "@shared", "@projects", PROJECT, DECISION]
    assert paths["alice"] == ["@portal", "@shared", "alice"]
    assert paths["personal_space:alice"] == ["@portal", "@personal", "personal_space:alice"]
    assert store.paths("P", [RESIDENT])[RESIDENT] == ["@principals", "@residents", PROJECT, LINEAGE, RESIDENT]
    assert store.paths("P", ["alice"]) == {"alice": ["@principals", "@users", "@members", "alice"]}


def test_default_scheme_rules(store, default):
    """默认方案的表规则：Project 成员、一般用户、公共内容、steward、管理员、全员可读与个人空间

    Project 的 Resident 对本 Project 下的全部资源（知识、Lineage、Resident，包括它自己）有 admin，对其他 Project 只能读；
    一般用户对全部 Project 有 admin，实习用户只能读；
    不属于 Project 的知识条目是公共内容，所有主体都有 admin；不属于 Project 的 Lineage 只有全员可读；创建者对自己创建的知识条目或 Lineage 没有额外权限；
    steward 对 Family 所在 Lineage 有 admin；管理员覆盖“共享”但不覆盖个人空间；所有主体可读共享资源；账号和个人空间只属于本人。
    判定报告命中的表项内容，表项本身不记录来自哪条规则。
    """
    loose = [Fact("lineage", "lineage_333333333333"), Fact("skill", "skill_111111111111", creator="alice"), user_fact("carol")]
    build(default, store, [*portal_facts(), *loose], administrators=["bob"], interns=["alice"])
    engine = engine_for(default, store)
    for resource in (PROJECT, DECISION, LINEAGE, RESIDENT, OTHER_LINEAGE):
        assert levels(engine, default, "carol", resource) == "admin", resource
    permit = engine.decide("carol", [("@admin", "@projects")])[0].permit
    assert (permit.p, permit.a, permit.r) == ("@members", "@admin", "@projects")
    assert levels(engine, default, "carol", "lineage_333333333333") == "viewer"
    assert levels(engine, default, "carol", "personal_space:alice") == "none"
    assert levels(engine, default, RESIDENT, PROJECT) == "admin"
    assert levels(engine, default, RESIDENT, DECISION) == "admin"
    assert levels(engine, default, RESIDENT, LINEAGE) == "admin"
    assert levels(engine, default, RESIDENT, RESIDENT) == "admin"
    assert levels(engine, default, RESIDENT, STEWARD) == "admin"
    assert levels(engine, default, RESIDENT, OTHER_LINEAGE) == "viewer"
    assert levels(engine, default, OTHER, OTHER_LINEAGE) == "admin"
    assert levels(engine, default, OTHER, PROJECT) == "viewer"
    for principal in ("alice", RESIDENT, OTHER):
        assert levels(engine, default, principal, "skill_111111111111") == "admin"
        assert levels(engine, default, principal, "lineage_333333333333") == "viewer"
    assert levels(engine, default, "alice", DECISION) == "viewer"
    assert levels(engine, default, "alice", LINEAGE) == "viewer"
    assert levels(engine, default, "alice", RESIDENT) == "viewer"
    assert levels(engine, default, STEWARD, OTHER_LINEAGE) == "admin"
    assert levels(engine, default, "bob", OTHER_LINEAGE) == "admin"
    assert levels(engine, default, "bob", "personal_space:alice") == "none"
    assert levels(engine, default, "alice", "personal_space:alice") == "admin"
    assert levels(engine, default, "alice", "alice") == "admin"
    assert levels(engine, default, "alice", "bob") == "viewer"
    administration = engine.decide("bob", [("UpdatePermissionScheme", "@shared")])[0]
    assert administration.allowed and administration.permit.p == "@portal_admins"
    assert not engine.decide("alice", [("UpdatePermissionScheme", "@shared")])[0].allowed
    assert all(decision.allowed for decision in engine.decide("someone", [("GetPortalStatus", "@shared"), ("MarkMailboxItemRead", "@portal")]))


def test_everyone_may_message_shared_residents(store, default):
    """默认方案允许所有主体给共享区的 Resident 发消息

    发消息的指令挂在 A 树“给 Resident 发消息”节点下，全员对“共享”有这一动作：不相干的 User 和其他 Lineage 的 Resident
    可以投递、转发和回答请求，但不因此得到 contributor；个人空间里的对象不受这条规则覆盖。
    """
    build(default, store, portal_facts(), interns=["bob"])
    engine = engine_for(default, store)
    for element in ("DeliverMessage:resident_id", "DeliverMessage:user_id", "ForwardMessages:target_resident_id",
                    "AnswerResidentRequest"):
        assert default.trees["A"].nodes[element].parent == "@message"
    assert engine.decide("bob", [("DeliverMessage:resident_id", OTHER)])[0].allowed
    assert engine.decide(RESIDENT, [("ForwardMessages:target_resident_id", OTHER)])[0].allowed
    assert not engine.decide("bob", [("UpdateResidentSettings", OTHER)])[0].allowed
    assert levels(engine, default, "bob", OTHER) == "viewer"
    assert not engine.decide("bob", [("DeliverMessage", "personal_space:alice")])[0].allowed


def test_incremental_sync_moves_subtrees_and_rules(store, default):
    """对象移动时子树随之移动，类节点上的权限随位置变化

    Resident 移到另一个 Lineage 后，它在 R、P 两棵树中的位置和 Lineage 成员权限都随之变化；Lineage 先于其中的 Resident 登记（同一事务提交），
    所以 Lineage 尚未登记的 Resident 只按“没有该关系”放在树的兜底节点，不为 Lineage 预留位置，Lineage 删除后其中的 Resident 留在所属 Project 下。
    """
    instance = build(default, store, [Fact("project", PROJECT)])
    engine = engine_for(default, store)
    instance.sync([Fact("resident", "resident_444444444444", {"lineage": LINEAGE})])
    assert store.paths("R", ["resident_444444444444"])["resident_444444444444"] == ["@portal", "@shared", "resident_444444444444"]
    assert store.paths("R", [LINEAGE]) == {}
    instance.sync([Fact("lineage", LINEAGE, {"project": PROJECT}), Fact("resident", RESIDENT, {"lineage": LINEAGE})])
    assert store.paths("R", [RESIDENT])[RESIDENT] == ["@portal", "@shared", "@projects", PROJECT, LINEAGE, RESIDENT]
    assert store.paths("P", [RESIDENT])[RESIDENT] == ["@principals", "@residents", PROJECT, LINEAGE, RESIDENT]
    assert levels(engine, default, RESIDENT, LINEAGE) == "admin"
    # Moving to a Lineage of another Project also leaves the first Project's admin rule.
    instance.sync([Fact("project", OTHER_PROJECT), Fact("lineage", OTHER_LINEAGE, {"project": OTHER_PROJECT}),
                   Fact("resident", RESIDENT, {"lineage": OTHER_LINEAGE})])
    assert levels(engine, default, RESIDENT, LINEAGE) == "viewer"
    assert levels(engine, default, RESIDENT, PROJECT) == "viewer"
    assert levels(engine, default, RESIDENT, OTHER_LINEAGE) == "admin"
    instance.sync([Fact("lineage", OTHER_LINEAGE, active=False)])
    assert store.paths("R", [RESIDENT])[RESIDENT] == ["@portal", "@shared", "@projects", OTHER_PROJECT, RESIDENT]
    assert store.paths("R", [OTHER_LINEAGE]) == {}


def test_only_live_principals_get_creator_entries(store, default):
    """只有现存的 User 或 Resident 才因“Project 创建人”规则得到表项

    创建者从对象自己的记录读取（created_by），权限系统不另存创建者；导入脚本这类不是主体的创建者、已删除的 Resident 都不生成表项，
    重新生成和增量同步一致；Resident 删除时它的表项随之撤除。
    """
    imported = Fact("project", "ImportedProject", creator="ProjectImporter")
    made = Fact("project", "MadeProject", creator=OTHER)
    instance = build(default, store, [*portal_facts(), imported, made])
    projects = ["ImportedProject", "MadeProject", "LaterProject"]

    def creator_entries():
        return {(p, r) for p, _a, r in rows(store, "p_node = ANY(%s) AND r_node = ANY(%s)", ([OTHER, "ProjectImporter"], projects))}
    assert creator_entries() == {(OTHER, "MadeProject")}
    instance.sync([Fact("project", "LaterProject", creator="ProjectImporter")])
    assert ("ProjectImporter", "LaterProject") not in creator_entries()
    instance.sync([Fact("resident", OTHER, {"lineage": OTHER_LINEAGE}, active=False)])
    assert creator_entries() == set()
    instance.generate([fact for fact in portal_facts() if fact.id != OTHER] + [imported, made])
    assert creator_entries() == set()


def test_creating_gives_no_rights_except_projects_chatrooms_and_posts(store, default):
    """创建者对自己创建的对象没有额外权限，Project、聊天室和帖子除外

    知识条目、论文、Lineage 按它们在树上的位置管理：创建者离开 Project 后不再能管理在那里创建的条目。
    实习用户创建的 Project 由创建人管理（一般用户本来就管理全部 Project）；聊天室创建者可以删除聊天室，其他成员只能参与；帖子作者可以修改和删除自己的帖子，其他人只能读。
    """
    made_inside = Fact("decision", "decision_222222222222", {"project": PROJECT}, creator=RESIDENT)
    project = Fact("project", "NewProject", creator="alice")
    room = Fact("chatroom", "chatroom_111111111111", creator="alice", members=("alice", "bob"))
    post = Fact("notice", "notice_111111111111", creator="bob")
    build(default, store, [*portal_facts(), made_inside, project, room, post,
                           Fact("decision", "decision_333333333333", {"project": "NewProject"})], interns=["alice", "bob"])
    engine = engine_for(default, store)
    assert levels(engine, default, "alice", LINEAGE) == "viewer"
    assert levels(engine, default, "alice", "NewProject") == levels(engine, default, "alice", "decision_333333333333") == "admin"
    assert levels(engine, default, "bob", "NewProject") == "viewer"
    assert levels(engine, default, "alice", room.id) == "admin" and levels(engine, default, "bob", room.id) == "contributor"
    assert levels(engine, default, "bob", post.id) == "admin" and levels(engine, default, "alice", post.id) == "viewer"
    Instance(default, store).sync([Fact("resident", RESIDENT, {"lineage": OTHER_LINEAGE})])
    assert levels(engine, default, RESIDENT, made_inside.id) == "viewer"


def test_relation_rows_exist_whatever_else_covers(store, default):
    """创建人和 steward 的表项每个对象各占一行，不看其他表项是否已经覆盖

    管理员、一般用户创建的 Project 也有“Project 创建人”表项；steward 与 Family 在同一 Project 时也有 steward 表项。
    对象移动不改动这些行；Family 换 steward 时旧 steward 的行换成新 steward 的行，Family 删除时它的行随之删除；重新生成与增量同步结果一致。
    """
    mine, bobs, carols = (Fact("project", f"{name}Project", creator=name.lower()) for name in ("Alice", "Bob", "Carol"))
    family = Fact("family", "family_222222222222", {"lineage": LINEAGE}, steward=STEWARD)
    facts = [*portal_facts(), user_fact("carol"), mine, bobs, carols, family]
    instance = build(default, store, facts, administrators=["bob"], interns=["alice"])
    projects = [mine.id, bobs.id, carols.id]
    assert rows(store, "r_node = ANY(%s) AND p_node <> r_node", (projects,)) == {
        ("alice", "@admin", mine.id), ("bob", "@admin", bobs.id), ("carol", "@admin", carols.id)}
    assert rows(store, "p_node = %s", (STEWARD,)) == {(STEWARD, "@admin", OTHER_LINEAGE), (STEWARD, "@admin", LINEAGE)}

    instance.sync([Fact("resident", STEWARD, {"lineage": OTHER_LINEAGE})])
    assert rows(store, "p_node = %s", (STEWARD,)) == {(STEWARD, "@admin", OTHER_LINEAGE), (STEWARD, "@admin", LINEAGE)}
    instance.sync([Fact("family", family.id, {"lineage": LINEAGE}, steward=RESIDENT)])
    assert rows(store, "p_node = %s", (STEWARD,)) == {(STEWARD, "@admin", OTHER_LINEAGE)}
    assert (RESIDENT, "@admin", LINEAGE) in rows(store, "p_node = %s", (RESIDENT,))
    incremental = rows(store)
    instance.generate([*(fact for fact in facts if fact.id not in (STEWARD, family.id)),
                       Fact("resident", STEWARD, {"lineage": OTHER_LINEAGE}),
                       Fact("family", family.id, {"lineage": LINEAGE}, steward=RESIDENT)])
    assert rows(store) == incremental
    instance.sync([Fact("family", family.id, active=False)])
    assert (RESIDENT, "@admin", LINEAGE) not in rows(store, "p_node = %s", (RESIDENT,))


def test_steward_follows_the_family_record(store, default):
    """Family steward 的 admin 随 Family 记录变化

    Family 的 steward 换人或 Family 解散后，原 steward 不再对 Lineage 持有 admin。
    """
    instance = build(default, store, portal_facts())
    engine = engine_for(default, store)
    assert levels(engine, default, STEWARD, OTHER_LINEAGE) == "admin"
    instance.sync([Fact("family", FAMILY, {"lineage": OTHER_LINEAGE}, steward=RESIDENT)])
    assert levels(engine, default, STEWARD, OTHER_LINEAGE) == "viewer"
    assert levels(engine, default, RESIDENT, OTHER_LINEAGE) == "admin"
    instance.sync([Fact("family", FAMILY, active=False)])
    assert levels(engine, default, RESIDENT, OTHER_LINEAGE) == "viewer"


def test_personal_ama_holds_its_owners_permissions(store, default):
    """个人 AMA 的 Resident 拥有所有者的权限，位置由方案规则决定

    个人 AMA 的 Lineage 只把所有者 User 和其个人空间作为归属，不属于任何 Project；默认方案在 R 树中把它放在所有者的个人空间下（under personal_space），
    P 树中按没有 Project 的 Lineage 放在“Resident”下，Resident 随 Lineage。引擎按登记的所有者判定 AMA 的 Resident（represents），所以它与所有者一样管理全部 Project，
    并管理整个 AMA，管理员和其他 User 对 AMA 没有权限；AMA 的 steward 也有自己的 steward 表项；所有者的账号记录改为实习用户后 AMA 的 Resident 随之只能读 Project；
    所有者尚未登记时 AMA 在 R 树按规则节点的兜底位置放在“个人”下，权限系统不替它放置所有者；重新生成与增量同步结果一致。

    :design: decision_b820faf42218, decision_600ae27b0538
    """
    record = {"resource_id": "lineage_555555555555", "attributes": {"lineage_membership": "personal", "created_by": "carol",
                                                                    "project_id": PROJECT}}
    ama = fact_from_record(record, "lineage")
    assert dict(ama.owners) == {"personal_space": "personal_space:carol", "user": "carol"}
    member = Fact("resident", "resident_555555555555", {"lineage": ama.id})
    steward = Fact("resident", "resident_666666666666", {"lineage": ama.id})
    facts = [*portal_facts(), user_fact("carol"), ama, member, steward,
             Fact("family", "family_555555555555", {"lineage": ama.id}, steward=steward.id)]
    instance = build(default, store, facts, administrators=["bob"])
    # The registered owners (personal_owner_id), as par.represents reads them.
    owners = {member.id: "carol", steward.id: "carol", "resident_777777777777": "dave"}
    engine = engine_for(default, store, represents=lambda principal: owners.get(principal, ""))
    placed = {"P": store.paths("P", [ama.id, member.id]), "R": store.paths("R", [ama.id, member.id])}
    assert placed["P"] == {ama.id: ["@principals", "@residents", ama.id],
                           member.id: ["@principals", "@residents", ama.id, member.id]}
    assert placed["R"][ama.id] == ["@portal", "@personal", "personal_space:carol", ama.id]
    for resource in (PROJECT, LINEAGE, OTHER, ama.id, member.id):
        assert levels(engine, default, member.id, resource) == "admin", resource
    assert levels(engine, default, "bob", ama.id) == levels(engine, default, "alice", member.id) == "none"
    assert rows(store, "p_node = %s", (steward.id,)) == {(steward.id, "@admin", ama.id)}

    instance.sync([user_fact("carol", category="intern")])
    assert levels(engine, default, member.id, PROJECT) == "viewer"
    assert levels(engine, default, steward.id, ama.id) == "admin"
    incremental = {"P": store.paths("P", [ama.id, member.id]), "R": store.paths("R", [ama.id, member.id])}
    instance.generate(categorized(facts, ["bob"], ["carol"]))
    assert {"P": store.paths("P", [ama.id, member.id]), "R": store.paths("R", [ama.id, member.id])} == incremental

    orphan = Fact("lineage", "lineage_888888888888", {"personal_space": "personal_space:erin", "user": "erin"})
    instance.sync([orphan])
    assert store.paths("P", ["erin", orphan.id]) == {orphan.id: ["@principals", "@residents", orphan.id]}
    assert store.paths("R", [orphan.id])[orphan.id] == ["@portal", "@personal", orphan.id]
    later = Fact("lineage", "lineage_777777777777", {"personal_space": "personal_space:dave", "user": "dave"})
    instance.sync([user_fact("dave"), later, Fact("resident", "resident_777777777777", {"lineage": later.id})])
    assert store.paths("P", [later.id])[later.id] == ["@principals", "@residents", later.id]
    assert store.paths("R", [later.id])[later.id] == ["@portal", "@personal", "personal_space:dave", later.id]
    assert levels(engine, default, "resident_777777777777", PROJECT) == "admin"


def test_complete_keeps_the_instance_and_applies_rules_to_new_objects_only(store, default):
    """补齐保留实例中的每一行，规则只应用到新对象

    删掉的规则表项、停用的表项和人工加的表项在补齐后保持原样；补齐期间新建的 Project 得到规则给它的表项，
    已删除的 Resident 连同指名它的表项一起离开，移动的对象只改位置。dry_run 只报告数量，不改实例。
    """
    instance = build(default, store, portal_facts())
    engine = engine_for(default, store)
    manual = Entry(new_entry_id(), PERMIT, "alice", "@viewer", "personal_space:bob")
    with store.transaction() as connection:
        store.add(connection, [manual])
        store.delete_contents(connection, [(PERMIT, "@members", "@admin", "@projects")])
        [own] = store.entries(connection, where="p_node = %s AND r_node = %s", parameters=(OTHER_PROJECT, OTHER_PROJECT))
        store.update(connection, own.entry_id, expected_version=own.version, effect=own.effect, p=own.p, a=own.a, r=own.r,
                     disabled=True)
    assert levels(engine, default, "alice", PROJECT) == "viewer"
    assert levels(engine, default, OTHER, OTHER_LINEAGE) == "viewer"

    later = [Fact("project", "LaterProject"), Fact("lineage", "lineage_444444444444", {"project": "LaterProject"}),
             Fact("resident", "resident_444444444444", {"lineage": "lineage_444444444444"})]
    facts = [*(fact for fact in portal_facts() if fact.id not in (OTHER, RESIDENT)),
             Fact("resident", RESIDENT, {"lineage": OTHER_LINEAGE}, creator="alice",
                  dependents=(Fact("workspace", "workspace_111111111111", {"resident": RESIDENT}),)), *later]
    before = rows(store)
    planned = instance.complete(facts, dry_run=True)
    assert rows(store) == before
    assert planned["added_nodes"] == 6 and planned["removed_nodes"] == 2 and planned["moved_nodes"] >= 2
    done = instance.complete(facts)
    assert done == planned
    assert not engine.decide("alice", [("@admin", "@projects")])[0].allowed
    assert ("alice", "@viewer", "personal_space:bob") in rows(store)
    with store.read() as connection:
        assert [entry.disabled for entry in store.entries(connection, where="entry_id = %s", parameters=(own.entry_id,))] == [True]
    assert rows(store, "p_node = %s OR r_node = %s", (OTHER, OTHER)) == set()
    assert ("LaterProject", "@admin", "LaterProject") in rows(store)
    assert levels(engine, default, "resident_444444444444", "lineage_444444444444") == "admin"
    assert levels(engine, default, RESIDENT, OTHER_LINEAGE) == "viewer"
    assert store.paths("R", [RESIDENT])[RESIDENT][-2:] == [OTHER_LINEAGE, RESIDENT]
    assert instance.complete(facts, dry_run=True) == {"added_nodes": 0, "moved_nodes": 0, "removed_nodes": 0,
                                                      "added_entries": 0, "removed_entries": 0}

    instance.generate(facts)
    assert engine.decide("alice", [("@admin", "@projects")])[0].allowed
    assert ("alice", "@viewer", "personal_space:bob") not in rows(store)


def test_rules_digest_ignores_titles():
    """实例按规则摘要判断是否需要重新生成，只改标题不改变它

    改方案和表项的标题只改变整份方案的摘要；改一条表项的 A 节点同时改变规则摘要。
    """
    document = copy.deepcopy(builtin_documents()["default"])
    base = compile_scheme(document)
    document["title"] = "改名"
    document["entries"][0]["title"] = "改名"
    retitled = compile_scheme(document)
    assert retitled.rules_digest == base.rules_digest and retitled.digest != base.digest
    document["entries"][0]["a"] = "@viewer" if document["entries"][0]["a"] != "@viewer" else "@admin"
    assert compile_scheme(document).rules_digest != base.rules_digest


def test_deny_wins_and_holes_travel_with_the_permit(store, default):
    """deny 优先：命中 deny 的请求被拒绝，并报告命中的表项

    一条 deny 表项挖掉 Project 下的一个知识条目后，持有 Project admin 的主体对该条目被拒绝，对 Project 内其他资源不受影响。
    """
    build(default, store, portal_facts(), administrators=["bob"])
    deny = Entry(new_entry_id(), DENY, "bob", "@actions", DECISION)
    with store.transaction() as connection:
        store.add(connection, [deny])
    engine = engine_for(default, store)
    decision = engine.decide("bob", [("@viewer", DECISION)])[0]
    assert not decision.allowed and decision.reason == "denied" and decision.deny.entry_id == deny.entry_id
    assert engine.decide("bob", [("@admin", LINEAGE)])[0].allowed
    coverage = engine.coverage("bob", "@viewer")
    assert DECISION in coverage.deny and "@shared" in coverage.permit
    covered = engine.covered("bob", "@viewer", kind="decision")
    assert DECISION not in covered


def test_reverse_query_lists_covered_resources(store, default):
    """反向查询在数据库中按物化路径过滤

    一个 Resident 能 contributor 的 Lineage 只有它所在 Project 的那个，能 viewer 的覆盖全部共享资源；它能 admin 的 Resident 是本 Project 的全部 Resident。
    """
    build(default, store, portal_facts())
    engine = engine_for(default, store)
    assert engine.covered(RESIDENT, "@contributor", kind="lineage") == [LINEAGE]
    assert engine.covered(RESIDENT, "@viewer", kind="lineage") == sorted([LINEAGE, OTHER_LINEAGE])
    assert engine.covered(RESIDENT, "@admin", kind="resident") == [RESIDENT, STEWARD]


def test_cached_grants_follow_entry_changes(store, default):
    """主体的缓存表项随修订号失效

    新增、停用或删除一条表项后，同一个引擎的下一次判定立即看到变化；停用的表项不参与判定。
    """
    build(default, store, portal_facts())
    engine = engine_for(default, store)
    assert levels(engine, default, OTHER, LINEAGE) == "viewer"
    entry = Entry(new_entry_id(), PERMIT, OTHER, "@admin", LINEAGE)
    with store.transaction() as connection:
        store.add(connection, [entry])
    assert levels(engine, default, OTHER, LINEAGE) == "admin"
    with store.transaction() as connection:
        store.update(connection, entry.entry_id, expected_version=1, effect=PERMIT, p=OTHER, a="@admin", r=LINEAGE, disabled=True)
    assert levels(engine, default, OTHER, LINEAGE) == "viewer"
    with store.transaction() as connection:
        store.update(connection, entry.entry_id, expected_version=2, effect=PERMIT, p=OTHER, a="@admin", r=LINEAGE, disabled=False)
    assert levels(engine, default, OTHER, LINEAGE) == "admin"
    with store.transaction() as connection:
        store.delete(connection, where="entry_id = %s", parameters=(entry.entry_id,))
    assert levels(engine, default, OTHER, LINEAGE) == "viewer"


def test_one_entry_on_all_projects_covers_later_projects_only(store, default):
    """一条“全部 Project”表项覆盖现有和以后新建的 Project，不覆盖 Project 之外的对象

    (carol, admin, 全部 Project) 让 carol 管理每个 Project 及其中的知识、Lineage 和 Resident，之后新建的 Project 不需要新表项；
    其他 User 的账号这类不在“全部 Project”下的对象仍只有全员可读。
    """
    instance = build(default, store, [*portal_facts(), user_fact("carol")], interns=["carol"])
    with store.transaction() as connection:
        store.add(connection, [Entry(new_entry_id(), PERMIT, "carol", "@admin", "@projects")])
    engine = engine_for(default, store)
    for resource in (PROJECT, DECISION, LINEAGE, RESIDENT):
        assert levels(engine, default, "carol", resource) == "admin", resource
    instance.sync([Fact("project", "LaterProject"), Fact("lineage", "lineage_444444444444", {"project": "LaterProject"})])
    assert levels(engine, default, "carol", "LaterProject") == "admin"
    assert levels(engine, default, "carol", "lineage_444444444444") == "admin"
    for resource in ("alice", "bob"):
        assert levels(engine, default, "carol", resource) == "viewer", resource


def test_unowned_papers_sit_in_the_paper_library(store, default):
    """没有关联 Project 的论文放在公共内容下的“论文库”，所有主体都能管理；关联 Project 的论文只由该 Project 管理

    关联 Project 的论文仍在该 Project 下，其他 Project 的 Resident 和普通 User 只能读；论文库中现有和以后收集的论文
    不需要任何逐条表项，所有 User 和 Resident 都有 admin。
    """
    loose = Fact("paper", "paper_111111111111", creator=RESIDENT)
    owned = Fact("paper", "paper_222222222222", {"project": PROJECT}, creator=RESIDENT)
    instance = build(default, store, [*portal_facts(), loose, owned], interns=["alice"])
    paths = store.paths("R", [loose.id, owned.id])
    assert paths[loose.id] == ["@portal", "@shared", "@public", "@papers", loose.id]
    assert paths[owned.id] == ["@portal", "@shared", "@projects", PROJECT, owned.id]
    instance.sync([Fact("paper", "paper_333333333333", creator=RESIDENT)])
    engine = engine_for(default, store)
    for paper in (loose.id, "paper_333333333333"):
        for principal in (RESIDENT, OTHER, "alice"):
            assert levels(engine, default, principal, paper) == "admin", (principal, paper)
    assert levels(engine, default, RESIDENT, owned.id) == "admin"
    assert levels(engine, default, OTHER, owned.id) == levels(engine, default, "alice", owned.id) == "viewer"
    assert rows(store, "r_node LIKE 'paper\\_%%'") == set()


def test_temporary_entries_expire(store, default):
    """临时表项到期后不再生效

    过期时间之前允许，之后同一请求被拒绝，即使表项仍在表中。
    """
    build(default, store, portal_facts())
    now = [time.time()]
    engine = engine_for(default, store, clock=lambda: now[0])
    loan = Entry(new_entry_id(), PERMIT, OTHER, "@contributor", LINEAGE, expires_at=now[0] + 60)
    with store.transaction() as connection:
        store.add(connection, [loan])
    assert levels(engine, default, OTHER, LINEAGE) == "contributor"
    now[0] += 120
    assert levels(engine, default, OTHER, LINEAGE) == "viewer"


def test_one_version_number_drops_every_cache(store, default):
    """实例只有一个版本号：表项和主体位置的变化让它加一，资源移动不加

    写入、修改、删除表项和新主体放进 P 树都让 permission.state 的版本号加一，另一个引擎的缓存随之整个清空；
    资源在 R 树中移动不改变版本号，判定每次现读资源路径，移动后的资源立即按新位置判定。
    """
    instance = build(default, store, portal_facts())
    engine = engine_for(default, store)
    other = engine_for(default, store)
    assert levels(engine, default, OTHER, LINEAGE) == levels(other, default, OTHER, LINEAGE) == "viewer"
    before = store.version()
    with store.transaction() as connection:
        store.add(connection, [Entry(new_entry_id(), PERMIT, OTHER, "@admin", LINEAGE)])
    assert store.version() == before + 1
    assert levels(other, default, OTHER, LINEAGE) == "admin"
    moved = store.version()
    assert levels(other, default, RESIDENT, DECISION) == "admin"
    instance.sync([Fact("decision", DECISION, {"project": OTHER_PROJECT})])
    assert store.version() == moved
    assert store.paths("R", [DECISION])[DECISION] == ["@portal", "@shared", "@projects", OTHER_PROJECT, DECISION]
    assert levels(other, default, RESIDENT, DECISION) == "viewer"
    instance.sync([Fact("resident", "resident_999999999999", {"lineage": LINEAGE})])
    assert store.version() > moved


def _errors(document):
    with pytest.raises(SchemeError) as raised:
        compile_scheme(document)
    return {item["path"] for item in raised.value.errors}


def test_compiler_reports_each_error_with_its_path():
    """方案编译器逐条报告错误及其 JSON 路径

    成环、未声明的父节点、一类对象按同一关系放在两处、不存在的放置关系（under）、嵌套规则节点的 under 不是父节点的对象类型、
    otherwise 不是本树分类节点或用在分类节点下的规则节点上、分类节点带 under、分类节点挂在规则节点下、未知 A 元素都被指出位置；同类对象按不同关系放在多处可以编译。
    instructions 只给 A 元素分类：写判定槽结构（argument、types 或旧的槽数组）、参数槽写 r、固定槽的 r 不是 R 树分类节点都报错。
    关系规则的关系必须取自代码中的固定关系、r 必须是适用于该关系的 R 树规则节点，节点规则的 p、r 必须是分类节点，一条表项不能同时有 p 和 relation。
    表项的取舍不做语义检查，deny 范围内的 permit 照常编译。
    """
    base = builtin_documents()["default"]
    broken = copy.deepcopy(base)
    nodes = broken["trees"]["R"]["nodes"]
    nodes.append({"id": "@loop_a", "parent": "@loop_b"})
    nodes.append({"id": "@loop_b", "parent": "@loop_a"})
    assert any(path.startswith("trees.R.nodes[") for path in _errors(broken))

    twice = copy.deepcopy(base)
    twice["trees"]["R"]["nodes"].append({"id": "@again", "parent": "@project", "each": "lineage", "under": "project"})
    assert any(path.endswith(".each") for path in _errors(twice))
    assert compile_scheme(base).pattern_of("R", "lineage", {"personal_space": "personal_space:carol", "user": "carol"}).node \
        == "@personal_ama_space"
    assert compile_scheme(base).pattern_of("R", "lineage", {}).node == "@lineage"

    owner = copy.deepcopy(base)
    owner["trees"]["R"]["nodes"].append({"id": "@chat_in_lineage", "parent": "@lineage", "each": "dataset", "under": "lineage"})
    assert any(path.endswith(".under") for path in _errors(owner))
    under = copy.deepcopy(base)
    under["trees"]["R"]["nodes"].append({"id": "@under_case", "parent": "@shared", "each": "dataset", "under": "project"})
    assert compile_scheme(under).pattern_of("R", "dataset", {"project": PROJECT}).under == "project"
    assert compile_scheme(under).pattern_of("R", "dataset", {"project": PROJECT}).fallback == "@shared"
    for parent, related in (("@lineage", "project"), ("@shared", "user"), ("@project", "")):
        wrong = copy.deepcopy(base)
        wrong["trees"]["R"]["nodes"].append({"id": "@under_case", "parent": parent, "each": "dataset", "under": related})
        assert any(path.endswith(".under") for path in _errors(wrong)), (parent, related)
    labelled = copy.deepcopy(base)
    next(node for node in labelled["trees"]["R"]["nodes"] if node["id"] == "@public")["under"] = "project"
    assert any(path.startswith("trees.R.nodes[") for path in _errors(labelled))

    no_node = copy.deepcopy(base)
    no_node["instructions"]["DeliverMessage:resident_id"]["a"] = "@nowhere"
    assert "instructions.DeliverMessage:resident_id.a" in _errors(no_node)

    retired = copy.deepcopy(base)
    retired["levels"] = {"viewer": "@viewer", "contributor": "@contributor", "admin": "@admin"}
    retired["instructions"]["DeliverMessage:resident_id"] = {"level": "contributor"}
    # What a slot judges belongs to the declaration: the old per-instruction slot lists no longer compile.
    retired["instructions"]["GetLineage"] = [{"argument": "lineage_id", "a": "@viewer", "types": ["lineage"]}]
    retired["instructions"]["GetResident"] = {"a": "@viewer", "types": ["resident"]}
    retired["instructions"]["UpdateLineage"] = {"a": "@contributor", "r": "@shared"}
    retired["instructions"]["CreateUser"] = {"a": "@admin", "r": "@nowhere"}
    assert {"levels", "instructions.DeliverMessage:resident_id", "instructions.GetLineage", "instructions.GetResident",
            "instructions.UpdateLineage.r", "instructions.CreateUser.r"} <= set(_errors(retired))

    under_rule = copy.deepcopy(base)
    under_rule["trees"]["R"]["nodes"].append({"id": "@group_in_lineage", "parent": "@lineage"})
    assert any(path.endswith(".parent") for path in _errors(under_rule))

    for node_id, otherwise in (("@project", "@shared"), ("@lineage", "@nowhere"), ("@lineage", "@project")):
        misplaced = copy.deepcopy(base)
        next(node for node in misplaced["trees"]["R"]["nodes"] if node["id"] == node_id)["otherwise"] = otherwise
        assert any(path.endswith(".otherwise") for path in _errors(misplaced)), (node_id, otherwise)

    def entry_errors(entry):
        document = copy.deepcopy(base)
        document["entries"].append({"id": "case", "effect": "permit", "a": "@admin", **entry})
        return _errors(document)
    entry_at = f"entries[{len(base['entries'])}]"
    assert f"{entry_at}.relation" in entry_errors({"relation": "colleague", "r": "@project"})
    assert f"{entry_at}.relation" in entry_errors({"relation": "steward", "r": "@project"})
    assert f"{entry_at}.r" in entry_errors({"relation": "creator", "r": "@shared"})
    assert f"{entry_at}.p" in entry_errors({"p": "@resident", "r": "@shared"})
    assert f"{entry_at}.r" in entry_errors({"p": "@users", "r": "@project"})
    assert entry_at in entry_errors({"p": "@users", "relation": "creator", "r": "@project"})

    inside = copy.deepcopy(base)
    inside["entries"] += [{"id": "block", "effect": "deny", "p": "@principals", "a": "@actions", "r": "@shared"}]
    compile_scheme(inside)

    unknown = copy.deepcopy(base)
    unknown["instructions"]["NoSuchInstruction"] = {"a": "@viewer"}
    assert "instructions.NoSuchInstruction" in _errors(unknown)
    from management_portal.hatg.declarations import On

    with pytest.raises(SchemeError) as raised:
        compile_scheme(base, instructions={"GetLineage": (On("lineage_id", "lineage"),)})
    assert "instructions.ApplyLineageChanges" in {item["path"] for item in raised.value.errors}


def test_builtin_schemes_compile_against_the_catalog():
    """内置方案都能按当前 ISA 目录编译，默认方案给每个 A 元素分类

    默认方案分类的每个 A 元素都来自指令声明的判定槽，声明中的每个判定槽也都有分类（免检的指令显式放在“免检”下）；内置方案只有默认方案。
    """
    documents = builtin_documents()
    default = compile_scheme(documents["default"])
    assert default.unmapped() == []
    assert "@unmapped" not in default.trees["A"].nodes
    assert default.trees["A"].default_parent == "@admin"
    assert default.describe("ApplyLineageChanges") == {"required": True, "requirements": [
        {"element": "ApplyLineageChanges", "argument": "lineage_id", "a": "@contributor", "types": ["lineage"]}]}
    assert default.describe("GetPortalStatus") == {"required": True, "requirements": [
        {"element": "GetPortalStatus", "a": "@viewer", "resource": "@shared"}]}
    assert default.describe("MarkMailboxItemRead") == {"required": True, "requirements": [
        {"element": "MarkMailboxItemRead", "a": "@free", "resource": "@portal"}]}
    unclassified = copy.deepcopy(documents["default"])
    del unclassified["instructions"]["CreateTemplate:@public"]
    described = compile_scheme(unclassified).describe("CreateTemplate")
    assert described["unmapped"] is True and described["requirements"][0] == {
        "element": "CreateTemplate:@public", "a": "@admin", "resource": "@shared"}
    assert sorted(documents) == ["default"]
    json.dumps(documents)


def test_builtin_rejects_new_instruction_without_classification(monkeypatch):
    """新增指令漏分类应在加载内置模板时报告，不能成为运行时权限拒绝。"""
    from management_portal.hatg.declarations import On
    from management_portal.permission_system.scheme import repository

    declared = repository.declared_decisions()
    declared["InspectExampleLineage"] = (On("lineage_id", "lineage"),)
    monkeypatch.setattr(repository, "declared_decisions", lambda: declared)
    with pytest.raises(SchemeError) as raised:
        repository.builtin_documents()
    assert "instructions.InspectExampleLineage" in {item["path"] for item in raised.value.errors}


def test_project_admin_and_shared_administration_boundaries(store):
    """Project 管理权限覆盖同 Project 的指令，共享运维仍需对应资源权限。

    自定义方案漏项沿其默认管理节点判定。显式映射到“免检”的指令所有主体都能通过；
    发布、重启与模型接口落在“Portal 运维”上，Project 成员对自己 Project 的 admin 不够；只能对“共享”有 @admin 的管理员通过。
    主机脚本（RunScript）按所属 Lineage 判定 @admin（sample_admin 2026-10-19）：项目 admin 能在自己 Project 的 Lineage 中运行，对其他 Project 的 Lineage 不行。
    """
    from management_portal.permission_system.requests import requests

    document = copy.deepcopy(builtin_documents()["default"])
    del document["instructions"]["RestartPortal"]
    scheme = compile_scheme(document)
    build(scheme, store, portal_facts(), administrators=("alice",), interns=("bob",))
    engine = engine_for(scheme, store)

    def allowed(principal, instruction, arguments=None):
        asked = [(request.action, request.resource) for request in requests(scheme, instruction, arguments or {})]
        return all(decision.allowed for decision in engine.decide(principal, asked))

    assert [(request.action, request.resource) for request in requests(scheme, "RestartPortal", {})] == [("RestartPortal", "@shared")]
    assert scheme.trees["A"].element_path("RestartPortal") == ("@actions", "@admin", "RestartPortal")
    assert allowed("alice", "RestartPortal")
    assert not allowed("bob", "RestartPortal") and not allowed(RESIDENT, "RestartPortal")
    assert allowed(RESIDENT, "RunScript", {"lineage_id": LINEAGE}) and allowed(OTHER, "RunScript", {"lineage_id": OTHER_LINEAGE})
    assert not allowed(OTHER, "RunScript", {"lineage_id": LINEAGE}) and not allowed("bob", "RunScript", {"lineage_id": LINEAGE})
    with pytest.raises(ValueError):
        requests(scheme, "RunScript", {})
    assert allowed("bob", "MarkMailboxItemRead") and allowed(RESIDENT, "GetInstructionSchema")
    assert allowed("bob", "GetPortalStatus") and allowed(RESIDENT, "GetResident", {"resident_id": OTHER})
    assert levels(engine, scheme, RESIDENT, PROJECT) == "admin"
    assert allowed(RESIDENT, "CheckFamilyRoleAvailability", {"lineage_id": LINEAGE})
    assert not allowed(OTHER, "CheckFamilyRoleAvailability", {"lineage_id": LINEAGE})
    assert not allowed(RESIDENT, "PublishRelease") and not allowed(RESIDENT, "UpdateModelApi")
    assert allowed("alice", "PublishRelease")
    assert allowed(RESIDENT, "DeleteFamily", {"lineage_id": LINEAGE}) and not allowed("bob", "DeleteFamily", {"lineage_id": LINEAGE})


def test_a_free_argument_slot_passes_an_object_the_instance_does_not_hold(store):
    """免检的参数槽遇到实例中没有的对象照常放行，对象是否存在由指令自己报告

    参数槽分类为“免检”时，请求仍按参数给出的对象判定；实例中没有这个对象，它落在 R 树默认节点（“共享”）下，
    仍在 Portal 根之下，“免检”表项覆盖它，所有主体都能通过。
    """
    from management_portal.permission_system.requests import requests

    document = copy.deepcopy(builtin_documents()["default"])
    document["instructions"]["GetResident"] = {"a": "@free"}
    scheme = compile_scheme(document)
    build(scheme, store, portal_facts(), administrators=("alice",), interns=("bob",))
    engine = engine_for(scheme, store)
    missing = "resident_000000000000"

    asked = [(request.action, request.resource) for request in requests(scheme, "GetResident", {"resident_id": missing})]
    assert asked == [("GetResident", missing)]
    assert engine.path("R", missing, store.paths("R", [missing]).get(missing)) == scheme.trees["R"].element_path(missing)
    assert scheme.trees["R"].element_path(missing)[:-1] == scheme.trees["R"].element_path(scheme.unmapped_resource)
    for principal in ("bob", RESIDENT):
        assert all(decision.allowed for decision in engine.decide(principal, asked)), principal
        assert all(decision.allowed for decision in engine.decide(
            principal, [("GetResident", OTHER)])), principal


def test_a_call_that_asks_nothing_passes_administrators_only(store):
    """一次调用没有生成任何判定请求时按未映射指令处理，只有管理员能通过

    映射里的 slot 全部可选、调用一个都没给时，过去没有请求要判定，对所有人放行；现在和未映射的指令一样落在“共享”上，
    只有管理员通过（decision_898b8911a2ff“目录测试”）。给出了参数的调用照常按映射判定，显式映射到“免检”的指令照常放行。
    """
    from management_portal.permission_system.requests import requests

    scheme = compile_scheme(builtin_documents()["default"])
    build(scheme, store, portal_facts(), administrators=("alice",), interns=("bob",))
    engine = engine_for(scheme, store)

    def allowed(principal, instruction, arguments=None):
        asked = [(request.action, request.resource) for request in requests(scheme, instruction, arguments or {})]
        return all(decision.allowed for decision in engine.decide(principal, asked))

    # Every slot optional, none given: ListPermissionEntries without r.
    assert [(request.action, request.resource) for request in requests(scheme, "ListPermissionEntries", {})] == [("@admin", "@shared")]
    assert not allowed("bob", "ListPermissionEntries") and not allowed(RESIDENT, "ListPermissionEntries")
    assert allowed("alice", "ListPermissionEntries")
    # Registering judges where the new resource will sit: a device has no place of its own (共享), an article
    # without a Project sits in 公共内容.
    device = {"resource_type": "device", "fields": {"name": "gpu"}}
    assert [request.resource for request in requests(scheme, "RegisterResource", device)] == ["@shared"]
    assert not allowed("bob", "RegisterResource", device) and not allowed(RESIDENT, "RegisterResource", device)
    assert allowed("alice", "RegisterResource", device)
    article = {"resource_type": "article", "fields": {}}
    assert [request.resource for request in requests(scheme, "RegisterResource", article)] == ["@public"]
    assert allowed("bob", "RegisterResource", article) and allowed(RESIDENT, "RegisterResource", article)
    assert allowed(RESIDENT, "CreateTemplate", {"from_selection": {"lineage_id": LINEAGE}})
    assert allowed("bob", "GetPermissionSystem")


def test_a_place_slot_judges_where_a_new_resource_will_sit(store):
    """按落点判定的 slot 判定新资源将要放进 R 树的位置，与登记后实际入树的位置相同

    指令声明里的 place 写关系对象类型到参数路径的映射，参数是新资源的类型；有关系对象时判定那个对象，没有时判定放置规则的 otherwise 节点，
    类型不在任何放置规则里时判定 R 树默认节点（decision_600ae27b0538）。@public 这类分类节点不是对象，按没有关系对象处理。
    """
    from management_portal.permission_system.requests import Unresolved, requests

    slot = {"argument": "resource_type", "a": "@contributor", "place": {"project": ["fields.project_id", "fields.project_ids"]}}
    scheme = compile_scheme(builtin_documents()["default"])
    assert scheme.describe("RegisterResource")["requirements"] == [{"element": "RegisterResource", **slot}]

    cases = [
        ("device_111111111111", "device", {}, "@shared"),
        ("article_111111111111", "article", {}, "@public"),
        ("article_222222222222", "article", {"project_id": PROJECT}, PROJECT),
        ("repo_111111111111", "repo", {"project_ids": ["@public"]}, "@public"),
        ("repo_222222222222", "repo", {"project_ids": [OTHER_PROJECT, PROJECT]}, OTHER_PROJECT),
        # A Workbench sits under the first Project it names; with none, under its Lineage, else in 公共内容.
        ("workbench_222222222222", "workbench", {"project_ids": [OTHER_PROJECT, PROJECT]}, OTHER_PROJECT),
        ("workbench_333333333333", "workbench", {}, "@public"),
    ]
    for _id, kind, fields, place in cases:
        assert [(request.action, request.resource) for request in requests(scheme, "RegisterResource", {"resource_type": kind, "fields": fields})] == [("RegisterResource", place)]
    with pytest.raises(Unresolved):
        requests(scheme, "RegisterResource", {"resource_type": "article", "fields": {"project_id": LINEAGE}})

    # Registered, each sits where it was judged.
    instance = build(scheme, store, [*portal_facts(), *(fact_from_record({"resource_id": resource_id, **fields}, kind)
                                                       for resource_id, kind, fields, _place in cases)])
    stored = instance.store.paths("R", [resource_id for resource_id, *_ in cases])
    for resource_id, _kind, _fields, place in cases:
        path = stored.get(resource_id) or [*scheme.trees["R"].path(scheme.trees["R"].default_parent), resource_id]
        assert path[-2] == place, resource_id


def test_a_via_slot_judges_the_object_its_saved_record_names():
    """按记录判定的 slot 在入口读出记录所属的对象，再和其他请求一起判定

    指令声明里的 via 写 <记录>.<字段>，参数值是记录 ID，判定对象是记录在该字段里保存的对象（decision_898b8911a2ff“判定对象可以来自已保存的记录”）。
    记录不存在时整次调用按不存在处理；记录在该字段里没有对象（调用者作用域的回执）时不产生请求。
    """
    from management_portal.permission_system.requests import RecordNotFound, Unresolved, requests

    slot = {"element": "UpdatePermissionEntry:entry_id", "argument": "entry_id", "a": "@admin", "via": "permission_entry.r"}
    scheme = compile_scheme(builtin_documents()["default"])
    assert scheme.describe("UpdatePermissionEntry")["requirements"][0] == slot

    saved = {"entry_aaaaaaaaaaaa": {"r": LINEAGE}, "entry_bbbbbbbbbbbb": {"r": "@shared"}}
    receipts = {"inst_l_1": {"lineage": LINEAGE}, "inst_l_2": {"lineage": ""}}
    readers = {"permission_entry": lambda ids, _arguments: {key: saved[key] for key in ids if key in saved},
               "instruction_execution": lambda ids, _arguments: {key: receipts[key] for key in ids if key in receipts}}

    def asked(instruction, arguments):
        return [(request.action, request.resource) for request in requests(scheme, instruction, arguments, readers=readers)]

    assert asked("UpdatePermissionEntry", {"entry_id": "entry_aaaaaaaaaaaa", "r": PROJECT}) == [
        ("UpdatePermissionEntry:entry_id", LINEAGE), ("UpdatePermissionEntry:r", PROJECT)]
    assert asked("DeletePermissionEntry", {"entry_ids": ["entry_aaaaaaaaaaaa", "entry_bbbbbbbbbbbb"]}) == [
        ("DeletePermissionEntry:entry_ids", LINEAGE), ("DeletePermissionEntry:entry_ids", "@shared")]
    with pytest.raises(RecordNotFound):
        asked("DeletePermissionEntry", {"entry_ids": ["entry_aaaaaaaaaaaa", "entry_cccccccccccc"]})
    assert asked("GetInstructionExecution", {"instruction_id": "inst_l_1"}) == [
        ("GetInstructionExecution:instruction_id", LINEAGE), ("GetInstructionExecution:@portal", "@portal")]
    assert asked("GetInstructionExecution", {"instruction_id": "inst_l_2"}) == [("GetInstructionExecution:@portal", "@portal")]
    with pytest.raises(Unresolved):
        requests(scheme, "UpdatePermissionEntry", {"entry_id": "entry_aaaaaaaaaaaa"})
