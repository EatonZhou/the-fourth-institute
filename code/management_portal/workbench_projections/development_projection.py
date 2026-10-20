"""Generate a commit-pinned Portal application definition; installation uses HATG."""
import re

from management_common.site_config import read_site_config


REGIONS = (
    ("architecture", "架构与协议", "network"),
    ("progress", "阶段进展", "activity"),
    ("evolution", "未来演化", "git-branch"),
    ("packaging", "打包发布", "package"),
)


def _configuration(*, example: bool) -> dict:
    deployment = read_site_config().get("deployment", {})
    if not isinstance(deployment, dict) or not isinstance(deployment.get("development_projection", {}), dict):
        raise ValueError("deployment.development_projection must be an object")
    config = deployment.get("development_projection", {})
    required = {"repo_id": "repo"}
    if config.get("system_lineage_id") is not None:
        required["system_lineage_id"] = "lineage"
    if not example:
        required.update(lineage_id="lineage", facade_resident_id="resident",
                        packaging_lineage_id="lineage", packaging_facade_resident_id="resident")
    for key, kind in required.items():
        value = config.get(key)
        if not isinstance(value, str) or not re.fullmatch(kind + r"_[0-9a-f]{12}", value):
            raise ValueError(f"deployment.development_projection.{key} must be configured with a {kind} ID")
    projects = config.get("project_ids", [])
    if not isinstance(projects, list) or any(not isinstance(p, str) or not p.strip() for p in projects):
        raise ValueError("deployment.development_projection.project_ids must be a list of nonempty strings")
    return config


def _application_shell(sidebar, config, *, example):
    return {
        "title": "Portal 开发投影", "description": "从架构模块理解 Portal，查看阶段进展和未来方向，与开发团队讨论。",
        "html": "", "navigation": "top", "project_ids": list(config.get("project_ids", [])),
        "bindings": {} if example else {"main": {"lineage_id": config["lineage_id"], "residents": {"facade": config["facade_resident_id"]}, "events": {}}},
        "manifest": {"schema": "tfi.projection.v2", "chat": {"binding": "main", "role": "facade"}, "sidebar": sidebar,
                     "context": [{"channel": "focus", "publishers": [item["id"] for item in sidebar], "subscribers": [item["id"] for item in sidebar]}]},
    }


def application_definition(commit: str, *, example: bool = False):
    """Prepare a source reference; saving through HATG builds this exact commit."""
    from .page import validate_application

    config = _configuration(example=example)
    regions = (("example", "D3 与 Worker 平台示例", "activity"),) if example else REGIONS
    sidebar = [{"id": key, "title": title, "icon": icon, "pattern": key, "source": f"package:{key}"}
               for key, title, icon in regions]
    prefix = str(config.get("code_directory") or "").strip("/")
    if prefix and (prefix.startswith(".") or ".." in prefix.split("/")):
        raise ValueError("development_projection.code_directory must be a repository-relative directory")
    directory = "/".join(filter(None, (prefix, "management_portal/workbench_projections/development_app")))
    application = {"repo_id": config["repo_id"], "directory": directory,
                   "commit": commit, "entries": {key: f"{key}.html" for key, _, _ in regions}}
    validate_application(application)
    value = {**_application_shell(sidebar, config, example=example), "application": application, "panels": {}}
    if not example:
        value["bindings"]["packaging"] = {"lineage_id": config["packaging_lineage_id"], "residents": {"facade": config["packaging_facade_resident_id"]}, "events": {}}
        if config.get("system_lineage_id"):
            value["bindings"]["packaging_system"] = {"lineage_id": config["system_lineage_id"], "residents": {}, "events": {}}
        next(item for item in sidebar if item["id"] == "packaging")["chat"] = {"binding": "packaging", "role": "facade"}
    if example:
        value.update(title="投影应用包平台示例", navigation="hidden")
        value["manifest"].pop("chat")
    return value


if __name__ == "__main__":
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True, help="Generate an application definition for this full Git SHA")
    parser.add_argument("--example", action="store_true", help="Only the D3/Worker platform example")
    args = parser.parse_args()
    print(json.dumps(application_definition(args.commit, example=args.example), ensure_ascii=False))
