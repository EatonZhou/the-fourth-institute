"""Portal 管理中的 Lineage 功能开关，直接映射到组件运行状态。"""
from __future__ import annotations

from management_common.site_config import read_site_config
from management_portal.society_service import component_runtime

SCHEMA = "tfi.portal-feature-binding.v1"
# Binding semantics only; distribution owns the catalog and default intent.
_TARGETS = {
    "architecture_audit": ("development_projection", "architecture_audit_effect_id", "effect"),
    "pulse": ("pulse", "event_source_ids", "event_source"),
}


class MappingError(ValueError):
    """An absent/ambiguous binding must never look like an effective 'off'."""

    def __init__(self, readiness: str, code: str, message: str):
        super().__init__(f"{readiness}: {message}")
        self.readiness, self.code = readiness, code

    def diagnostic(self) -> dict:
        return {"code": self.code, "message": str(self)}


def mapping_contract(name: str) -> dict:
    """Describe this owner's interface without inspecting any site or runtime."""
    if not isinstance(name, str) or name not in _TARGETS:
        raise ValueError("未知的功能开关")
    section, field, kind = _TARGETS[name]
    return {"schema": SCHEMA, "name": name, "component_kind": kind,
            "site_section": ["deployment", section], "lineage_field": "lineage_id",
            "components_field": field, "cardinality": "one" if kind == "effect" else "nonempty_group",
            "state_owner": "component_runtime", "state_field": "paused"}


def _identifier(value) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _site_binding(name: str, site: dict) -> dict:
    contract = mapping_contract(name)
    deployment = site.get("deployment", {}) if isinstance(site, dict) else None
    section = contract["site_section"][1]
    config = deployment.get(section, {}) if isinstance(deployment, dict) else None
    if not isinstance(config, dict):
        raise MappingError("conflict", "invalid_configuration", f"{name} 的站点配置无效")
    if not _identifier(config.get("lineage_id")):
        raise MappingError("needs_configuration", "missing_lineage", f"{name} 的 Lineage 未配置")
    value = config.get(contract["components_field"])
    if value is None or value == "" or value == [] or value == ():
        raise MappingError("needs_configuration", "missing_components", f"{name} 的组件未配置")
    ids = [value] if contract["cardinality"] == "one" else value
    if not isinstance(ids, (list, tuple)) or any(not _identifier(item) for item in ids):
        raise MappingError("conflict", "invalid_components", f"{name} 的组件配置无效")
    if len(set(ids)) != len(ids):
        raise MappingError("conflict", "duplicate_components", f"{name} 的组件配置重复")
    return {"schema": SCHEMA, "name": name, "lineage_id": config["lineage_id"],
            "components": [{"id": item, "kind": contract["component_kind"]} for item in ids]}


def installation_mapping(name: str, *, site_config: dict, selected_lineage_ids,
                         definition_rows: dict) -> dict:
    """Resolve explicit site references against selected ordinary definitions.

    Rows use distribution's (schema, table) keys: lineage.lineages,
    lineage.lineage_nodes and lineage.lineage_components. Callers pass the
    actual exported selection, not all source definitions. This pure function
    reads neither source runtime nor the ambient site, and writes nothing.
    Ready means a unique mapping, not applied defaults or workflow readiness.
    """
    mapping_contract(name)
    if (not isinstance(selected_lineage_ids, (list, tuple, set, frozenset))
            or any(not _identifier(item) for item in selected_lineage_ids)):
        raise ValueError("selected_lineage_ids 必须显式列出所选普通 Lineage")
    try:
        binding = _site_binding(name, site_config)
        lid = binding["lineage_id"]
        if lid not in selected_lineage_ids:
            raise MappingError("needs_configuration", "lineage_not_selected", "开关所属 Lineage 未带出")
        lineages = [r for r in definition_rows.get(("lineage", "lineages"), [])
                    if r.get("lineage_id") == lid and not r.get("deleted_at")]
        if len(lineages) != 1:
            raise MappingError("conflict" if lineages else "needs_configuration", "lineage_not_unique",
                               "所选 Lineage 缺失或不唯一")
        for component in binding["components"]:
            nodes = [r for r in definition_rows.get(("lineage", "lineage_nodes"), [])
                     if r.get("node_id") == component["id"] and not r.get("deleted_at")]
            definitions = [r for r in definition_rows.get(("lineage", "lineage_components"), [])
                           if r.get("node_id") == component["id"]]
            if len(nodes) != 1 or len(definitions) != 1:
                duplicate = len(nodes) > 1 or len(definitions) > 1
                raise MappingError("conflict" if duplicate else "needs_configuration", "component_not_unique",
                                   f"组件 {component['id']} 缺失或不唯一")
            if nodes[0].get("lineage_id") != lid or nodes[0].get("node_type") != component["kind"]:
                raise MappingError("conflict", "component_mismatch", f"组件 {component['id']} 的归属或类型不符")
    except MappingError as exc:
        return {"schema": SCHEMA, "name": name, "readiness": exc.readiness,
                "binding": None, "site_config": None, "diagnostics": [exc.diagnostic()]}
    contract = mapping_contract(name)
    ids = [c["id"] for c in binding["components"]]
    site = {"deployment": {contract["site_section"][1]: {
        "lineage_id": lid, contract["components_field"]: ids[0] if contract["cardinality"] == "one" else ids}}}
    return {"schema": SCHEMA, "name": name, "readiness": "ready", "binding": binding,
            "site_config": site, "diagnostics": []}


def status(name: str, actor: str) -> dict:
    return _status(_site_binding(name, read_site_config()), actor)


def _status(binding: dict, actor: str) -> dict:
    ids = [c["id"] for c in binding["components"]]
    states = []
    for component in binding["components"]:
        try:
            rows = component_runtime.views(binding["lineage_id"], actor, component_id=component["id"])
        except component_runtime.ComponentRuntimeError as exc:
            raise MappingError("needs_configuration", "component_missing", "开关组件已不存在") from exc
        except KeyError as exc:
            if exc.args != ("Lineage not found",):
                raise
            raise MappingError("needs_configuration", "lineage_missing", "开关所属 Lineage 已不存在") from exc
        if len(rows) != 1:
            raise MappingError("conflict" if rows else "needs_configuration", "component_not_unique", "开关组件缺失或不唯一")
        row = rows[0]
        if row.get("kind") != component["kind"] or row.get("component_id") != component["id"]:
            raise MappingError("conflict", "component_mismatch", "开关组件类型与配置不符")
        if type(row.get("paused")) is not bool:
            raise MappingError("conflict", "invalid_runtime_state", "组件暂停状态无效")
        states.append(row)
    paused = [state["paused"] for state in states]
    # A partial group can still produce new work, so the switch remains on.
    return {"enabled": not all(paused), "partial": any(paused) and not all(paused),
            "components": [{"id": item, "paused": value} for item, value in zip(ids, paused)]}


def set_enabled(name: str, enabled: bool, actor: str) -> dict:
    if type(enabled) is not bool:
        raise ValueError("enabled 必须是布尔值")
    # Bind once: a concurrently changed site file must not switch the read,
    # mutation, rollback or final read to a different set of components.
    binding = _site_binding(name, read_site_config())
    lineage_id = binding["lineage_id"]
    before = _status(binding, actor)
    changed: list[tuple[str, bool]] = []
    try:
        for component in before["components"]:
            if component["paused"] == (not enabled):
                continue
            # The owner can commit and then fail while projecting its result.
            changed.append((component["id"], component["paused"]))
            component_runtime.set_paused(lineage_id, component["id"], not enabled, actor)
    except Exception:
        failed = []
        for component_id, was_paused in reversed(changed):
            try:
                component_runtime.set_paused(lineage_id, component_id, was_paused, actor)
            except Exception:
                failed.append(component_id)
        if failed:
            raise RuntimeError("开关写入失败且部分恢复未确认，请重读组件状态：" + ", ".join(failed))
        raise
    return _status(binding, actor)
