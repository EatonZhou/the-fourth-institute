from __future__ import annotations

import logging
import time

from management_portal import resident_handoff
from management_portal.application_bindings import main_lineage, projection_application
from management_portal.society_service import SocietyServiceUnavailable
from management_portal.resources.errors import ResourceServiceUnavailable
from management_portal.hatg.contracts import InstructionError
from management_portal.portal_access_control import is_active_user_or_resident


from .service import bound_lineage_graph, get_service, members
from .store import ProjectionError


def _delivers_fixed_prompt(script: object) -> bool:
    """A functional entry's Effect: one DeliverMessage line with a non-empty fixed prompt."""

    from management_portal.society_service.lineage_effect_script import deliver_script_parts

    parts = deliver_script_parts(script) if isinstance(script, str) else None
    return bool(parts and parts[1].strip())


def validate_native_bindings(app: dict, actor: str) -> dict:
    if "main" not in app["bindings"]:
        raise ProjectionError("projection.lineage_unbound", "此业务页面尚未确认主 Lineage；请先完成业务绑定迁移", 409)
    return with_binding_status(app, actor)


def with_binding_status(app: dict, actor: str) -> dict:
    """The workbench with ``binding_issues`` of its bindings as they are now.

    A binding saved while valid can lose its Lineage or Residents later; the
    page and settings show that instead of an earlier "applied" state.
    """
    issues = []
    for alias, target in (app.get("bindings") or {}).items():
        try:
            available = members(bound_lineage_graph(target, actor))
            for role, resident_id in target.get("residents", {}).items():
                if resident_id not in available:
                    issues.append({"binding": alias, "role": role, "code": "projection.resident_unbound", "message": f"Resident 未绑定：{alias}/{role} → {resident_id}"})
        except ProjectionError as error:
            issues.append({"binding": alias, "code": error.code, "message": str(error)})
    result = {**app, "binding_issues": issues}
    if issues:
        result["binding_notice"] = f"{len(issues)} 项已声明绑定异常：" + "；".join(item["message"] for item in issues[:3])
    else:
        result.pop("binding_notice", None)
    return result


def resolve_role(app: dict, scope: str, actor: str) -> dict | None:
    if sum(scope in binding.get("residents", {}) for binding in app["bindings"].values()) > 1:
        raise ProjectionError("projection.role_ambiguous", f"功能角色存在多个权威绑定：{scope}；请完成迁移", 409)
    for binding in app["bindings"].values():
        resident_id = binding.get("residents", {}).get(scope)
        if resident_id:
            graph = bound_lineage_graph(binding, actor)
            if resident_id not in members(graph):
                raise ProjectionError("projection.resident_unbound", f"Resident 未绑定：{scope} → {resident_id}；请在工作台设置中重新绑定", 409)
            return {"resident_id": resident_id, "lineage_id": binding["lineage_id"]}
    return None


def workflow_definitions(name: str, role: str) -> list[dict]:
    if name == "studio":
        from .studio import FLOWS
        return [{"name": f"studio:{flow}", "title": title, "prompt": prompt}
                for flow, (title, prompt) in FLOWS.items()] if role == "designer" else []
    from management_portal import portal_features

    # A built-in workbench's feature declares its workflows; one left out of the package has none.
    definition = portal_features.contribution(f"workbench.workflows.{name}")
    return definition(role) if definition else []


def activation_report(app: dict, actor: str) -> dict:
    checked = validate_native_bindings(app, actor)
    issues = list(checked["binding_issues"])
    main = app["bindings"]["main"]
    graph = bound_lineage_graph(main, actor)
    if not is_active_user_or_resident(actor):
        raise PermissionError("启用业务入口需要有效的 User 或 Resident 身份")
    if app.get("published_revision") != app.get("revision"):
        issues.append({"code": "projection.binding_draft", "message": "当前绑定修改尚未发布"})
    roles = {}
    for alias, target in app["bindings"].items():
        target_graph = graph if target["lineage_id"] == main["lineage_id"] else bound_lineage_graph(target, actor)
        program = target_graph.get("program", {})
        sources = {item["id"] for item in program.get("components", [])
                   if item.get("kind") == "event_source" and item.get("config", {}).get("event_type") == "manual.triggered"}
        effects = {item["id"] for item in program.get("components", [])
                   if item.get("kind") == "effect" and _delivers_fixed_prompt(item.get("config", {}).get("script"))}
        wired_effects = {wire.get("target") for wire in program.get("wires", [])
                         if wire.get("kind") == "route" and wire.get("source") in sources and wire.get("target") in effects}
        resident_effects = {}
        for wire in program.get("wires", []):
            if wire.get("kind") == "target" and wire.get("source") in wired_effects:
                resident_effects.setdefault(wire.get("target"), set()).add(wire["source"])
        for role, resident_id in target.get("residents", {}).items():
            if role in roles:
                issues.append({"code": "projection.role_ambiguous", "role": role, "message": f"功能角色有多个绑定：{role}"})
            roles[role] = resident_id
            if role.startswith("member:"):
                issues.append({"code": "projection.member_role_unclassified", "role": role, "message": f"扫描成员尚未归类为业务角色：{role}；请核对真实业务职责后显式绑定"})
            if not role.startswith("system:") and target["lineage_id"] != main["lineage_id"]:
                issues.append({"code": "projection.business_role_outside_main", "role": role, "message": f"业务角色不属于主 Lineage：{alias}/{role}"})
            if role.startswith("system:") and target["lineage_id"] == main["lineage_id"]:
                issues.append({"code": "projection.system_role_in_business", "role": role, "message": f"基础能力必须由 Portal 职责登记指定：{role}"})
            required_flows = workflow_definitions(app.get("builtin", ""), role)
            if len(resident_effects.get(resident_id, set())) < len(required_flows):
                issues.append({"code": "projection.workflow_unbound", "role": role, "message": f"尚未完整绑定业务定义的 Event → 固定 prompt Effect → Resident 流程：{role}"})
    required = set()
    if app.get("builtin") == "paper":
        from management_portal.paper_projection import ROLES
        from management_portal.application_targets import ApplicationUnconfigured
        from management_portal.semantic_search_surfaces import search_binding
        required = set(ROLES)
        try:
            search_binding("paper")
        except (ApplicationUnconfigured, KeyError) as error:
            issues.append({"code": "projection.system_capability_unbound", "role": "search.paper",
                           "message": str(error)})
        assignments = [roles[role] for role in ROLES if role in roles]
        if len(assignments) != len(set(assignments)):
            issues.append({"code": "projection.shared_function_resident", "message": "Paper 固定功能必须各自绑定独立 Resident，而不是共用同一执行身份"})
    elif app.get("builtin") == "studio":
        required = {"designer"}
    if not any(not role.startswith("system:") for role in roles):
        issues.append({"code": "projection.empty_business_team", "message": "主业务团队尚无已绑定的功能 Resident，不能启用空团队"})
    for role in sorted(required - roles.keys()):
        issues.append({"code": "projection.resident_unbound", "role": role, "message": f"尚未绑定功能 Resident：{role}"})
    return {"workbench_id": app["workbench_id"], "revision": app["revision"],
            "main_lineage_id": main["lineage_id"], "ready": not issues, "issues": issues}


def binding(name: str, options: dict, backend: str, *, create_role: bool = False) -> dict:
    service = get_service()
    actor = options["actor"]
    app = projection_application(name, actor)
    scope = str(options.get("resident_scope") or "coordinator")
    known = resolve_role(app, scope, actor)
    if known:
        return known
    if not create_role:
        raise ProjectionError("projection.resident_unbound", f"业务功能尚未绑定 Resident：{scope}；请完成角色迁移", 409)
    if app.get("published_revision") != app.get("revision"):
        raise ProjectionError("projection.binding_draft", "页面绑定存在未发布修改；请先发布，再创建业务对话或执行实例", 409)
    app, resident_id = service.require_role(app, actor, scope, title=str(options.get("title") or scope),
        backend=backend, backend_route_id=str(options.get("backend_route_id") or ""),
        settings=options.get("runtime_settings") or options.get("runtime_options"))
    return {"resident_id": resident_id, "lineage_id": main_lineage(app)}


def execution_bindings(name: str, options: dict, backend: str) -> dict:
    definition = options.pop("workflow", None)
    create_role = options.pop("create_role", False)
    if not definition:
        raise ProjectionError("projection.workflow_unbound", "业务 AI 调用必须指定 Event / Effect 工作流", 409)
    from management_portal.lineage_workflows import WorkflowBindings
    cancelled = options.get("cancellation_requested") or (lambda: False)
    pending_instruction = ""
    identity = None
    while True:
        if cancelled():
            raise InterruptedError("业务调用已取消，未发送输入")
        try:
            if identity is None:
                identity = binding(name, options, backend, create_role=create_role)
            workflow = WorkflowBindings().install(actor=options["actor"], **identity, **definition)
            if cancelled():
                raise InterruptedError("业务调用已取消，未发送输入")
            return {"resident_binding": identity, "workflow_binding": workflow}
        except (SocietyServiceUnavailable, ResourceServiceUnavailable):
            pass
        except InstructionError as error:
            if error.code != "instruction.in_progress":
                raise
            instruction_id = str(error.details.get("instruction_id") or "unknown")
            if instruction_id != pending_instruction:
                logging.getLogger(__name__).warning(
                    "Waiting for original workflow Instruction %s; scope=%s business_ref=%s",
                    instruction_id, options.get("resident_scope", ""), options.get("business_ref", ""),
                )
                pending_instruction = instruction_id
        time.sleep(1.0)


def submit(name: str, **options) -> dict:
    bindings = execution_bindings(name, options, options.get("backend", "codex"))
    return resident_handoff.submit_invocation(**options, **bindings)


def lookup(name: str, *, actor: str, resident_scope: str) -> dict:
    if name == "paper":
        # Session reads need one current role, not the settings page's complete
        # binding diagnostics. resolve_role still checks ambiguity, current permissions
        # and live membership on every read, with no cached authorization.
        app = get_service().builtin_application(name, actor)
        main_lineage(app)
    else:
        app = projection_application(name, actor)
    return resolve_role(app, resident_scope, actor) or {}


def ensure(name: str, *, create_role: bool = False, **options) -> dict:
    identity = resident_handoff.ensure_invocation_resident(**options, resident_binding=binding(name, options, options.get("backend", "codex"), create_role=create_role))
    metadata = dict(options.get('metadata') or {})
    if create_role and metadata.get('resource_id'):
        app = get_service().builtin_application(name, options['actor'])
        metadata['workbench_id'] = app['workbench_id']
        metadata['workbench_role'] = options['resident_scope']
        resident_handoff.get_portal_ui_state_store().put(
            options['actor'], resident_handoff.INVOCATION_ASSOCIATION_NAMESPACE,
            options['resident_scope'], {**identity, 'metadata': metadata})
    return identity


def turn(name: str, **options) -> dict:
    bindings = execution_bindings(name, options, options.get("provider", "codex"))
    return resident_handoff.run_resident_turn(**options, **bindings)


def completion(name: str, **options) -> dict:
    bindings = execution_bindings(name, options, "provider_api")
    return resident_handoff.run_resident_completion(**options, **bindings)
