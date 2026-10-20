"""Reclaim ordinary temporary teams owned by one TAU Run/module."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from management_portal.hatg.contracts import InstructionError
from management_portal.shared_state import atomic_replace_json
from management_portal.test_framework.tau_residents import TAU_ACTOR


def team_marker(run_id: str, module_id: str) -> str:
    if not re.fullmatch(r"testrun_[0-9a-f]{16}", run_id):
        raise ValueError("Invalid TAU Run ID")
    if not re.fullmatch(r"[a-z][a-z0-9.-]+", module_id):
        raise ValueError("Invalid TAU module ID")
    return f"[TAU:{run_id}:{module_id}]"


def _instruction(name: str, arguments: dict[str, Any], *, key: str = "", version: Any = None) -> dict[str, Any]:
    from management_portal import isa_client

    return isa_client.call(TAU_ACTOR, name, arguments, key=key, version=version)


def _query(**kwargs: Any) -> dict[str, Any]:
    from management_portal.resources import get_resource_service

    return get_resource_service().query(actor=TAU_ACTOR, resource_types=["lineage"], **kwargs)


def _resource(resource_id: str) -> dict[str, Any]:
    from management_portal.resources import get_resource_service

    return get_resource_service().get(resource_id, actor=TAU_ACTOR)


def _release_member(lineage_id: str, resident_id: str, key: str) -> dict[str, Any]:
    from management_portal.resident_handoff import release_resident

    release_resident(actor=TAU_ACTOR, lineage_id=lineage_id, resident_id=resident_id)
    return {"resident_id": resident_id, "deleted": True}


class TauTeamCleanup:
    """Owns discovery, exact deletion requests and verified cleanup receipts.

    The marker is an explicit fixture contract, not a fuzzy title heuristic.
    Creation and deletion remain ordinary HATG operations.
    """

    def __init__(self, *, query: Callable = _query, instruction: Callable = _instruction,
                 resource: Callable = _resource, release_member: Callable = _release_member):
        self.query = query
        self.instruction = instruction
        self.resource = resource
        self.release_member = release_member

    def _graph(self, lineage_id: str) -> dict[str, Any] | None:
        try:
            return self.instruction("GetLineage", {"lineage_id": lineage_id}).get("lineage")
        except InstructionError as exc:
            if exc.code == "instruction.not_found":
                return None
            raise
        except (KeyError, FileNotFoundError):
            return None

    def _resident(self, lineage_id: str, resident_id: str) -> dict[str, Any] | None:
        try:
            return self.instruction("GetResident", {"resident_id": resident_id}).get("resident")
        except InstructionError as exc:
            if exc.code == "instruction.not_found":
                return None
            raise
        except (KeyError, FileNotFoundError):
            return None

    def _cleanup_members(self, state: dict[str, Any], path: Path, marker: str,
                         lineages: set[str], protected: set[str],
                         emit: Callable[[str], None]) -> list[str]:
        """Only marked sample members of an assigned module team are disposable."""
        members = state.setdefault("residents", {})
        for lineage_id in sorted(lineages):
            graph = self._graph(lineage_id)
            if graph is None:
                continue
            for node in graph.get("nodes", []):
                if marker in str(node.get("title") or ""):
                    members.setdefault(node["id"], {"lineage_id": lineage_id})
        atomic_replace_json(path, state)
        errors = []
        for resident_id, entry in members.items():
            try:
                lineage_id = entry["lineage_id"]
                if resident_id in protected or lineage_id not in lineages:
                    raise ValueError("Temporary member is outside the assigned module scope")
                resident = self._resident(lineage_id, resident_id)
                if resident is None:
                    entry.update(status="deleted", error="")
                    continue
                graph = self._graph(lineage_id)
                provenance = self.resource(resident_id)
                attributes = provenance.get("attributes") or {}
                if (graph is None or resident.get("lineage_id") != lineage_id
                        or provenance.get("resource_id") != resident_id
                        or provenance.get("resource_type") != "resident"
                        # The module's TAU Resident may create samples through its own ISA tool.
                        or attributes.get("created_by") not in {TAU_ACTOR, *protected}
                        or marker not in str(resident.get("title") or "")
                        or resident_id == graph.get("leader_resident_id")):
                    raise ValueError("Temporary member ownership, home or marker changed")
                node = next((n for n in graph.get("nodes", []) if n.get("id") == resident_id), {})
                if node.get("status") not in {"idle", "closed"}:
                    raise ValueError("Temporary member still has active or unknown work")
                request = entry.setdefault("request", {
                    "arguments": {"resident_id": resident_id},
                    "key": f"tau-cleanup:{state['run_id']}:{state['module_id']}:{resident_id}",
                })
                entry["status"] = "deleting"
                atomic_replace_json(path, state)
                entry["receipt"] = self.release_member(lineage_id, resident_id, request["key"])
                if self._resident(lineage_id, resident_id) is not None:
                    raise RuntimeError("DeleteResident has not removed the member")
                entry.update(status="deleted", error="")
                emit(f"TAU 已清理临时成员：{resident_id}，保留团队 {lineage_id}\n")
            except Exception as exc:
                entry.update(status="error", error=str(exc))
                errors.append(f"{resident_id}: {exc}")
            finally:
                atomic_replace_json(path, state)
        return errors

    def run(self, *, run_id: str, module_id: str, run_dir: Path,
            protected_lineages: set[str], protected_residents: set[str],
            emit: Callable[[str], None], resident_lineages: set[str] | None = None,
            lineage_fixtures: dict[str, str] | None = None) -> dict[str, Any]:
        marker = team_marker(run_id, module_id)
        path = run_dir / "team-cleanup.json"
        state = json.loads(path.read_text()) if path.exists() else {
            "run_id": run_id, "module_id": module_id, "teams": {},
        }
        if state.get("run_id") != run_id or state.get("module_id") != module_id:
            raise ValueError("TAU cleanup journal belongs to another module/Run")
        teams = state["teams"]
        state["status"] = "running"
        run_dir.mkdir(parents=True, exist_ok=True)
        atomic_replace_json(path, state)
        # Collect every page before deleting: mutation must not shift offsets.
        offset = 0
        while True:
            page = self.query(q=run_id, offset=offset, limit=200)
            rows = page["resources"]
            for row in rows:
                if marker in str(row.get("title") or ""):
                    teams.setdefault(row["resource_id"], {})
            offset += len(rows)
            if offset >= page["total"]:
                break
            if not rows:
                raise RuntimeError("Incomplete TAU team discovery")
        atomic_replace_json(path, state)
        errors = []
        if lineage_fixtures is not None:
            state["lineage_fixtures"] = dict(lineage_fixtures)
            if teams and not lineage_fixtures:
                errors.append("发现未声明独立团队必要性的 TAU 残留；按原范围回收并报告违规")
        for lineage_id, entry in teams.items():
            try:
                graph = self._graph(lineage_id)
                if graph is None:
                    entry.update(status="deleted", error="")
                    continue
                if lineage_id in protected_lineages or any(
                    node.get("id") in protected_residents for node in graph.get("nodes", [])
                ):
                    raise ValueError("Refusing to delete a persistent TAU module identity")
                if (graph.get("created_by") != TAU_ACTOR
                        or marker not in str(graph.get("title") or "")):
                    raise ValueError("Temporary team creation provenance or marker changed")
                if any(node.get("status") not in {"idle", "closed"} for node in graph.get("nodes", [])):
                    raise ValueError("Temporary team still has active or unknown work")
                # An ambiguous transport result reuses this exact request after
                # restart; never silently rebase an already dispatched deletion.
                request = entry.setdefault("request", {
                    "arguments": {"lineage_id": lineage_id},
                    "key": f"tau-cleanup:{run_id}:{module_id}:{lineage_id}",
                })
                entry["status"] = "deleting"
                atomic_replace_json(path, state)
                entry["receipt"] = self.instruction("DeleteLineage", request["arguments"], key=request["key"])
                if self._graph(lineage_id) is not None:
                    raise RuntimeError("DeleteLineage has not removed the team")
                entry.update(status="deleted", error="")
                emit(f"TAU 已清理临时团队：{lineage_id}\n")
            except Exception as exc:
                entry.update(status="error", error=str(exc))
                errors.append(f"{lineage_id}: {exc}")
            finally:
                atomic_replace_json(path, state)
        errors.extend(self._cleanup_members(
            state, path, marker, resident_lineages or set(), protected_residents, emit,
        ))
        state.update(status="error" if errors else "complete", errors=errors)
        atomic_replace_json(path, state)
        return state
