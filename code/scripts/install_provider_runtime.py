"""Run the Provider preparation owner within a Portal script run."""
from __future__ import annotations

from scripts.portal_operations import script_context


def main() -> int:
    from portal_script import set_output

    run = script_context("install_provider_runtime")
    arguments = run["arguments"]
    provider = arguments.get("provider")
    if set(arguments) != {"provider"} or not isinstance(provider, str) or not provider.strip():
        raise ValueError("参数须包含 Provider 标识 provider")
    from management_portal.execution.provider_updates import prepare_runtime

    preparation = prepare_runtime(provider, background=False)
    set_output({"preparation": preparation})
    return int(preparation.get("status") not in {"ready", "pending", "busy"})


if __name__ == "__main__":
    raise SystemExit(main())
