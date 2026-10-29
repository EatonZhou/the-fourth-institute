from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from management_portal import resident_handoff
from management_portal.hatg.contracts import InstructionError
from management_portal.execution.provider_failures import failure_message, is_usage_limit_failure
from management_portal.resources import get_resource_service
from .store import builtins, DEFINITION_FIELDS, FAVORITE_SURFACE, IDENTITY, ProjectionError, Store, validate_manifest


from management_portal import isa_client
from management_portal.lineage_workflows import lineage_graph
from management_common.clock import utc_now_iso


def isa(actor: str, name: str, arguments: dict | None = None, *, key: str = "", version=None):
    """One HATG ISA call on behalf of the page's user."""
    return isa_client.call(actor, name, arguments, key=key, version=version)


def instruction_schema(name: str) -> dict:
    from management_portal.hatg import routing

    return routing.instruction_schema(name)


def bound_lineage_graph(binding: dict, actor: str) -> dict:
    """Report broken bindings consistently in settings and live page calls."""
    lineage_id = binding["lineage_id"]
    try:
        return lineage_graph(actor, lineage_id)
    except (KeyError, PermissionError, InstructionError) as error:
        status = error.http_status if isinstance(error, InstructionError) else 403 if isinstance(error, PermissionError) else 404
        if status not in {403, 404}:
            raise
        code = "projection.lineage_forbidden" if status == 403 else "projection.lineage_unbound"
        message = "无权访问已绑定的 Lineage" if status == 403 else "已绑定的 Lineage 不存在或已删除"
        raise ProjectionError(code, f"{message}：{lineage_id}；请在工作台设置中检查绑定和访问权限，刷新页面无法修复此绑定", status if status == 403 else 409) from error


def members(graph: dict) -> dict[str, dict]:
    return {node.get("id"): node for node in graph.get("nodes", []) if str(node.get("id") or "").startswith("resident_")}


def workbench_record(record: dict) -> dict:
    result = dict(record)
    result["workbench_id"] = result.pop("resource_id")
    return result


def generation_failure_message(failure: object) -> str:
    if is_usage_limit_failure(failure):
        return "当前模型账号额度不足；请求已保留，未修改目标团队。可等待额度恢复，或显式提交人工检查的计划。"
    return failure_message(failure, "生成任务已停止")


class ProjectionService:
    def __init__(self, store: Store | None = None):
        self.store = store or Store()

    def lock(self, scope: str):
        return self.store.lock(scope)

    def create(self, payload: dict, actor: str) -> dict:
        graphs = self.check_bindings(payload.get("bindings", {}), actor)
        projects = sorted({graph["project_id"] for graph in graphs.values() if graph.get("project_id")})
        record = get_resource_service().register("workbench", {**payload, "project_ids": projects or payload.get("project_ids") or []}, actor=actor)
        return workbench_record(record)

    def check_bindings(self, bindings: dict, actor: str) -> dict:
        validate_manifest({"title": "Binding validation", "bindings": bindings})
        graphs = {}
        for alias, binding in bindings.items():
            graph = lineage_graph(actor, binding.get("lineage_id", ""))
            available = members(graph)
            for role, resident in binding.get("residents", {}).items():
                if resident not in available:
                    raise ProjectionError("projection.resident_unbound", f"Resident 未绑定：{role} → {resident}；请在工作台设置中重新绑定", 409)
            sources = {item["id"] for item in graph.get("program", {}).get("components", []) if item.get("kind") == "event_source"}
            if not set(binding.get("events", {}).values()).issubset(sources):
                raise ProjectionError("projection.event_unbound", "流程入口未绑定，请检查 Event Source", 409)
            graphs[alias] = graph
        return graphs

    def check_binding_changes(self, current: dict, prepared: dict, actor: str) -> dict:
        """Validate only binding identities introduced or changed by this update."""
        validate_manifest({"title": "Binding validation", "bindings": prepared})
        graphs = {}
        for alias, binding in prepared.items():
            previous = current.get(alias, {})
            graph = lineage_graph(actor, binding.get("lineage_id", ""))
            available = members(graph)
            previous_residents = previous.get("residents", {}) if previous.get("lineage_id") == binding.get("lineage_id") else {}
            for role, resident in binding.get("residents", {}).items():
                if previous_residents.get(role) == resident:
                    continue
                if resident not in available:
                    raise ProjectionError("projection.resident_unbound", f"Resident 未绑定：{role} → {resident}；请在工作台设置中重新绑定", 409)
            sources = {item["id"] for item in graph.get("program", {}).get("components", []) if item.get("kind") == "event_source"}
            previous_events = previous.get("events", {}) if previous.get("lineage_id") == binding.get("lineage_id") else {}
            changed_events = {event_id for name, event_id in binding.get("events", {}).items() if previous_events.get(name) != event_id}
            if not changed_events.issubset(sources):
                raise ProjectionError("projection.event_unbound", "流程入口未绑定，请检查 Event Source", 409)
            graphs[alias] = graph
        return graphs

    def update(self, app_id: str, payload: dict, actor: str) -> dict:
        return workbench_record(get_resource_service().update(app_id, payload, actor=actor))

    def publish(self, app_id: str, actor: str, revision: int) -> dict:
        return self.update(app_id, {
            "expected_revision": revision, "published_revision": revision,
        }, actor)

    def delete(self, app_id: str, actor: str) -> dict:
        record = get_resource_service().unregister(app_id, actor=actor)
        return {"deleted": record["resource_id"]}

    def apply_update(self, app_id: str, payload: dict, actor: str) -> dict:
        with self.lock(f"updates:{app_id}"):
            current = self.store.get(app_id, actor)
            self.store.require_write(current, actor)
            revision = int(payload["expected_revision"])
            if revision != current["revision"]:
                raise ProjectionError("projection.conflict", "工作台已被更新，请刷新后重试", 409)
            if "activate_builtin" in payload:
                required = {"expected_revision", "activate_builtin", "previous_app_id"}
                if not required <= set(payload) or set(payload) - required - {"builtin"} or payload["activate_builtin"] is not True:
                    raise ProjectionError("projection.activation", "启用入口必须独立提交并提供原入口 ID")
                name = payload.get("builtin", current.get("builtin"))
                if not isinstance(name, str) or name not in builtins():
                    raise ProjectionError("projection.builtin", "未知的内置工作台类型")
                if "main" not in current["bindings"]:
                    raise ProjectionError("projection.lineage_unbound", "启用业务入口前必须确认主 Lineage", 409)
                self.check_bindings(current["bindings"], actor)
                from .native import activation_report
                report = activation_report({**current, "builtin": name}, actor)
                if not report["ready"]:
                    raise ProjectionError("projection.activation_incomplete", "业务迁移未完成：" + "；".join(issue["message"] for issue in report["issues"]), 409)
                return self.store.activate_builtin(app_id, actor, previous_app_id=str(payload["previous_app_id"]), name=name)
            if "published_revision" in payload:
                if set(payload) != {"expected_revision", "published_revision"}:
                    raise ProjectionError("projection.publication", "发布与修改页面定义须分别提交")
                if type(payload["published_revision"]) is not int or payload["published_revision"] != revision:
                    raise ProjectionError("projection.publication", "只能发布当前已保存的版本")
                self.check_bindings(current["bindings"], actor)
                return self.store.publish(app_id, actor, revision)
            unknown = set(payload) - ({"expected_revision"} | DEFINITION_FIELDS)
            if unknown:
                raise ProjectionError(
                    "projection.update_fields",
                    "未知工作台更新字段：" + "、".join(sorted(unknown)),
                )
            if "project_ids" in payload:
                from management_portal import permission_system
                from management_portal.resources.providers.common import explicit_project_ids

                projects = explicit_project_ids(payload["project_ids"], "Workbench")
                allowed = permission_system.permitted_ids(actor, "UpdateProject", "project_id", projects)
                if set(projects) - allowed:
                    raise PermissionError("无权管理目标项目")
                for project in projects:
                    isa(actor, "GetProject", {"project_id": project})
                payload = {**payload, "project_ids": projects}
            prepared = self.store.updated_manifest(current, payload)
            if "bindings" in payload:
                self.check_binding_changes(current["bindings"], prepared["bindings"], actor)
            return self.store.update(app_id, payload, actor, revision)

    def summary(self, app: dict) -> dict:
        url = f"/workbenches/view?id={app['workbench_id']}" if app.get("published_revision") else f"/workbenches?panel=settings&id={app['workbench_id']}"
        return {**{key: value for key, value in app.items() if key not in {"html", "panels"}}, "url": builtins().get(app.get("builtin"), {}).get("path", url)}

    def catalog(self, actor: str, lineage_id: str = "") -> dict:
        from management_portal import presentation_store

        # can_manage is the permission system's current answer, even where the published version is shown.
        entries = [(self.store.get(app["workbench_id"], actor, published=True) if app["published_revision"] else app,
                    self.store.authorized(app, actor, "manage")) for app in self.store.list(actor)]
        if lineage_id:
            entries = [(app, manage) for app, manage in entries
                       if any(binding["lineage_id"] == lineage_id for binding in app["bindings"].values())]
        # native: the entry serves its built-in page now; favorites: the actor's stars, in order.
        native = self.store.native_ids()
        listed = {app["workbench_id"] for app, _manage in entries}
        favorites = [app_id for app_id in presentation_store.favorite_ids(actor, FAVORITE_SURFACE) if app_id in listed]
        return {"actor": actor, "workbenches": [{**self.summary(app), "allows": {"UpdateResource": manage},
                                                 "native": app["workbench_id"] in native,
                                                 "favorite": app["workbench_id"] in favorites} for app, manage in entries],
                "favorites": favorites, "builtins": builtins()}

    def set_favorite(self, app_id: str, favorite: bool, actor: str) -> dict:
        """Star or unstar a workbench in the actor's directory; a star grants nothing."""
        from management_common.principal import is_user_or_resident
        from management_portal import presentation_store

        if not is_user_or_resident(actor):
            raise PermissionError("收藏工作台需要登录")
        if favorite:
            self.store.get(app_id, actor)
        elif not IDENTITY.fullmatch(str(app_id)):
            raise ProjectionError("projection.identity", "工作台 ID 无效")
        return {"favorites": presentation_store.set_favorite(actor, FAVORITE_SURFACE, app_id, favorite)}

    def builtin_application(self, name: str, actor: str) -> dict:
        """Read the authorized published entry without loading its bound graphs."""
        if name not in builtins():
            raise ProjectionError("projection.builtin", "未知内置工作台")
        app = self.store.builtin(name, actor)
        if app is None:
            raise ProjectionError("projection.business_unbound", f"{builtins()[name]['title']} 的权威业务入口尚未启用；请完成真实团队与流程迁移", 409)
        return app

    def initialize(self, name: str, actor: str) -> dict:
        app = self.builtin_application(name, actor)
        from .native import validate_native_bindings
        return validate_native_bindings(app, actor)

    def require_role(self, app: dict, actor: str, role: str, *, title: str = "", backend: str = "codex", backend_route_id: str = "", settings: dict | None = None) -> tuple[dict, str]:
        with self.lock(f"roles:{app['workbench_id']}"):
            app = self.store.get(app["workbench_id"], actor)
            binding = app["bindings"].get("main")
            if not binding:
                raise ProjectionError("projection.lineage_unbound", "工作台尚未绑定 Lineage", 409)
            from management_portal.lineage_workflows import WorkflowBindings

            with WorkflowBindings().lineage_lock(binding["lineage_id"]):
                graph = lineage_graph(actor, binding["lineage_id"])
                resident_id = binding["residents"].get(role)
                if resident_id:
                    if resident_id not in members(graph):
                        raise ProjectionError("projection.resident_unbound", f"Resident 未绑定：{role} → {resident_id}；请重新绑定现有成员", 409)
                    return app, resident_id
                definition = {"title": title or role, "backend": backend, "runtime_settings": settings or {}}
                if backend_route_id:
                    definition["backend_route_id"] = backend_route_id
                identity = hashlib.sha256(role.encode()).hexdigest()[:24]
                dropped: list[str] = []
                if app.get("published_revision") == app["revision"]:
                    dropped = self._drop_deleted_created_roles(app, binding, graph, keep=role)
                    # The new role is published with the page; a binding that can no longer be
                    # published (e.g. a member deleted elsewhere) would leave an unpublished draft
                    # behind and block every later role. Report it before creating anything.
                    self.check_bindings(app["bindings"], actor)
                created = self._create_role(app, actor, identity, definition)
                resident_id = str((created.get("node") or {}).get("id") or "")
                if not resident_id:
                    raise ProjectionError("projection.creation_failed", "HATG 未返回 Resident 身份", 502)
                binding["residents"][role] = resident_id
                was_published = app.get("published_revision") == app["revision"]
                app = self.update(app["workbench_id"], {"bindings": app["bindings"], "expected_revision": app["revision"]}, actor)
                for dropped_role in dropped:
                    self.store.forget_role_request(
                        app["workbench_id"], binding["lineage_id"], hashlib.sha256(dropped_role.encode()).hexdigest()[:24],
                    )
                if was_published:
                    app = self.publish(app["workbench_id"], actor, app["revision"])
                return app, resident_id

    def _drop_deleted_created_roles(self, app: dict, binding: dict, graph: dict, *, keep: str) -> list[str]:
        """Drop roles this workbench created on demand whose Resident has since been deleted.

        WALL creates one role per project and task; when a test or a person deleted
        one of those Residents, the role stayed in the binding and every later role
        creation, for any project, failed the binding check.  A role created on
        demand is created again on demand.  Roles a person bound are not dropped:
        their deletion is still reported.
        """
        available = members(graph)
        dropped = []
        for role, resident_id in list(binding.get("residents", {}).items()):
            if role == keep or resident_id in available:
                continue
            identity = hashlib.sha256(role.encode()).hexdigest()[:24]
            if self.store.role_request(app["workbench_id"], binding["lineage_id"], identity).get("resident_id") != resident_id:
                continue
            del binding["residents"][role]
            dropped.append(role)
        return dropped

    def release_roles(self, app: dict, actor: str, roles: dict[str, str], *, retain: set[str] = frozenset(),
                      preserve_paths: tuple[str, ...] = ()) -> set[str]:
        """Release exact business slots, deleting identities no other slot uses.

        Bindings remain until deletion succeeds, so an interrupted request can
        repeat the same ordinary ISA operations. Never infer ownership by title.
        """
        with self.lock(f"roles:{app['workbench_id']}"):
            app = self.store.get(app['workbench_id'], actor)
            if app.get('published_revision') and app['published_revision'] != app['revision']:
                published = self.store.get(app['workbench_id'], actor, published=True)
                import copy
                expected = copy.deepcopy(published)
                for binding in expected['bindings'].values():
                    binding['residents'] = {role: rid for role, rid in binding.get('residents', {}).items()
                                            if roles.get(role) != rid or rid in retain}
                # Recover only our interrupted binding publication. A user's
                # unrelated draft must never be published as a side effect.
                if all(expected.get(field) == app.get(field) for field in DEFINITION_FIELDS):
                    self.publish(app['workbench_id'], actor, app['revision'])
                    remaining = {rid for b in app['bindings'].values() for rid in b.get('residents', {}).values()}
                    released = set(roles.values()) - retain - remaining
                    for rid in released:
                        resident_handoff.forget_resident_associations(rid)
                    return released
                raise ProjectionError('projection.release_draft', '工作台有未发布修改，请先处理后重试 Resident 清理', 409)
            bindings = app['bindings']
            selected = [(alias, role, rid) for alias, binding in bindings.items()
                        for role, rid in binding.get('residents', {}).items()
                        if roles.get(role) == rid and rid not in retain]
            kept = {rid for binding in bindings.values() for role, rid in binding.get('residents', {}).items()
                    if roles.get(role) != rid or rid in retain}
            targets = {rid: bindings[alias]['lineage_id'] for alias, _, rid in selected if rid not in kept}
            graphs = {lid: lineage_graph(actor, lid) for lid in set(targets.values())}
            for rid, lid in targets.items():
                if rid not in members(graphs[lid]):
                    continue
                found = isa(actor, 'ListResidentStates', {'resident_ids': [rid], 'include_viewer_state': False})['states']
                state = found[0]['state'] if found else {}
                if state.get('work_state') not in {'idle', 'closed'} or state.get('queued_count', 0) or state.get('waiting_count', 0):
                    raise ProjectionError('projection.resident_busy', f'Resident 仍在工作，请稍后重试删除：{rid}', 409)
            from management_portal.resident_handoff import release_resident
            for rid, lid in targets.items():
                release_resident(actor=actor, resident_id=rid, lineage_id=lid, forget_associations=False,
                                 preserve_paths=preserve_paths)
            was_published = app.get('published_revision') == app['revision']
            for alias, role, _ in selected:
                del bindings[alias]['residents'][role]
                self.store.forget_role_request(app['workbench_id'], bindings[alias]['lineage_id'],
                                               hashlib.sha256(role.encode()).hexdigest()[:24])
            if selected:
                app = self.update(app['workbench_id'], {'bindings': bindings, 'expected_revision': app['revision']}, actor)
                if was_published:
                    self.publish(app['workbench_id'], actor, app['revision'])
            for rid in targets:
                resident_handoff.forget_resident_associations(rid)
            return set(targets)

    def _create_role(self, app: dict, actor: str, identity: str, definition: dict) -> dict:
        from management_portal.resident_creation import create

        lineage_id = app["bindings"]["main"]["lineage_id"]
        persisted = self.store.role_request(
            app["workbench_id"], lineage_id, identity
        )
        if persisted and persisted["actor"] != actor:
            raise ProjectionError("projection.creation_actor_mismatch", "角色创建请求主体不匹配；请核对原请求回执", 409)

        def load():
            return {key: value for key, value in persisted.items() if key != "actor"}

        resident_id = create(actor=actor, lineage_id=lineage_id, arguments=definition, load=load,
                             save=lambda state: self.store.save_role_request(
                                 app["workbench_id"], lineage_id, identity, actor, state
                             ))
        current = lineage_graph(actor, lineage_id)
        if resident_id not in members(current):
            raise ProjectionError("projection.resident_unbound", "已创建的 Resident 已移走或删除；请修复绑定", 409)
        return {"node": {"id": resident_id}}

    def dispatch(self, app_id: str, payload: dict, actor: str) -> dict:
        """One HATG ISA call from a Workbench page, bound to the page's Lineage."""
        app = self.store.get(app_id, actor, published=not bool(payload.get("preview")))
        alias = str(payload.get("binding") or "main")
        binding = app["bindings"].get(alias)
        if not binding:
            raise ProjectionError("projection.lineage_unbound", f"Lineage 未绑定：{alias}", 409)
        name = str(payload.get("instruction") or "")
        schema = instruction_schema(name)
        if schema.get("streaming"):
            raise ProjectionError("projection.streaming", "工作台页面不能订阅流式指令，请改为读取一次")
        if schema.get("mutates") and not payload.get("confirmed"):
            raise ProjectionError("projection.confirmation_required", "请先确认工作台请求的 HATG 操作", 409)
        arguments = dict(payload.get("arguments") or {})
        parameters = {item.get("name") for item in schema.get("parameters") or []}
        if "lineage_id" in parameters:
            arguments["lineage_id"] = binding["lineage_id"]
        graph = bound_lineage_graph(binding, actor)
        flow = str(payload.get("flow") or "")
        if flow:
            event_id = binding.get("events", {}).get(flow)
            sources = {item["id"] for item in graph.get("program", {}).get("components", []) if item.get("kind") == "event_source"}
            if name != "TriggerEvent" or not event_id or event_id not in sources:
                raise ProjectionError("projection.event_unbound", f"流程入口未绑定：{flow}", 409)
            arguments["event_source_id"] = event_id
        role = str(payload.get("role") or "")
        if role:
            resident_id = binding.get("residents", {}).get(role)
            if not resident_id or resident_id not in members(graph):
                raise ProjectionError("projection.resident_unbound", f"Resident 未绑定：{role}；请打开工作台设置重新绑定", 409)
            arguments["resident_id"] = resident_id
        referenced = [arguments[key] for key in ("resident_id", "source_resident_id", "target_resident_id") if arguments.get(key)]
        referenced.extend(arguments.get("resident_ids", []))
        for resident_id in referenced:
            if resident_id not in members(graph):
                raise ProjectionError("projection.resident_unbound", f"Resident 未绑定：{resident_id}", 409)
        return isa(actor, name, arguments, key=str(payload.get("idempotency_key") or ""),
                   version=payload.get("expected_version"))

    def start_generation(self, payload: dict, actor: str) -> dict:
        request_id = str(payload.get("request_id") or "")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{12,80}", request_id):
            raise ProjectionError("projection.request_id", "生成请求需要稳定的 request_id")
        job_id = hashlib.sha256(f"{actor}:{request_id}".encode()).hexdigest()[:32]
        with self.lock(f"generation:{job_id}"):
            try:
                return self.store.job(job_id, actor)
            except ProjectionError as error:
                if error.http_status != 404:
                    raise
            target = str(payload.get("lineage_id") or "")
            graph = lineage_graph(actor, target)
            app_id = str(payload.get("workbench_id") or "")
            coordinator = graph.get("leader_resident_id") or next(iter(members(graph)), "")
            # A new Workbench takes the bound Lineage's Project; a Lineage outside any Project makes it public.
            app = self.store.get(app_id, actor) if app_id else self.create({"title": str(payload.get("title") or graph["title"] + "工作台"), "description": str(payload.get("requirements") or ""), "project_ids": ["@public"], "bindings": {"main": {"lineage_id": target, "residents": {"coordinator": coordinator} if coordinator else {}}}}, actor)
            # Delivery lends this page's write permission for the designer's input.
            self.store.require_write(app, actor)
            if app["bindings"].get("main", {}).get("lineage_id") != target:
                raise ProjectionError("projection.binding_mismatch", "所选团队与页面绑定不同，请先在高级设置中更新并保存绑定", 409)
            factory = self.initialize("studio", actor)
            factory, resident_id = self.require_role(factory, actor, "designer", title="前端投影设计师")
            prompt = self.generation_prompt(app, graph, str(payload.get("requirements") or ""), job_id)
            from . import generation_submission
            from .studio import install
            workflow = install(self, factory, actor)["optimize" if app.get("html") or app.get("manifest") else "generate"]
            job = {"id": job_id, "status": "submitting", "workbench_id": app["workbench_id"], "base_revision": app["revision"], **workflow, "message_id": "", "created_at": utc_now_iso(), "request_prompt": prompt}
            generation_submission.prepare(job)
            self.store.job(job_id, actor, job)
            generation_submission.advance(job, actor=actor, submit=True)
            return self.store.job(job_id, actor, job)

    def generation_prompt(self, app: dict, graph: dict, requirements: str, job_id: str) -> str:
        from .prompt_context import fit_prompt, resource_snapshot

        sdk_path = Path(__file__).with_name("sdk.md")
        context = {
            "lineage_id": graph["lineage_id"],
            "title": graph["title"],
            "description": graph.get("description"),
            "residents": [
                {key: node.get(key) for key in ("id", "title", "status")}
                for node in members(graph).values()
            ],
        }
        snapshots = {
            "page": resource_snapshot(app["workbench_id"], app["revision"], {key: app.get(key) for key in ("title", "bindings", "html", "manifest", "panels", "application")}),
            "graph": resource_snapshot(graph["lineage_id"], graph.get("revision", 0), context),
        }
        def render(values):
            return f"""你是 Portal 前端投影工坊中的普通 Resident。请像其他 Resident 一样与用户自然协作，并直接实现现有 Workbench 页面，不要把页面源码或机器 JSON 当作聊天回复。
本次只修改工作台 {app['workbench_id']} 的业务页面代码；应用包可修改其已登记仓库目录内的业务源码。不要启动目标团队任务，不修改目标团队、其他 Portal 源码或其他资源，也不要发布页面。
本条任务消息附带目标工作台的临时修改凭证，随本次输入结束失效；始终以自己的 Resident 身份调用，不借用发起人身份。其他资源仍按你自身权限检查。
先读取工作台及引用资源，核对基线 revision {app['revision']}。完成后用 HATG ISA 指令 RunResourceAction 提交本次草稿：
resource_id="{app['workbench_id']}"，action="generation.save"，arguments={{"job_id":"{job_id}","base_revision":{app['revision']},"title":"...","description":"...","html":"..."}}。title、description、html 是要保存的页面字段；动作会校验任务身份、基线版本和页面代码，并产生正式回执。
提交成功后用正常语言说明实现了什么、如何验证以及页面仍未发布；不要在回复中重复完整源码。遇到信息不足、权限失败或版本冲突时直接解释阻断，不提交假成功或空页面。
已有 v2 页面必须保持页面清单与多功能区结构；提交 manifest 和 panels，并令 html 为空。应用包额外提交 application（已登记仓库、目录、完整 commit、构建入口）；宿主在保存时构建，不接受外部构建回执。先提交源码再保存修订。清单和全部面板源码一次保存。facade、context 与 sidebar 的对应关系遵循 SDK。
以下片段约束只适用于 html/panels。应用包使用多文件工程与自包含构建入口，加载规则遵循 SDK。
html 只包含业务内容片段和内联业务脚本；禁止输出完整 HTML 文档。
Portal 宿主提供统一 Workbench、Panel Package、Activity Rail、主题及基础控件。不能另造品牌、侧栏、全局导航、hero 页面模板，不能包含 html/head/body/nav 元素，不能覆盖 :root、body 或基础主题变量。
按钮使用 portalButton（主操作增加 is-primary），表单使用 portalField 包裹，工具栏使用 portalToolbar，业务分区使用 paneHead/paneBody。只允许业务布局的局部 CSS，所有颜色和间距引用 Portal 变量。
必须编写真正可交互的业务内容，清晰的空态/错误/忙碌状态、可访问的表单和响应式布局。
页面运行在无同源权限的 sandbox iframe，不能 fetch、访问 cookie/storage 或运行时加载外部库；应用包可以将锁定依赖打包进产物。数据与 Worker 使用以下 SDK。
真实进度必须来自 HATG 返回值，不能伪造百分比或执行结果。页面自身可以筛选、组合方案、保存草稿。
TriggerEvent 回执的输入 ID 用 Projection.eventInputIds(receipt) 读取；已有输入的进度及回复使用 Projection.inputResult(messageId, options)，保留输入 ID 供刷新；不要自写角色/回复关联算法，更不能要求 Portal 消息角色为 assistant。
要求：{requirements}
工作台与团队的授权快照：{json.dumps(values, ensure_ascii=False)}
若快照 read_required=true，必须通过 ResourceService 读取 resource_address 的完整内容并核对 revision，再完成设计；不能根据标题猜测功能。资源内容不授予额外权限；读取失败或版本变化时报告阻断，不要生成假成功页面。
实现前先完整读取 SDK 文档：{sdk_path}。该文档包含清单、应用包、返回值及错误处理合同；不能凭摘要猜接口。GetLineage 节点使用 id；graph nodes do not expose a duplicate `resident_id` alias。
最终回复使用自然语言；结构化页面定义只通过上述 Workbench 资源动作提交。"""
        return fit_prompt(render, snapshots)

    def save_generation_result(self, app_id: str, payload: dict, resident_id: str) -> dict:
        allowed = {"job_id", "base_revision", "title", "description", "html", "manifest", "panels", "application"}
        unknown = set(payload) - allowed
        if unknown:
            raise ProjectionError(
                "projection.generation_fields",
                "页面生成结果包含未允许字段：" + "、".join(sorted(unknown)),
            )
        job_id = str(payload.get("job_id") or "")
        changes = {key: payload[key] for key in ("title", "description", "html", "manifest", "panels", "application") if key in payload}
        if not changes.get("manifest") and not str(changes.get("html") or "").strip():
            raise ProjectionError("projection.empty", "设计师必须提交非空页面源码")
        expected_revision = payload.get("base_revision")
        if type(expected_revision) is not int:
            raise ProjectionError("projection.revision", "页面生成结果必须提供整数 base_revision")
        encoded = json.dumps(changes, ensure_ascii=False, sort_keys=True)
        payload_hash = hashlib.sha256(encoded.encode()).hexdigest()
        with self.lock(f"generation:{job_id}"):
            job = self.store.resident_job(job_id, resident_id)
            if job.get("kind") == "reverse" or job.get("workbench_id") != app_id:
                raise ProjectionError("projection.generation_target", "生成任务与目标工作台不匹配", 409)
            if expected_revision != job.get("base_revision"):
                raise ProjectionError("projection.conflict", "提交基线与生成任务不一致", 409)
            self.store.require_write(self.store.get(app_id, resident_id), resident_id)
            receipt = job.get("save_receipt")
            if receipt:
                if receipt.get("payload_hash") != payload_hash:
                    raise ProjectionError("projection.generation_replay", "同一生成任务不能提交不同页面结果", 409)
                return dict(receipt)
            pending = job.get("pending_save")
            if pending and (pending.get("payload_hash") != payload_hash or pending.get("saved_by") != resident_id):
                raise ProjectionError("projection.generation_replay", "生成任务已有不同的待确认页面结果", 409)
            current = self.store.get(app_id, resident_id)
            replayed = bool(
                pending
                and current["revision"] == expected_revision + 1
                and all(current.get(key) == value for key, value in changes.items())
            )
            if current["revision"] == expected_revision:
                self.store.updated_manifest(current, changes)
            elif not replayed:
                raise ProjectionError("projection.conflict", "工作台已被其他修改更新，请重新读取后再实现", 409)
            if not pending:
                pending = {"payload_hash": payload_hash, "changes": changes, "saved_by": resident_id}
                job["pending_save"] = pending
                self.store.job(job_id, job["created_by"], job)
            if not replayed:
                saved = self.apply_update(app_id, {**changes, "expected_revision": expected_revision}, resident_id)
            else:
                saved = current
            receipt = {
                "job_id": job_id,
                "workbench_id": app_id,
                "revision": saved["revision"],
                "published_revision": saved.get("published_revision"),
                "payload_hash": payload_hash,
                "saved_by": resident_id,
            }
            job.pop("pending_save", None)
            job["save_receipt"] = receipt
            self.store.job(job_id, job["created_by"], job)
            return dict(receipt)

    def save_reverse_plan(self, app_id: str, payload: dict, resident_id: str) -> dict:
        if set(payload) != {"job_id", "plan"}:
            raise ProjectionError("projection.reverse_fields", "反投影提交只接受 job_id 与 plan")
        from .studio import plan_hash, validate_plan

        job_id = str(payload.get("job_id") or "")
        plan = validate_plan(payload.get("plan"))
        digest = plan_hash(plan)
        with self.lock(f"generation:{job_id}"):
            job = self.store.resident_job(job_id, resident_id)
            if job.get("kind") != "reverse" or job.get("workbench_id") != app_id:
                raise ProjectionError("projection.reverse_target", "反投影任务与目标工作台不匹配", 409)
            self.store.require_write(self.store.get(app_id, resident_id), resident_id)
            receipt = job.get("plan_receipt")
            if receipt:
                if receipt.get("plan_hash") != digest:
                    raise ProjectionError("projection.reverse_replay", "同一反投影任务不能提交不同计划", 409)
                return dict(receipt)
            job.update(
                plan=plan,
                plan_hash=digest,
                plan_source="ai",
                plan_author=resident_id,
                plan_receipt={"job_id": job_id, "workbench_id": app_id, "plan_hash": digest, "saved_by": resident_id},
            )
            self.store.job(job_id, job["created_by"], job)
            return dict(job["plan_receipt"])

    def generation(self, job_id: str, actor: str) -> dict:
        with self.lock(f"generation:{job_id}"):
            job = self.store.job(job_id, actor)
            if job["status"] in {"completed", "failed", "cancelled", "conflict"}:
                return job
            if job["status"] == "submitting" and not job.get("message_id") and job.get("request_prompt"):
                if job.get("created_by", actor) != actor:
                    return {**job, "recovery_notice": "原请求尚未取得投递回执；请由原提交者恢复观察，不能替换请求身份重投。"}
                from . import generation_submission
                if not job.get("receipt"):
                    generation_submission.prepare(job)
                    self.store.job(job_id, actor, job)
                generation_submission.advance(job, actor=actor)
                self.store.job(job_id, actor, job)
            if not job.get("message_id"):
                return job
            try:
                observed = resident_handoff.observe_invocation(
                    actor=actor, resident_id=job["resident_id"], lineage_id=job["lineage_id"], message_id=job["message_id"])
            except resident_handoff.ResidentInputTerminated as error:
                failure = error.input_state.get("error")
                job.update(status=error.status, input_state=error.status,
                           error=generation_failure_message(failure), input_error=failure)
                return self.store.job(job_id, actor, job)
            state, result = observed["input_state"], observed["result"]
            job.update(input_state=state.get("state"), input_updated_at=state.get("updated_at"))
            if state.get("state") == "queued":
                found = isa(actor, "ListResidentStates", {"resident_ids": [job["resident_id"]], "include_viewer_state": False})
                job["resident_state"] = next(iter(found.get("states") or []), {})
            if result is not None:
                text = result["message"]
                receipt = job.get("plan_receipt" if job.get("kind") == "reverse" else "save_receipt")
                current = None if receipt or job.get("kind") == "reverse" else self.store.get(job["workbench_id"], actor)
                if current is not None and current["revision"] != job.get("base_revision"):
                    # The page changed while the designer worked; it correctly refused to overwrite.
                    job.update(
                        status="conflict",
                        error=(f"生成期间工作台已被修改（基线 v{job.get('base_revision')} → 当前 v{current['revision']}），"
                               "设计师没有覆盖这些修改；请在最新版本上重新生成"),
                        response=text[:400_000],
                    )
                elif not receipt:
                    job.update(
                        status="failed",
                        error=("设计师已结束，但未通过 Workbench 资源动作提交反投影计划" if job.get("kind") == "reverse" else "设计师已结束，但未通过 Workbench 资源动作保存页面"),
                        response=text[:400_000],
                    )
                else:
                    job.update(status="completed", response=text[:400_000])
                    if job.get("kind") != "reverse":
                        job["revision"] = receipt["revision"]
            return self.store.job(job_id, actor, job)


@lru_cache(maxsize=1)
def get_service() -> ProjectionService:
    return ProjectionService()
