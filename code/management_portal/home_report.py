"""Read-only Home reports. Domain owners retain all facts and permissions.

A report is a presentation snapshot, not a task status or a second fact store.
Every source records the ISA read used to reopen it with the current principal.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Callable

from management_portal.hatg.contracts import InstructionContext
from management_portal.hatg.routing import execute_or_raise

FRONTIER_KEY = "exploration.frontier.v1"
MAX_ROWS = 80
STATE_LABELS = {"running": "推进中", "queued": "排队中", "pending": "已接收",
                "completed": "已有回复", "failed": "执行失败", "cancelled": "已取消"}
STANCE_LABELS = {"candidate": "候选", "focus": "主攻", "explore": "探索",
                 "watch": "观望", "concluded": "已收束", "dropped": "已放弃"}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def text(value, limit=4000) -> str:
    return str(value or "")[:limit]


def normalize_target(target: dict) -> dict:
    if not isinstance(target, dict) or target.get("kind") not in {"task", "lineage", "project"}:
        raise ValueError("请选择任务、Lineage 或 Project")
    kind, identifier = target["kind"], text(target.get("id"), 300)
    if kind == "task":
        valid = re.fullmatch(r"resident_[a-f0-9]{12}/msg_[a-f0-9]{12,64}", identifier)
    elif kind == "lineage":
        valid = re.fullmatch(r"lineage_[a-f0-9]{12}", identifier)
    else:
        valid = re.fullmatch(r"[\w.@+\-]{1,80}", identifier)
    if not valid:
        raise ValueError("目标标识无效；任务使用 resident_ID/msg_ID")
    return {"kind": kind, "id": identifier, **({"focus": text(target["focus"], 200)} if target.get("focus") else {})}


def isa_reader(actor: str) -> Callable:
    context = InstructionContext(caller=actor, entry="web")
    return lambda instruction, arguments: execute_or_raise(
        {"instruction": instruction, "arguments": arguments}, context)


class ReportReader:
    def __init__(self, read: Callable):
        self.read = read
        self.sources = {}
        self.warnings = []
        self.resolved = {}
        self.relations = []

    def source(self, instruction, arguments, label, value, *, address="", version=None):
        key = digest([instruction, arguments])[:20]
        self.sources[key] = {"id": key, "label": text(label, 180), "address": address,
                             "read": {"instruction": instruction, "arguments": arguments},
                             "version": str(version if version is not None else digest(value)),
                             "version_kind": "revision" if version is not None else "content_sha256"}
        return key

    def resolve(self, address):
        if address in self.resolved:
            return self.resolved[address]
        result = self.read("ResolveResource", {"address": address})
        resource = result.get("resource") or result
        key = self.source("ResolveResource", {"address": address}, resource.get("title") or address,
                          resource, address=address, version=resource.get("version"))
        self.resolved[address] = key
        return key

    def task(self, identifier):
        resident, message = identifier.split("/")
        args = {"resident_id": resident, "message_id": message}
        original = self.read("GetMessage", args)["message"]
        state = self.read("GetInputState", args)["input_state"]
        body = "\n".join(p.get("text", "") for p in original.get("content_parts", []) if p.get("type") == "text")
        source = self.source("GetMessage", args, "原始任务全文", original)
        status_source = self.source("GetInputState", args, "输入处理状态", state)
        replies, refs = [], [source, status_source]
        for reply_id in state.get("reply_message_ids", [])[-5:]:
            try:
                reply_args = {"resident_id": resident, "message_id": reply_id}
                reply = self.read("GetMessage", reply_args)["message"]
                refs.append(self.source("GetMessage", reply_args, "原始回复 " + reply_id, reply))
                replies.append({"message_id": reply_id, "text": "\n".join(
                    p.get("text", "") for p in reply.get("content_parts", []) if p.get("type") == "text")})
            except Exception:
                self.warnings.append("有回复当前不可读；仅保留可核对的请求与处理状态。")
        row = {"id": identifier, "title": "任务请求", "body": text(body), "group": "任务",
               "request_text": body, "replies": replies,
               "status": STATE_LABELS.get(state.get("state"), "待核实"),
               "next": "展开原始请求和回复，逐项核对交付内容。", "sources": refs,
               "open": {"view": "report", "target": {"kind": "task", "id": identifier}}}
        updated = state.get("updated_at") or original.get("created_at")
        row["updated_at"] = updated
        if state.get("error"):
            row["detail"] = text(state["error"])
        return "任务汇报", "执行状态由消息处理服务提供；已有回复与业务完成分别判断。", [row], []

    def lineage(self, identifier):
        data = self.read("GetLineage", {"lineage_id": identifier})["lineage"]
        definition = {key: data.get(key) for key in ("title", "description", "revision", "updated_at")}
        src = self.source("GetLineage", {"lineage_id": identifier}, data.get("title") or identifier,
                          definition, address=identifier, version=data.get("revision"))
        rows = self.frontier(identifier, src)
        return data.get("title") or identifier, text(data.get("description")), rows, self.relations

    def frontier(self, identifier, lineage_source):
        args = {"lineage_id": identifier, "key": FRONTIER_KEY}
        try:
            doc = self.read("GetSharedState", args).get("value")
        except Exception:
            self.warnings.append("部分工作线当前无法读取，未计入报告。")
            return []
        if doc is None:
            return []
        if not isinstance(doc, dict) or doc.get("schema") != "tfi.exploration-frontier.v1" or not isinstance(doc.get("items"), list):
            self.warnings.append("工作线格式不受支持，未将其解释为进展。")
            return []
        source = self.source("GetSharedState", args, "探索前沿工作线", doc, address=identifier)
        rows = []
        items = doc["items"]
        if len(items) > MAX_ROWS:
            self.warnings.append(f"本次读取前 {MAX_ROWS} 条工作线；完整内容可在来源中查看。")
        for item in items[:MAX_ROWS]:
            refs = item.get("sources")
            if not isinstance(refs, list) or not refs:
                self.warnings.append("缺少可核对来源的工作线未展示。")
                continue
            try:
                sources = [self.resolve(address) for address in refs]
            except Exception:
                self.warnings.append("部分工作线的来源不可读，相关内容已撤去。")
                continue
            state = None
            dispatch = item.get("dispatch")
            if dispatch:
                try:
                    args = {"resident_id": dispatch["owner"], "message_id": dispatch["message_id"]}
                    state = self.read("GetInputState", args)["input_state"]
                    sources.append(self.source("GetInputState", args, "工作线执行状态", state))
                except Exception:
                    self.warnings.append("部分执行状态未能核对，保留为待核实。")
            status = ("已取消" if item["closed"] == "cancelled" else "已收束") if item.get("closed") else (
                STATE_LABELS.get((state or {}).get("state"), "待核实") if dispatch else "尚未投递")
            rows.append({"id": f'{identifier}/{item.get("id", "")}', "title": text(item.get("title"), 200),
                         "group": text(item.get("line") or "工作线", 200), "body": text(item.get("progress")),
                         "detail": text(item.get("reason")), "next": text(item.get("next")), "status": status,
                         "attention": item.get("attention"), "updated_at": item.get("updated_at"),
                         "sources": [lineage_source, source, *sources],
                         "open": {"view": "frontier", "target": {"kind": "lineage", "id": identifier, "focus": item.get("id")}}})
        visible = {row["id"] for row in rows}
        for item in items[:MAX_ROWS]:
            if not item.get("parent"):
                continue
            edge = {"from": f'{identifier}/{item["id"]}', "to": f'{identifier}/{item["parent"]}', "kind": item.get("relation")}
            if edge["from"] not in visible or edge["to"] not in visible or not edge["kind"]:
                self.warnings.append("部分工作线关系的端点或类型不完整，未据此推断完整影响。")
                continue
            self.relations.append(edge)
        return rows

    def project(self, identifier):
        args = {"project_id": identifier, "include_resources": False}
        info = self.read("GetProject", args)
        project = info["project"]
        basic = {key: project.get(key) for key in ("title", "description", "status", "modified_at")}
        source = self.source("GetProject", args, project.get("title") or identifier, basic)
        try:
            research = self.read("GetResearchMap", {"project_id": identifier, "include_outputs": True})
        except Exception:
            self.warnings.append("研究地图当前无法读取；仍可查看已读取的项目与团队工作线。")
            research = {}
        map_source = self.source("GetResearchMap", {"project_id": identifier, "include_outputs": True},
                                 "研究地图", research, version=research.get("revision")) if research else None
        rows = []
        directions = research.get("directions", [])
        if len(directions) > MAX_ROWS:
            self.warnings.append(f"本次读取前 {MAX_ROWS} 个方向；完整地图可在来源中查看。")
        for direction in directions[:MAX_ROWS]:
            key = direction["direction_id"]
            refs = [source, map_source]
            artifacts = []
            for output in research.get("outputs", {}).get(key, [])[:8]:
                try:
                    ref = self.resolve(output["resource_id"])
                except Exception:
                    continue
                refs.append(ref)
                artifacts.append(ref)
            conclusion = direction.get("conclusion") or {}
            notes = []
            for note in sorted((n for n in research.get("notes", []) if n.get("direction_id") == key),
                               key=lambda n: n.get("noted_at", ""), reverse=True)[:3]:
                try:
                    refs.extend(self.resolve(address) for address in note.get("refs", []))
                except Exception:
                    continue
                notes.append(text(note.get("text"), 800))
            rows.append({"id": key, "title": text(direction.get("title"), 200), "group": "研究方向",
                         "body": text(direction.get("question")), "detail": text(direction.get("approach")),
                         "next": text(direction.get("expectation")),
                         "conclusion": text(conclusion.get("text")), "notes": notes,
                         "status": STANCE_LABELS.get(direction.get("stance"), "待核实"),
                         "updated_at": direction.get("updated_at"), "sources": refs, "artifacts": artifacts,
                         "open": {"view": "map", "target": {"kind": "project", "id": identifier, "focus": key}}})
        # Project work continues before a research map is populated. Existing
        # team worklines remain visible, grouped by their registered direction
        # or explicitly as unassigned; no new research direction is invented.
        teams = {team["lineage_id"]: team.get("title") or team["lineage_id"] for team in info.get("team", {}).get("lineages", [])}
        project_team = info.get("team", {}).get("project_lineage_id")
        if project_team:
            teams[project_team] = "项目团队"
        direction_teams = {}
        for direction in directions:
            for team in direction.get("teams", []):
                direction_teams.setdefault(team, []).append(direction.get("title") or direction["direction_id"])
                teams.setdefault(team, team)
        if len(teams) > 40:
            self.warnings.append("本次读取前 40 个团队；完整范围请打开项目查看。")
        for team, name in list(teams.items())[:40]:
            worklines = self.frontier(team, source)
            for row in worklines:
                row["group"] = (" / ".join(direction_teams[team]) if team in direction_teams else "未关联研究方向") + " · " + name + " · " + row["group"]
            rows.extend(worklines)
        if len(rows) > MAX_ROWS:
            self.warnings.append(f"本次展示前 {MAX_ROWS} 项，完整内容可在原地图与工作线中查看。")
            rows = rows[:MAX_ROWS]
        visible = {r["id"] for r in rows}
        relations = [{k: r.get(k) for k in ("from", "to", "kind")} for r in research.get("relations", [])
                     if r.get("from") in visible and r.get("to") in visible]
        if len(relations) != len(research.get("relations", [])):
            self.warnings.append("部分研究关系的端点不在本次可读范围，未据此推断完整影响。")
        relations.extend(r for r in self.relations if r["from"] in visible and r["to"] in visible)
        return project.get("title") or identifier, text(research.get("goal") or project.get("description")), rows, relations


def build_report(target: dict, *, actor: str = "", read: Callable | None = None) -> dict:
    started = time.monotonic()
    target = normalize_target(target)
    reader = ReportReader(read or isa_reader(actor))
    title, goal, rows, relations = getattr(reader, target["kind"])(target["id"])
    report = {"schema": "tfi.home-report.v1", "target": target, "title": title, "goal": goal,
              "rows": rows, "relations": relations, "counts": dict(Counter(r["status"] for r in rows)),
              "sources": list(reader.sources.values()), "warnings": list(dict.fromkeys(reader.warnings)),
              "empty": not rows}
    from management_portal.home_report_reasoning import build_reasoning
    report["reasoning"] = build_reasoning(report)
    report["version"] = digest(report)
    report["checked_at"] = datetime.now(timezone.utc).isoformat()
    report["read_seconds"] = round(time.monotonic() - started, 3)
    return report


def reauthorize(report: dict, *, actor: str = "", read: Callable | None = None):
    """No cached permission grant: reopen every dependency before serving media."""
    call = read or isa_reader(actor)
    for source in report["sources"]:
        item = source["read"]
        call(item["instruction"], item["arguments"])
