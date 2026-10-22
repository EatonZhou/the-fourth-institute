from __future__ import annotations

from management_portal.portal_access_control import is_active_user_or_resident

import copy
import hashlib
import json
from pathlib import Path

from management_portal.lineage_workflows import WorkflowBindings

from .store import ProjectionError, now


FLOWS = {
    "generate": ("生成投影页面", "设计 Portal 业务内容片段。只交付页面草稿，不修改目标团队、不启动任务、不发布页面。"),
    "optimize": ("优化投影页面", "在保留绑定与有效业务行为的前提下优化页面。只交付草稿，不修改目标团队、不启动任务、不发布页面。"),
    "reverse": ("反投影 · 分析流程", "分析页面行为和现有 Lineage，提出最小可审核增量。复用现有成员、Event、Effect 和历史；纯 UI 不进入图。只分析，不执行修改。"),
}
ALLOWED = {"CreateResident", "CreateComponent", "CreateWire"}


def _delivers_messages_only(script: object) -> bool:
    """A reverse plan's Effect may only deliver messages: every call of its script is DeliverMessage."""

    from management_portal.society_service.lineage_effect_script import ScriptSyntaxError, parse

    if not isinstance(script, str) or not script.strip():
        return False
    try:
        calls = parse(script).calls
    except ScriptSyntaxError:
        return False
    return bool(calls) and all(call.instruction == "DeliverMessage" for call in calls)


def install(service, app, actor):
    binding = app["bindings"]["main"]
    resident_id = binding["residents"]["designer"]
    workflows = WorkflowBindings()
    return {name: workflows.install(actor=actor, lineage_id=binding["lineage_id"], resident_id=resident_id,
                                   name=f"studio:{name}", title=title, prompt=prompt, lend_temporary_credentials=True)
            for name, (title, prompt) in FLOWS.items()}


def plan_hash(plan):
    return hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def validate_plan(value):
    if not isinstance(value, dict) or not isinstance(value.get("summary"), str):
        raise ValueError("反投影结果须包含 summary 和 steps")
    steps = value.get("steps")
    if not isinstance(steps, list) or len(steps) > 30:
        raise ValueError("单次反投影最多 30 个增量步骤")
    seen = set()

    def references(value):
        if isinstance(value, dict):
            if "$ref" in value:
                reference = value["$ref"]
                if set(value) != {"$ref"} or not isinstance(reference, str) or reference.split(".")[0] not in seen:
                    raise ValueError("步骤引用必须指向之前的回执")
            else:
                for child in value.values():
                    references(child)
        elif isinstance(value, list):
            for child in value:
                references(child)

    for step in steps:
        if not isinstance(step, dict) or set(step) != {"id", "instruction", "arguments"}:
            raise ValueError("每个步骤仅接受 id、instruction、arguments")
        label = step["id"]
        if not isinstance(label, str) or not label.isidentifier() or label in seen:
            raise ValueError("步骤 ID 必须唯一且为标识符")
        if step["instruction"] not in ALLOWED or not isinstance(step["arguments"], dict):
            raise ValueError("此版本只接受添加 Resident、Event、Effect 和连接；不允许执行任务、授权、删除或替换现有对象")
        arguments = step["arguments"]
        if step["instruction"] == "CreateComponent":
            config = arguments.get("config") if isinstance(arguments.get("config"), dict) else {}
            if arguments.get("kind") == "event_source" and config.get("event_type") != "manual.triggered":
                raise ValueError("反投影仅创建显式手动触发的入口，不能自动监听现有事件")
            if arguments.get("kind") == "effect" and not _delivers_messages_only(config.get("script")):
                raise ValueError("反投影暂只安装只调用 DeliverMessage 的 Effect")
            if arguments.get("kind") not in {"event_source", "effect"}:
                raise ValueError("反投影只创建手动事件源与消息 Effect")
        if {"actor", "lineage_id"}.intersection(arguments):
            raise ValueError("反投影步骤不得改写身份、团队或权限")
        references(step["arguments"])
        seen.add(label)
    page_update = value.get("page_update", {})
    if not isinstance(page_update, dict) or set(page_update) - {"html", "events", "manifest", "panels", "application"}:
        raise ValueError("page_update 只接受 html、manifest、panels、application 与 events；不允许修改权限或其他团队绑定")
    if page_update.get("application") is not None:
        from .page import validate_application
        validate_application(page_update["application"])
    if "html" in page_update:
        from .content import validate_content
        validate_content(page_update["html"])
    if not isinstance(page_update.get("events", {}), dict):
        raise ValueError("page_update.events 必须是流程别名映射")
    references(page_update.get("events", {}))
    return copy.deepcopy(value)


def resolve_references(value, receipts):
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            path = value["$ref"].split(".")
            result = receipts[path.pop(0)]
            for key in path:
                result = result[key]
            return copy.deepcopy(result)
        return {key: resolve_references(child, receipts) for key, child in value.items()}
    if isinstance(value, list):
        return [resolve_references(child, receipts) for child in value]
    return value


def start_reverse(service, payload, actor):
    from management_portal.lineage_workflows import lineage_graph

    app = service.store.get(str(payload["id"]), actor)
    service.store.require_write(app, actor)
    if not app.get("manifest") and not str(app.get("html") or "").strip():
        raise ProjectionError("projection.empty", "请先保存非空页面，再分析反投影流程", 409)
    target = str(payload.get("lineage_id") or app.get("bindings", {}).get("main", {}).get("lineage_id") or "")
    if target not in {binding["lineage_id"] for binding in app["bindings"].values()}:
        raise ProjectionError("projection.binding_mismatch", "反投影只能整理当前页面已绑定的团队", 409)
    graph = lineage_graph(actor, target)
    if not is_active_user_or_resident(actor):
        raise PermissionError("反投影需要有效的 User 或 Resident 身份")
    request_id = str(payload.get("request_id") or "")
    if not request_id or len(request_id) > 180:
        raise ValueError("请提供稳定的 request_id")
    job_id = hashlib.sha256(f"reverse:{actor}:{request_id}".encode()).hexdigest()[:24]
    with service.lock(f"generation:{job_id}"):
        try:
            return service.store.job(job_id, actor)
        except ProjectionError as error:
            if error.http_status != 404:
                raise
        factory = service.initialize("studio", actor)
        workflow = install(service, factory, actor)["reverse"]
        from .prompt_context import fit_prompt, resource_snapshot
        snapshots = {
            "page": resource_snapshot(app["workbench_id"], app["revision"], {key: app.get(key) for key in ("title", "bindings", "html", "manifest", "panels", "application")}),
            "graph": resource_snapshot(target, graph["revision"], {
                "residents": [{"id": node.get("id"), "title": node.get("title")} for node in graph.get("nodes", [])],
                "program": graph.get("program", {}),
            }),
        }
        instructions = f"""依据输入提出反投影计划，并像普通 Resident 一样用自然语言与用户协作。机器可读计划不要放在聊天正文中；完成后用 HATG ISA 指令 RunResourceAction 提交：
resource_id="{app['workbench_id']}"，action="reverse.plan.save"，arguments={{"job_id":"{job_id}","plan":{{...}}}}。plan 包含 summary、operations（UI/确定性/AI/聊天分类）、reuse、risks、verification、domain_spec_changes、steps。
本条消息附带目标工作台的临时修改凭证，随本次输入结束失效。始终以自己的 Resident 身份调用；目标 Lineage 与其他资源仍按你自身权限检查。此凭证只供保存本次计划，不代表应用计划或发布页面的指令。
""" + """steps 是有序数组，每项仅含 id、instruction、arguments。仅允许 CreateResident、CreateComponent、CreateWire，参数遵循 HATG ISA Schema，lineage_id 由宿主填写。参数内可以用 {"$ref":"先前步骤ID.component_id"} 引用组件回执，新建 Resident 用 {"$ref":"先前步骤ID.resident_id"}。优先复用已有 resident_id，不要猜 ID。没有必要新增时 steps=[]。
固定任务提示词写在 Effect 脚本里 DeliverMessage 的 message 中；本次业务输入由 TriggerEvent 的 prompt 追加。连接 manual.triggered Event Source → 调用 DeliverMessage 的 Effect → Resident。不要为纯筛选或每轮聊天建立 Effect。不得自动触发任务、修改权限、删除对象或发布页面。
创建入口示例：CreateComponent arguments={"kind":"event_source","title":"请求分析","config":{"event_type":"manual.triggered"}}；CreateComponent arguments={"kind":"effect","title":"分析任务","config":{"script":"DeliverMessage resident_id=\\"${endpoint.target}\\" message=\\"固定任务说明\\"","endpoints":["target"]}（Effect 脚本只能调用 DeliverMessage）}。CreateWire arguments={"kind":"route","source":{"$ref":"request.component_id"},"target":{"$ref":"effect.component_id"},"config":{"include_envelope":false}}；target 连接为 {"kind":"target","source":{"$ref":"effect.component_id"},"source_port":"target","target":"真实resident_id"}。
如需同步页面，返回 page_update={"html":"更新后的完整业务片段","events":{"analyze":{"$ref":"request.component_id"}}}。页面调用 Projection.hatg("TriggerEvent", {prompt: 用户输入}, {binding:"main",flow:"analyze"})；不在源码里猜 Event ID。宿主负责确认及固定提示词组合。html 必须遵循 Portal 内容片段规范，不能加入独立模板。page_update 会保存为草稿而非发布。若没有页面变化，省略 page_update。
若 page 未内联 html，请用 ReadResource 读取 resource_address 的页面源码，并确认版本等于 revision；不能仅从标题猜流程。大图也可以按 resource_address 读取。缺少权限、规范、动态绑定机制或无法确定行为时在 risks 说明，不要编造。domain_spec_changes 仅为建议，不声称规范已经修改。
v2 页面同时读取 manifest、panels 及已登记的 application 工程；把 facade 与各功能区动作对应到 Lineage 职责。同步页面时用 page_update.manifest、page_update.panels 和空 html 保留完整的 v2 清单与源码。应用包更新提交 page_update.application，包含仓库、目录、完整提交与入口，由宿主构建；不提交构建回执。
提交成功后，用正常语言概述计划、风险和需要用户确认的步骤，不要把整份 JSON 重复到回复中。
输入："""
        sdk = f"\n实现前先完整读取 SDK 文档：{Path(__file__).with_name('sdk.md')}。应用包、绑定、返回值与错误处理遵循该文档。"
        prompt = fit_prompt(lambda values: instructions + json.dumps({**values, "requirements": str(payload.get("requirements") or "")}, ensure_ascii=False) + sdk, snapshots)
        job = {"id": job_id, "kind": "reverse", "status": "submitting", "workbench_id": app["workbench_id"],
               "base_revision": app["revision"], "target_lineage_id": target, "target_revision": graph["revision"],
               **workflow, "created_at": now(), "request_prompt": prompt}
        from . import generation_submission
        generation_submission.prepare(job)
        service.store.job(job_id, actor, job)
        generation_submission.advance(job, actor=actor, submit=True)
        return service.store.job(job_id, actor, job)


def apply_reverse(service, payload, actor):
    from management_portal.lineage_workflows import lineage_graph

    from .service import isa

    job_id = str(payload["job_id"])
    with service.lock(f"generation:{job_id}"):
        job = service.store.job(job_id, actor)
        if job.get("kind") != "reverse" or "plan" not in job:
            raise ValueError("没有可确认的反投影计划")
        if payload.get("plan_hash") != plan_hash(job["plan"]) or payload.get("confirm") is not True:
            raise ValueError("必须明确确认当前计划的摘要哈希")
        app = service.store.get(job["workbench_id"], actor)
        service.store.require_write(app, actor)
        graph = lineage_graph(actor, job["target_lineage_id"])
        if not is_active_user_or_resident(actor):
            raise PermissionError("需要有效的 User 或 Resident 身份")
        if job.get("applied"):
            return job
        pending_page = job.get("pending_page")
        page_replayed = bool(pending_page and app["revision"] == job["base_revision"] + 1 and all(app.get(key) == value for key, value in pending_page.items()))
        # The plan was analysed against the team at target_revision.  Once its
        # own steps started, the team's change counter moves with them, and the
        # saved step receipts keep the rest of the apply idempotent.
        started = bool(job.get("pending_apply") or job.get("apply_receipts"))
        if (app["revision"] != job["base_revision"] and not page_replayed) or (not started and graph["revision"] != job["target_revision"]):
            raise ProjectionError("projection.reverse_conflict", "页面或团队已改变，请重新分析；已完成步骤不会被回滚", 409)
        plan = validate_plan(job["plan"])
        receipts = job.setdefault("apply_receipts", {})
        for step in plan["steps"]:
            if step["id"] in receipts:
                continue
            pending = job.get("pending_apply") or {"id": step["id"], "actor": actor, "arguments": resolve_references(step["arguments"], receipts)}
            if pending["id"] != step["id"]:
                raise ValueError("未完成步骤与计划不一致")
            if pending.get("actor", job.get("created_by", actor)) != actor:
                raise ProjectionError("projection.pending_actor", "原操作主体的指令回执尚未确认；不能替换身份重放，请先由原主体恢复该步骤。", 409)
            job["pending_apply"] = pending
            if step["instruction"] == "CreateWire":
                wire = pending["arguments"]
                current = lineage_graph(actor, job["target_lineage_id"])
                sources = {component["id"]: component for component in current.get("program", {}).get("components", [])}
                source = sources.get(wire.get("source"), {})
                created_ids = {receipt.get("component_id") for receipt in receipts.values()}
                if wire.get("kind") == "route":
                    if source.get("config", {}).get("event_type") != "manual.triggered":
                        raise ValueError("不能把现有自动事件接入新任务")
                elif wire.get("kind") != "target" or wire.get("source") not in created_ids:
                    raise ValueError("不能修改既有 Effect 目标或添加隐含控制连接")
            service.store.job(job_id, actor, job)
            result = isa(actor, step["instruction"], {"lineage_id": job["target_lineage_id"], **pending["arguments"]},
                         key=f"reverse:{job_id}:{step['id']}")
            receipts[step["id"]] = result
            job.pop("pending_apply")
            service.store.job(job_id, actor, job)
        page_update = plan.get("page_update")
        if page_update and not page_replayed:
            bindings = copy.deepcopy(app["bindings"])
            alias = next(key for key, binding in bindings.items() if binding["lineage_id"] == job["target_lineage_id"])
            bindings[alias].setdefault("events", {}).update(resolve_references(page_update.get("events", {}), receipts))
            changes = {"bindings": bindings, **{key: page_update[key] for key in ("html", "manifest", "panels", "application") if key in page_update}}
            job["pending_page"] = changes
            service.store.job(job_id, actor, job)
            saved = service.update(app["workbench_id"], {**changes, "expected_revision": job["base_revision"]}, actor)
            job["page_revision"] = saved["revision"]
        elif page_replayed:
            job["page_revision"] = app["revision"]
        job.pop("pending_page", None)
        job.update(applied=True, applied_at=now())
        return service.store.job(job_id, actor, job)


def revise_plan(service, payload, actor):
    from management_portal.lineage_workflows import lineage_graph

    job_id = str(payload["job_id"])
    with service.lock(f"generation:{job_id}"):
        job = service.store.job(job_id, actor)
        if job.get("kind") != "reverse" or job.get("applied") or job.get("apply_receipts") or job.get("pending_apply"):
            raise ValueError("只能修订尚未应用的反投影计划")
        if job["status"] not in {"completed", "failed", "cancelled", "conflict"}:
            raise ValueError("请先等待分析结束，再修订计划")
        if str(payload.get("expected_plan_hash") or "") != str(job.get("plan_hash") or ""):
            raise ProjectionError("projection.reverse_conflict", "计划已被修改，请刷新后重试", 409)
        app = service.store.get(job["workbench_id"], actor)
        service.store.require_write(app, actor)
        graph = lineage_graph(actor, job["target_lineage_id"])
        if not is_active_user_or_resident(actor):
            raise PermissionError("需要有效的 User 或 Resident 身份")
        if app["revision"] != job["base_revision"] or graph["revision"] != job["target_revision"]:
            raise ProjectionError("projection.reverse_conflict", "页面或团队已改变，请重新分析", 409)
        plan = validate_plan(payload.get("plan"))
        job.setdefault("plan_history", []).append({"plan": job.get("plan"), "hash": job.get("plan_hash"), "source": job.get("plan_source", "ai"), "error": job.get("error"), "at": now()})
        job.update(plan=plan, plan_hash=plan_hash(plan), plan_source="user", plan_author=actor, status="completed")
        job.pop("error", None)
        return service.store.job(job_id, actor, job)
