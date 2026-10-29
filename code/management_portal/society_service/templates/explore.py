"""Exploration creates an independent Resident and durable candidate Seeds."""

from __future__ import annotations

import json
import logging
import secrets
from copy import deepcopy

from .registry import TemplateError, now, result
from .values import render
from ..seed_state_marker import read_marker, MARKER_NAME
from .. import lineage_aggregate

LOGGER = logging.getLogger(__name__)
MAX_SEEDS_PER_SET = 32
DISMISSED_MARKER = '<!-- ' + MARKER_NAME + ' {"status":"dismissed"} -->'


def _domain():
    from .. import lineage_domain

    return lineage_domain


def _workspace(lineage_id, resident_id):
    return _domain()._node_workspace_path(lineage_id, {"id": resident_id})


def _directory(explorer_id):
    return f"seeds/{explorer_id}"


def _shelf(lineage_id, explorer_id):
    return _domain()._lineage_dir(lineage_id) / _directory(explorer_id)


def _publish(source, target):
    content = source.read_bytes()
    if target.exists() and target.read_bytes() == content:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(
        "." + target.name + "." + secrets.token_hex(4) + ".tmp"
    )
    temporary.write_bytes(content)
    temporary.replace(target)


def _candidate_fork(source_id, seed_id):
    return {
        "id": "edge_candidate_" + secrets.token_hex(6),
        "source": source_id,
        "target": seed_id,
        "type": "candidate_fork",
    }


def _seed(seed_id, title, seed_type, path, actor):
    return {
        "id": seed_id,
        "kind": "seed",
        "title": title,
        "config": {
            "seed_type": seed_type,
            "artifact_path": path,
            "summary": "",
            "status": "active",
            "created_by": actor,
            "created_at": now(),
            "updated_at": now(),
        },
    }


def _files(directory):
    if not directory.is_dir():
        return []
    return [
        path
        for path in sorted(directory.iterdir())
        if path.is_file()
        and not path.is_symlink()
        and path.suffix.lower() == ".md"
        and not path.name.startswith(".")
    ][:MAX_SEEDS_PER_SET]


def _dismissal(path):
    value = read_marker(path.read_text(encoding="utf-8"))
    return value if value and value.get("status") == "dismissed" else None


def sync_explorer(lineage_id, explorer_id, actor):
    directory = _workspace(lineage_id, explorer_id) / _directory(explorer_id)
    shelf = _shelf(lineage_id, explorer_id)
    if not directory.is_dir() or not shelf.is_dir():
        return []
    files = _files(directory)
    prefix = _directory(explorer_id) + "/"
    shelved = {path.name for path in _files(shelf)}
    changed = []

    def candidates(body):
        return {
            item["config"].get("artifact_path"): item
            for item in body.get("program", {}).get("components", [])
            if item.get("kind") == "seed"
            and item.get("config", {}).get("artifact_path", "").startswith(prefix)
        }

    def plan(body):
        seeds = candidates(body)
        additions = []
        dismissals = []
        for path in files:
            seed = seeds.get(prefix + path.name)
            dismissed = _dismissal(path)
            if (
                seed is None
                and path.name not in shelved
                and not dismissed
                and len(shelved) + len(additions) < MAX_SEEDS_PER_SET
            ):
                additions.append(path)
            elif (
                seed
                and dismissed
                and seed["config"].get("status", "active") == "active"
            ):
                dismissals.append((seed, dismissed))
        return additions, dismissals

    lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor)
    if any(plan(lineage)):

        def edit(body):
            changed.clear()
            seeds = candidates(body)
            siblings = list(seeds.values())
            config = siblings[0]["config"] if siblings else {}
            sources = {
                edge["target"]: edge["source"]
                for edge in body.get("edges", [])
                if edge.get("type") in {"candidate_fork", "inheritance"}
            }
            source = next(
                (sources[s["id"]] for s in siblings if s["id"] in sources),
                sources.get(explorer_id, ""),
            )
            additions, dismissals = plan(body)
            for index, path in enumerate(additions, start=len(shelved) + 1):
                seed = _seed(
                    "seed_" + secrets.token_hex(6),
                    f"候选 {index}",
                    config.get("seed_type", "other"),
                    prefix + path.name,
                    explorer_id,
                )
                body.setdefault("program", {}).setdefault("components", []).append(seed)
                if source:
                    body.setdefault("edges", []).append(
                        _candidate_fork(source, seed["id"])
                    )
                changed.append(seed["id"])
            for seed, dismissed in dismissals:
                seed["config"].update(
                    status="dismissed",
                    dismissed_at=now(),
                    dismissed_by=explorer_id,
                    dismissal_source="explorer",
                    updated_at=now(),
                )
                if dismissed.get("reason"):
                    seed["config"]["dismissal_reason"] = str(dismissed["reason"])[:2000]
                changed.append(seed["id"])

        _domain().commit_lineage_edit(lineage_id, actor, edit, reason="同步探索候选")
        lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor)
    kept = candidates(lineage)
    for path in files:
        if path.name in shelved or prefix + path.name in kept:
            _publish(path, shelf / path.name)
    return changed


def sync_after_turn(lineage_ids, resident_id, actor):
    for lineage_id in lineage_ids:
        try:
            if not (
                _workspace(lineage_id, resident_id) / _directory(resident_id)
            ).is_dir():
                continue
            sync_explorer(lineage_id, resident_id, actor)
        except Exception:
            LOGGER.warning(
                "Candidate sync failed for %s in %s",
                resident_id,
                lineage_id,
                exc_info=True,
            )


class ExploreKind:
    key = "explore"
    title = "探索候选"
    targets = ("resident",)

    def normalize(self, template):
        body = template["body"]
        body.setdefault("seed_type", "idea")
        body.setdefault(
            "label", "候选想法" if body["seed_type"] == "idea" else "候选方案"
        )
        body.setdefault("count", 3)
        body.setdefault("task", "提出互有差异的候选，写清内容、依据、风险和建议行动。")
        if (
            body["seed_type"] not in {"idea", "plan", "other"}
            or not isinstance(body["task"], str)
            or not body["task"].strip()
        ):
            raise TemplateError("探索模板需要有效的 seed_type 和 task")
        if not isinstance(body["count"], str) and (
            isinstance(body["count"], bool)
            or not isinstance(body["count"], int)
            or not 1 <= body["count"] <= 12
        ):
            raise TemplateError("候选数量必须是 1–12")
        return template

    def describe(self, template):
        return {
            "steps": [
                "从目标 Fork 一个探索者。",
                f"由探索者写出 {template['body']['count']} 个候选文件，并在来源旁边保留 Seed。",
                "探索者每轮结束后同步候选；采纳候选时从来源 Fork。",
            ],
            "persistent": False,
            "roles": [{"key": "explorer", "title": "探索者", "from": "fork"}],
        }

    def editor(self):
        return {
            "fields": [
                {
                    "name": "task",
                    "type": "text",
                    "label": "探索任务",
                    "multiline": True,
                },
                {"name": "count", "type": "text", "label": "候选数量或参数引用"},
                {
                    "name": "seed_type",
                    "type": "enum",
                    "label": "候选类型",
                    "options": ["idea", "plan", "other"],
                },
                {"name": "label", "type": "text", "label": "候选名称"},
            ],
            "resident_roles": [
                {
                    "key": "explorer",
                    "title": "探索者",
                    "from": "fork",
                    "description": "继承目标 Resident 的 Provider 与上下文",
                }
            ],
        }

    def apply(self, ctx):
        from .prompt import deliver
        from .structure import create_role

        source = ctx.target["resident_ids"][0]
        lineage = lineage_aggregate.read_lineage(ctx.lineage_id, actor=ctx.actor)
        node = next(item for item in lineage["nodes"] if item["id"] == source)
        roles = {"target": {"id": source, "title": node.get("title") or source}}
        body = render(
            ctx.template["body"],
            ctx.parameters,
            roles=roles,
            lineage_id=ctx.lineage_id,
            title=ctx.title,
        )
        count = body["count"]
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or not 1 <= count <= 12
        ):
            raise TemplateError("候选数量必须是 1–12")
        if set(ctx.resident_settings) - {"explorer"}:
            raise TemplateError("探索模板仅接受 explorer 的起始设置")
        explorer = create_role(
            ctx,
            {
                "key": "explorer",
                "from": "fork",
                "fork_of": "target",
                "fork_mode": "full",
                "title": "{{role.target.title}} · {{block.title}}",
            },
            roles,
            source,
        )
        workspace = _workspace(ctx.lineage_id, explorer["id"])
        seeds = []
        paths = []
        for index in range(1, count + 1):
            path = f"{_directory(explorer['id'])}/{body['seed_type']}-{index:02d}.md"
            draft = workspace / path
            draft.parent.mkdir(parents=True, exist_ok=True)
            draft.write_text(
                f"# {body['label']} {index}\n\n探索者正在填写这个候选。\n\n## 候选内容\n\n## 依据\n\n## 主要风险\n\n## 建议行动\n",
                encoding="utf-8",
            )
            paths.append(path)
            seeds.append(
                _seed(
                    "seed_" + secrets.token_hex(6),
                    f"{body['label']} {index}",
                    body["seed_type"],
                    path,
                    ctx.actor,
                )
            )

        def edit(current):
            current.setdefault("program", {}).setdefault("components", []).extend(
                deepcopy(seeds)
            )
            current.setdefault("edges", []).extend(
                _candidate_fork(source, seed["id"]) for seed in seeds
            )

        _domain().commit_lineage_edit(
            ctx.lineage_id, ctx.actor, edit, reason="施加探索模板"
        )
        for path in paths:
            _publish(workspace / path, _domain()._lineage_dir(ctx.lineage_id) / path)
        instructions = "\n".join(
            f"{index}. {workspace / path}" for index, path in enumerate(paths, start=1)
        )
        message = f"{body['task']}\n\n## 候选交付\n每个候选覆盖一个已建好的 Markdown 文件；一级标题作为候选名称。\n{instructions}\n\n每轮结束后系统同步这些文件。新增候选时在同目录创建顶层 Markdown；抛弃候选时在第一行写入 {DISMISSED_MARKER}。不要删除候选文件或改变路径。"
        receipt = deliver(explorer["id"], message, ctx.actor)
        return result(
            f"已新建探索者与 {count} 个候选 Seed。",
            created={
                "resident_ids": [explorer["id"]],
                "component_ids": [seed["id"] for seed in seeds],
                "wire_ids": [],
            },
            delivered=[
                {"resident_id": explorer["id"], "message_id": receipt["message_id"]}
            ],
        )


KIND = ExploreKind()
