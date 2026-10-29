"""The Project list and a Project's overview read registry, index and team in one call.

:module: spec.design-governance
:design: decision_46c37026e8ea, decision_6591e7793116
"""
from __future__ import annotations

from types import SimpleNamespace

from management_portal import society_service
from management_portal.resources import isa as resource_isa
from management_portal.society_service import lineage_access_policy, lineage_domain, project_domain, resident_protocol


def project(project_id, **attributes):
    return {"resource_id": project_id, "resource_type": "project", "title": project_id,
            "summary": f"{project_id} description", "source": {"modified_at": "2026-01-01T00:00:00Z"},
            "attributes": {"status": "active", "project_type": "academic", "ddl": "", "tags": [], **attributes}}


def resource(resource_id, resource_type, projects, modified_at):
    return {"resource_id": resource_id, "resource_type": resource_type, "title": resource_id.upper(),
            "url": f"/r/{resource_id}", "source": {"modified_at": modified_at},
            "attributes": {"project_ids": list(projects)}}


class Index:
    """Answers queries the way ResourceService filters them, a page at a time."""

    def __init__(self, records, state):
        self.records = records
        self.state = state

    def query(self, *, actor, limit, offset=0, include_attributes=False, resource_types=(), projects=(), q=""):
        assert actor == "alice" and include_attributes
        found = [record for record in self.records
                 if (not resource_types or record["resource_type"] in resource_types)
                 and (not projects or set(projects) & set(record["attributes"].get("project_ids") or ()))
                 and (not q or q == record["resource_id"])]
        return {"resources": found[offset:offset + limit], "total": len(found)}

    def projection_state(self, adapter):
        assert adapter == "project"
        return self.state


def team(resident="", lineages=(), running=0):
    return {"project_resident_id": resident, "project_lineage_id": "lineage_000000000001" if resident else "",
            "lineages": list(lineages), "resident_count": 1 + sum(item["member_count"] for item in lineages),
            "running_resident_count": running}


def use(monkeypatch, index, teams):
    monkeypatch.setattr(resource_isa, "_service", lambda: index)
    calls = []

    def project_teams(project_ids, actor):
        calls.append((list(project_ids), actor))
        return {project_id: teams[project_id] for project_id in project_ids}

    monkeypatch.setattr(society_service, "get_society_service",
                        lambda: SimpleNamespace(project=SimpleNamespace(project_teams=project_teams)))
    return calls


def invocation(**arguments):
    return SimpleNamespace(caller="alice", argument=arguments.get)


def test_project_list_carries_registry_fields_and_signals_in_registry_order(monkeypatch):
    """ListProjects 按登记顺序列出项目，并带团队规模、运行中的 Resident 数和最近变化时间"""
    lineage = {"lineage_id": "lineage_000000000002", "title": "Speed", "leader_resident_id": "resident_000000000002",
               "member_count": 3, "running_count": 1}
    index = Index([
        project("Zeta", tags=["robot"], ddl="2026-12-31"), project("Alpha", status="paused"),
        resource("decision_000000000001", "decision", ["Zeta"], "2026-10-02T00:00:00Z"),
        resource("paper_000000000001", "paper", ["Zeta", "Alpha"], "2026-10-05T00:00:00Z"),
        resource("decision_000000000002", "decision", ["Zeta"], "2026-10-09T00:00:00Z"),
    ], {"schema_version": 1, "project_ids": ["Zeta", "Alpha"], "status_vocab": ["active", "paused"]})
    calls = use(monkeypatch, index, {"Zeta": team("resident_000000000001", [lineage], running=2),
                                     "Alpha": team()})
    result = resource_isa.list_projects(invocation())
    assert calls == [(["Zeta", "Alpha"], "alice")]
    assert result["status_vocab"] == ["active", "paused"]
    zeta, alpha = result["projects"]
    assert zeta == {"project_id": "Zeta", "status": "active", "project_type": "academic",
                    "description": "Zeta description", "ddl": "2026-12-31", "tags": ["robot"],
                    "project_resident_id": "resident_000000000001", "lineage_count": 1, "resident_count": 4,
                    "running_resident_count": 2, "last_changed_at": "2026-10-09T00:00:00Z"}
    assert (alpha["project_id"], alpha["status"], alpha["lineage_count"]) == ("Alpha", "paused", 0)
    assert alpha["last_changed_at"] == "2026-10-05T00:00:00Z"


def test_project_overview_counts_resources_and_lists_recent_changes_without_the_team(monkeypatch):
    """GetProject 按类型计数资源、按时间列出最近修改，团队对象不计入，团队由 SocietyService 给出"""
    from management_portal.resources.providers import project as project_provider

    records = [project("Demo", tags=["vla"])]
    records += [resource(f"decision_{index:012x}", "decision", ["Demo"], f"2026-10-{index:02d}T00:00:00Z")
                for index in range(1, 25)]
    records += [resource("lineage_00000000000a", "lineage", ["Demo"], "2026-11-01T00:00:00Z"),
                resource("resident_00000000000a", "resident", ["Demo"], "2026-11-01T00:00:00Z"),
                resource("paper_00000000000a", "paper", ["Demo"], "2026-10-30T00:00:00Z"),
                resource("decision_0000000000ff", "decision", ["Other"], "2026-12-01T00:00:00Z")]
    the_team = team("resident_000000000001")
    use(monkeypatch, Index(records, {}), {"Demo": the_team})
    monkeypatch.setattr(project_provider, "project_summary", lambda project_id: {"exists": True, "text": "# Demo"})
    result = resource_isa.get_project(invocation(project_id="Demo"))
    assert result["resource_counts"] == {"decision": 24, "paper": 1}
    assert [item["resource_id"] for item in result["recent_changes"]][:2] == ["paper_00000000000a", "decision_000000000018"]
    assert len(result["recent_changes"]) == resource_isa.RECENT_PROJECT_CHANGES
    assert result["recent_changes"][0] == {"resource_id": "paper_00000000000a", "resource_type": "paper",
                                           "title": "PAPER_00000000000A", "url": "/r/paper_00000000000a",
                                           "modified_at": "2026-10-30T00:00:00Z"}
    assert result["team"] == the_team
    assert result["summary"]["text"] == "# Demo"
    assert result["project"]["resource_id"] == "Demo" and "project_id" not in result["project"]
    assert (result["project"]["status"], result["project"]["tags"]) == ("active", ["vla"])
    assert "state" not in result


def test_project_team_is_its_readable_lineages_with_leaders_and_running_members(monkeypatch):
    """Project 的团队由它能读到的 Project 层与普通 Lineage 组成，个人与 User 层 Lineage 不算在内"""
    rows = [
        {"lineage_id": "lineage_00000000000a", "project_id": "Demo", "title": "Project Lineage",
         "leader_resident_id": "resident_00000000000a", "principal_hierarchy": {"level": "project"}},
        {"lineage_id": "lineage_00000000000b", "project_id": "Demo", "title": "Vision",
         "leader_resident_id": "resident_00000000000b", "principal_hierarchy": {"level": "lineage"}},
        {"lineage_id": "lineage_00000000000c", "project_id": "Demo", "title": "Hidden",
         "leader_resident_id": "resident_00000000000c", "principal_hierarchy": {"level": "lineage"}},
        {"lineage_id": "lineage_00000000000d", "project_id": "Demo", "title": "AMA", "lineage_membership": "personal",
         "leader_resident_id": "resident_00000000000d", "principal_hierarchy": {"level": "lineage"}},
        {"lineage_id": "lineage_00000000000e", "project_id": "Other", "title": "Elsewhere",
         "leader_resident_id": "resident_00000000000e", "principal_hierarchy": {"level": "lineage"}},
    ]
    nodes = {"lineage_00000000000a": ["resident_00000000000a", "resident_00000000000b"],
             "lineage_00000000000b": ["resident_00000000000b", "resident_000000000001", "resident_000000000002"]}

    class Catalog:
        def listing_records(self):
            return rows

        def listing_graph_summaries(self, lineage_ids):
            assert sorted(lineage_ids) == ["lineage_00000000000a", "lineage_00000000000b"]
            return {lineage_id: {"lineage_node_ids": nodes[lineage_id]} for lineage_id in lineage_ids}

    monkeypatch.setattr(lineage_domain, "_lineage_catalog_store", lambda: Catalog())
    monkeypatch.setattr(lineage_access_policy, "changeable_ids",
                        lambda actor, owners, instruction, parameter: set(owners) - {"lineage_00000000000c"})
    monkeypatch.setattr(resident_protocol, "read_resident_states", lambda ids: {
        resident_id: {"work_state": "running" if resident_id in {"resident_00000000000a", "resident_000000000002"}
                      else "idle"} for resident_id in ids})
    teams = project_domain.project_teams(["Demo", "Empty"], "alice")
    assert teams["Empty"] == {"project_resident_id": "", "project_lineage_id": "", "lineages": [],
                              "resident_count": 0, "running_resident_count": 0}
    # The Project Lineage's reference to the Vision Leader is not a second member.
    assert teams["Demo"] == {
        "project_resident_id": "resident_00000000000a", "project_lineage_id": "lineage_00000000000a",
        "lineages": [{"lineage_id": "lineage_00000000000b", "title": "Vision",
                      "leader_resident_id": "resident_00000000000b", "member_count": 3, "running_count": 1}],
        "resident_count": 4, "running_resident_count": 2,
    }
