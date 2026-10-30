"""Assemble the standalone Claude daemon and its registration-read dependencies."""
from pathlib import Path
import shutil
import sys


MODULES = {
    "management_portal": (
        "claude_code_daemon", "claude_provider_catalog", "claude_input_media", "claude_tool_bridge",
        "claude_account_usage", "claude_runtime", "ai_provider_registry", "model_api_store",
        "provider_transport_diagnostics", "resident_shell_environment", "jev_provider",
    ),
    "management_portal/execution": ("account_registrations", "account_hosting", "model_api_protocols", "provider_failures"),
    "management_portal/hatg": ("contracts",),
    "management_common": (
        "provider_subagent_runs", "domain_database", "portal_database", "postgres_database",
        "instance_paths", "instance_config", "portal_paths", "operation_errors",
        "file_permissions", "format_tags", "product_environment", "site_compatibility",
        "site_config", "portal_network", "file_lock", "local_ipc",
    ),
}


def build_package(source: Path, destination: Path) -> None:
    for package, modules in MODULES.items():
        target = destination / package
        target.mkdir(parents=True, exist_ok=True)
        # Keep Web/Execution service import hooks out of the standalone daemon.
        (target / "__init__.py").write_text('"""Installed Claude adapter runtime."""\n')
        for module in modules:
            shutil.copyfile(source / package / f"{module}.py", target / f"{module}.py")
    units = destination / "systemd"
    units.mkdir(exist_ok=True)
    for name in ("portal-claude-code.env", "portal-claude-code.service"):
        shutil.copyfile(source / "systemd" / name, units / name)
    shutil.copyfile(source / "management_portal/claude_app_server_version.txt", destination / "VERSION")


if __name__ == "__main__":
    build_package(Path(sys.argv[1]), Path(sys.argv[2]))
