"""Publish the selected instance's development tree and record the release receipt."""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import subprocess
import sys

from management_common.instance_manifest import load
from scripts.portal_operations import script_context


def publish_options(arguments: dict) -> list[str]:
    message = arguments.get("message")
    if not isinstance(message, str) or not message.strip() or "\0" in message:
        raise ValueError("发布参数 message 必须是非空字符串")
    options = [f"--message={message}"]
    if set(arguments) == {"message", "all"} and arguments["all"] is True:
        return [*options, "--all"]
    paths = arguments.get("paths")
    if (set(arguments) - {"message", "paths", "all"} or arguments.get("all", False) is not False
            or not isinstance(paths, list) or not paths):
        raise ValueError("发布参数须指定非空 paths 或 all: true")
    for name in paths:
        if not isinstance(name, str) or "\\" in name or "\0" in name:
            raise ValueError("请选择相对 code/ 的路径")
        path = PurePosixPath(name)
        if not path.parts or path.is_absolute() or PureWindowsPath(name).drive or ".." in path.parts:
            raise ValueError("请选择相对 code/ 的路径")
        options.append(f"--path=code/{path.as_posix()}")
    return options


def main() -> int:
    from portal_script import set_output

    run = script_context("publish_release")
    options = publish_options(run["arguments"])
    instance = load()
    if "dev" not in instance.deployments:
        raise ValueError("本站未配置 Dev 到 Release 发布")
    code = instance.code_root("dev")
    command = [sys.executable, str(code / "scripts/tfi_dev.py"), "publish", *options]
    result = subprocess.run(command, cwd=code.parent, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    try:
        report = json.loads(result.stdout)
    except ValueError:
        report = None
    if not isinstance(report, dict) or report.get("status") not in {"published", "failed"}:
        set_output({"status": "failed", "error": "发布器没有返回可读取的回执",
                    "commit": "", "paths": [], "health": {"status": "unknown"}, "push": {"ok": None}})
        return 1
    output = {
        "status": report["status"], "commit": report.get("commit") or "", "paths": report.get("paths") or [],
        "health": report.get("health") or {"status": "not_queued"}, "push": report.get("push") or {},
        **{field: report[field] for field in ("stage", "error", "release_advanced") if field in report},
    }
    receipt = json.dumps(output, ensure_ascii=False).encode()
    if len(receipt) > 65536:
        name = "publication.json"
        (Path(os.environ["PORTAL_SCRIPT_ARTIFACTS"]) / name).write_bytes(receipt)
        output.update(paths=[], paths_count=len(output["paths"]), paths_artifact=name)
    set_output(output)
    return int(result.returncode != 0 or report["status"] != "published")


if __name__ == "__main__":
    raise SystemExit(main())
