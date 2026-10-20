from __future__ import annotations

from copy import deepcopy

from management_common.portal_paths import path as portal_path
from management_common.portal_network import DEFAULT_PORT, router_port


PORTAL_PORT = DEFAULT_PORT

_SHARE = portal_path("share_root")
_STAGING = portal_path("staging_root")

PATH_PREFIXES = {
    "projects": {
        "from": "/Projects",
        "to": str(_SHARE / "Projects"),
    },
    "codes": {
        "from": "/Codes",
        "to": str(_SHARE / "Codes"),
    },
}


def _rewrite_path_prefix(raw: str, prefix: dict[str, str]) -> str | None:
    source = prefix["from"]
    target = prefix["to"]
    if raw == source:
        return target
    if raw.startswith(f"{source}/"):
        return f"{target}{raw[len(source):]}"
    return None


SHARED_PATHS = {
    "share_root": str(_SHARE),
    "projects_root": str(_SHARE / "Projects"),
    "codes_root": str(_SHARE / "Codes"),
    "agent_staging_root": str(_STAGING),
}

MODULES = [
    {
        "id": "knowledge",
        "title": "Knowledge",
        "route": "/knowledge",
        "entry_port": PORTAL_PORT,
        "entry_path": "/knowledge/",
        "requires_auth": False,
        "write_capabilities": [],
        "handler_kind": "mounted-board",
        "summary_label": "Knowledge board",
    },
    {
        "id": "projects",
        "title": "Project",
        "route": "/projects",
        "entry_port": PORTAL_PORT,
        "entry_path": "/projects/",
        "requires_auth": False,
        "write_capabilities": ["create-project", "update-status", "update-ddl", "update-project-type"],
        "handler_kind": "mounted-board",
        "summary_label": "Project board",
    },
    {
        "id": "skills",
        "title": "Skills",
        "route": "/skills",
        "entry_port": PORTAL_PORT,
        "entry_path": "/skills/",
        "requires_auth": False,
        "write_capabilities": ["edit-skill"],
        "handler_kind": "mounted-board",
        "summary_label": "Skills board",
    },
    {
        "id": "lineage",
        "title": "Lineage",
        "route": "/lineage",
        "entry_port": PORTAL_PORT,
        "entry_path": "/lineage",
        "requires_auth": False,
        "write_capabilities": ["agent-chat", "agent-control"],
        "handler_kind": "portal-page",
        "summary_label": "Lineage workbench",
    },
    {
        "id": "explore",
        "title": "Explore",
        "route": "/explore",
        "entry_port": PORTAL_PORT,
        "entry_path": "/explore",
        "requires_auth": False,
        "write_capabilities": ["experiment-create", "experiment-run", "agent-control"],
        "handler_kind": "portal-page",
        "summary_label": "Experiment exploration tree",
    },
    {
        "id": "paper",
        "title": "Paper",
        "route": "/paper",
        "entry_port": PORTAL_PORT,
        "entry_path": "/paper",
        "requires_auth": False,
        "write_capabilities": ["paper-register", "paper-review", "paper-chat"],
        "handler_kind": "portal-page",
        "summary_label": "Paper research workspace",
    },
    {
        "id": "test",
        "title": "Test",
        "route": "/test",
        "entry_port": PORTAL_PORT,
        "entry_path": "/test",
        "requires_auth": False,
        "write_capabilities": ["test-run", "test-catalog", "test-analysis"],
        "handler_kind": "portal-page",
        "summary_label": "Portal test observatory",
    },
]


def rewrite_shared_path(path: str, *, include_codes: bool = True) -> str:
    raw = str(path or "").strip()
    prefix_keys = ("projects", "codes") if include_codes else ("projects",)
    for key in prefix_keys:
        rewritten = _rewrite_path_prefix(raw, PATH_PREFIXES[key])
        if rewritten is not None:
            return rewritten

    return raw


def get_module_registry() -> list[dict]:
    modules = deepcopy(MODULES)
    port = router_port()
    for module in modules:
        module["entry_port"] = port
    return modules


def get_runtime_config() -> dict:
    modules = get_module_registry()
    return {
        "portal": {
            "title": "TFI Portal",
            "port": router_port(),
        },
        "paths": deepcopy(SHARED_PATHS),
        "path_prefixes": deepcopy(PATH_PREFIXES),
        "modules": modules,
        "routes": {
            module["id"]: module["route"]
            for module in modules
        },
    }


def get_portal_summary() -> dict:
    return {
        "generated_from": "shared-module-registry",
        "cards": [
            {
                "id": "urgent-work",
                "label": "Most urgent project",
                "value": "占位：下一步由统一摘要接口汇总 project_board 和 todo_board 的高优先级项。",
            },
            {
                "id": "contribution-window",
                "label": "Today / This week / This month contribution",
                "value": "占位：后续接入 git、知识新增、项目变更和审批活动的时间窗口摘要。",
            },
            {
                "id": "agent-status",
                "label": "Agent status",
                "value": "占位：后续接入本地 agent、队列与服务健康状态。",
            },
        ],
    }
