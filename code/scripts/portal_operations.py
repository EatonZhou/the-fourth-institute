"""Deployment bindings and authenticated RunScript inputs for Portal operations."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
from typing import Any

from management_common.configuration_files import atomic_json
from management_common.instance_manifest import load
from management_common.principal import actor_principal
from management_common.site_config import read_site_config


OPERATIONS = frozenset({"publish_release", "install_provider_runtime", "colab_session", "article_restore"})
LINEAGE_ID = re.compile(r"lineage_[0-9a-f]{12}")
SCRIPT_RUN_ID = re.compile(r"script_run_[0-9a-f]{24}")


def lineage_id() -> str:
    deployment = read_site_config().get("deployment", {})
    config = deployment.get("portal_operations", {}) if isinstance(deployment, dict) else {}
    identifier = config.get("lineage_id") if isinstance(config, dict) else None
    if not isinstance(identifier, str) or not LINEAGE_ID.fullmatch(identifier):
        raise ValueError("deployment.portal_operations.lineage_id 须配置固定的 Portal 运维 Lineage ID")
    return identifier


def script_path(operation: str) -> str:
    if operation not in OPERATIONS:
        raise ValueError("Unknown Portal operation")
    root = load().code_root().resolve()
    path = root / "scripts" / f"{operation}.py"
    if path.resolve() != path or not path.is_file():
        raise ValueError(f"Portal 运维脚本未安装：{operation}")
    return str(path)


def run_arguments(operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "lineage_id": lineage_id(), "path": script_path(operation), "runtime": "python",
        "arguments": arguments,
    }


def script_context(operation: str) -> dict[str, Any]:
    from portal_script import get_current_run, inputs

    expected_path = script_path(operation)
    owner = lineage_id()
    hints = inputs()
    identifier = hints.get("script_run_id") if isinstance(hints, dict) else None
    if not isinstance(identifier, str) or not SCRIPT_RUN_ID.fullmatch(identifier):
        raise PermissionError("缺少有效的脚本运行 ID")
    expected_input = load().directory("runtime") / "script_runs" / identifier / "io" / "input.json"
    if Path(os.environ.get("PORTAL_SCRIPT_INPUT", "")) != expected_input:
        raise PermissionError("脚本输入文件与运行 ID 不匹配")
    run = get_current_run(lineage_id=owner, script_run_id=identifier)
    if not isinstance(run, dict) or run.get("lineage_id") != owner:
        raise PermissionError("该脚本须在固定的 Portal 运维 Lineage 执行")
    if (run.get("script_run_id") != identifier or run.get("status") != "running"
            or run.get("path") != expected_path or not isinstance(run.get("arguments"), dict)):
        raise PermissionError("脚本运行记录与当前运维入口不匹配")
    caller = run.get("caller_id")
    try:
        principal = actor_principal(caller) if isinstance(caller, str) else None
    except ValueError as exc:
        raise PermissionError("运维脚本要求真实 User 或 Resident 调用者") from exc
    if principal is None or principal.id != caller or principal.kind not in {"user", "resident"}:
        raise PermissionError("运维脚本要求真实 User 或 Resident 调用者")
    return {"caller_id": caller, "lineage_id": owner, "script_run_id": identifier, "arguments": run["arguments"]}


def install_binding(instance, identifier: str) -> None:
    """Bind an existing operations Lineage during installation or cutover."""
    if not isinstance(identifier, str) or not LINEAGE_ID.fullmatch(identifier):
        raise ValueError("须指定已登记的 Portal 运维 Lineage ID")
    site = instance.site_settings()
    deployment = site.setdefault("deployment", {})
    if not isinstance(deployment, dict):
        raise ValueError("deployment 须为配置对象")
    config = deployment.setdefault("portal_operations", {})
    if not isinstance(config, dict):
        raise ValueError("deployment.portal_operations 须为配置对象")
    existing = config.get("lineage_id")
    if existing == identifier:
        return
    if existing:
        raise ValueError("此实例已绑定另一条 Portal 运维 Lineage")
    config["lineage_id"] = identifier
    atomic_json(instance.root / "site.json", site)


def main() -> None:
    parser = argparse.ArgumentParser(description="Install the selected site's Portal operations Lineage binding.")
    parser.add_argument("--lineage-id", required=True, help="Existing Portal operations Lineage ID")
    args = parser.parse_args()
    install_binding(load(), args.lineage_id)


if __name__ == "__main__":
    main()
