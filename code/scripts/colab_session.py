"""Start or stop the configured shared Colab session through Portal RunScript."""
from __future__ import annotations

import re

from management_portal.google_cloud_devices import change_colab_session
from scripts.portal_operations import script_context


def main() -> int:
    from portal_script import isa, set_output

    run = script_context("colab_session")
    arguments = run["arguments"]
    device = arguments.get("resource_id")
    running = arguments.get("running")
    required = {"resource_id", "running", "hardware"} if running is True else {"resource_id", "running"}
    if (set(arguments) != required or type(running) is not bool
            or not isinstance(device, str) or not re.fullmatch(r"device_[0-9a-f]{12}", device)):
        raise ValueError("参数须包含设备资源 ID 和运行状态，启动时还须提供 hardware")
    resolved = isa("ResolveResource", address=device)
    if resolved["target"].get("resource_type") != "device" or resolved["resource"].get("resource_id") != device:
        raise ValueError("请选择已登记的 Colab 设备")
    action = "colab.start" if running else "colab.stop"
    payload = {"hardware": arguments["hardware"]} if running else {}
    set_output(change_colab_session(device, action, payload, actor=run["caller_id"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
